"""Listening, so Notes itself is the interface.

Two ways to reach Notron, both of them just typing in the Notes app:

  * write in `📥 Ask Notron`, anywhere in the note; or
  * write `#notron` in any note you own — the book idea, the meeting note, the
    half-finished plan — and ask about that thing, in that place.

Either way she answers directly underneath what you wrote, and draws no more
attention to herself than that.

A third surface is quieter still: lines thrown into `🧠 Brain Dump` are filed
into the right notes once the dump has been left alone for a while.

Three things this has to get right, all learned the hard way:

  * **Answering mid-sentence.** She waits for your typing to go quiet first.
  * **Answering herself.** Her replies are signed, and signed turns are never
    read as questions.
  * **Blocking Notes.** Apple Notes serves one script request at a time, and a
    slow one wedges the app for everybody — including Notron. So there is exactly
    one loop, it never runs two requests at once, the Ask note is checked often
    because it is one cheap read, and the full sweep for `#notron` runs on a much
    longer cycle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import attachments, conversation, filer, graph, mentions, notes, workspace, policy, requests
from .applescript import AppleScriptError, NotesBusy
from .markup import to_text

ASK_POLL = 5           # seconds between checks of the Ask note
SWEEP_EVERY = 20       # seconds between sweeps for #notron mentions (a survey is ~1s)
SETTLE = 12            # how long your typing must be still before she answers
MIN_CHARS = 2

# A typed turn that ends in a question mark is finished far more often than not;
# the 12 s above is for a thought still being written. Measured 2026-09-23: the
# 12 s wait was the largest single slice of a typed Ask-note answer after the
# graph itself. A question answered a moment early costs one more line.
QUESTION_SETTLE = 4

# A project channel line usually arrives whole — Siri dictates it in one go —
# so it waits 3 s, not the 12 s a typed Ask-note turn needs. Measured
# 2026-09-23: poll + settle were 22 of a ~110 s channel reply. A half-typed line
# answered early costs one more line; a Siri line waiting 12 s costs every time.
CHANNEL_POLL = 3       # seconds between looks at project channels (one read each)
CHANNEL_SETTLE = 3
# The Reminders inbox: one EventKit read (~0.1 s), so it can be looked at often.
# A reminder arrives whole; there is nothing to let settle.
INBOX_POLL = 5
MIN_PAUSE = 0.25      # never spin: a due line is looked at again this soon at most
DUMP_POLL = 60         # seconds between looks at the Brain Dump (one cheap read)

HERE_CHARS = 40_000    # how much of the note she was tagged in the model sees
ELIDED = "\n\n[… a part of this note is not shown …]\n\n"


def here_text(body_html: str, anchor: str = "", *, budget: int = HERE_CHARS) -> str:
    """The note she was tagged in, as the model should see it.

    Whole, whenever it fits — and it almost always does. Some cap is
    unavoidable (a note may run to 200,000 characters), but a cap that simply
    keeps the opening is worse than it looks: a question like "would my first
    scene idea, written at the end of this note, work?" then points at text she
    was never given, and she answers, reasonably and wrongly, that she cannot
    find it. That happened on a 25,000-character story bible against the old
    4,000-character cut.

    So an oversized note is sent as three parts — how it opens, the passage
    around the line she was tagged in, and how it ends — with the gaps marked
    so she can say what she has not read rather than assume it is not there.
    Parts that turn out to touch are joined back into one, so the marker only
    ever appears where something really was left out.
    """
    text = to_text(body_html)
    if len(text) <= budget:
        return text

    third = budget // 3
    spans = [(0, third), (len(text) - third, len(text))]
    at = text.find(anchor.strip()) if anchor.strip() else -1
    if at >= 0:
        window = budget - 2 * third
        start = max(0, min(at - window // 2, len(text) - window))
        spans.append((start, start + window))

    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return ELIDED.join(text[start:end] for start, end in merged)

# How long the dump must be untouched before she files it. A dump session is a
# burst of half-thoughts; filing on a timer would file the half. Fifteen quiet
# minutes means the session is over.
DUMP_SETTLE = 900

# The Ask note's own standing text, which nobody said out loud.
ASK_FURNITURE = (workspace.ASK, "Type anything below this line", conversation.QA_RULE)


@dataclass
class Watcher:
    brain: object
    ask_poll: float = ASK_POLL
    sweep_every: float = SWEEP_EVERY
    settle: float = SETTLE
    dump_poll: float = DUMP_POLL
    dump_settle: float = DUMP_SETTLE
    channel_poll: float = CHANNEL_POLL
    channel_settle: float = CHANNEL_SETTLE
    inbox_poll: float = INBOX_POLL
    on_event: object = None
    scanner: mentions.Scanner = field(default_factory=mentions.Scanner)

    _pending: dict = field(default_factory=dict)   # key -> (text, first seen at)
    _failures: dict = field(default_factory=dict)  # key -> (count, last attempt at)
    _ask_id: str | None = None
    _dump_id: str | None = None
    _runtime_ready: bool = False
    _last_sweep: float = 0
    _last_dump: float = 0
    _last_channels: float = 0
    _last_tasks: float = 0
    _last_inbox: float = 0
    _inbox_problem: str = ""
    _dump_on_hold: bool = False
    # Set on a resume until one full tick has passed: a stamp from before the
    # sleep says nothing about whether a line was finished.
    _just_resumed: bool = False
    _intent_version: int = -1

    # A question that produced no write stays "unanswered" in the note, so the
    # next poll would send it to the model again, and again, forever — a quiet
    # API bill for one stuck message. Two honest tries, then a long pause.
    #
    # A pause is the right answer for a note that was busy or too big, because
    # either may be different in half an hour. It is the wrong answer for a
    # note holding a picture: that refusal is identical every time, and the
    # pause turns it into a model call every thirty minutes for the life of
    # the note. Those are set aside for good instead — she has already said
    # her piece in 📥 Ask Notron.
    MAX_TRIES = 2
    COOLDOWN = 1800

    # ---------------------------------------------------------------- output

    def _say(self, msg: str) -> None:
        if self.on_event:
            self.on_event(msg)

    # --------------------------------------------------------------- answering

    def _answer(self, question: str, *, title: str, folder: str, after: int,
                here: str = "", source: str = "", note_id: str | None = None, modified: str = "",
                envelope: requests.RequestEnvelope | None = None) -> bool:
        self._say(f"\n> [{title}] {question}")
        if note_id is None:
            raise policy.PolicyError('Reply requires the observed note ID.')
        with policy.explicit_reply(note_id):
            if envelope is None:
                raise ValueError('A persisted source occurrence is required.')
            state = graph.run_request(envelope, brain=self.brain,
                                      carried=self._carried(note_id, envelope.source_modified))
        for line in state.trace:
            self._say(f"  {line}")
        for result in state.results:
            self._say(f"  {result}")
        wrote = state.receipt_complete
        if not wrote:
            self._say("  nothing was written — she will read this again")
        self._say(f"\n{state.answer}\n")
        return wrote

    def _carried(self, note_id: str, modified: str = "") -> list:
        """The files hanging off this note, for the prompt to be honest about.

        Asked here rather than in the poll: a note's attachments cost a 0.4s
        request to Notes, and the Ask note is read every five seconds. Only a
        note actually about to be answered pays for it.
        """
        try:
            return attachments.on_note(note_id, modified)
        except Exception as e:
            # Not knowing what a note carries is worth less than not answering
            # at all. She simply says nothing about files, as she did before.
            self._say(f"  (couldn't check for attached files: {type(e).__name__})")
            return []

    def _worth_trying(self, key: str) -> bool:
        from .health import HealthStore
        from hashlib import sha256
        with HealthStore().connection() as db:
            row = db.execute('SELECT failures,attempted_at FROM cooldowns WHERE key_hash=?',
                             (sha256(key.encode()).hexdigest(),)).fetchone()
        count, at = self._failures.get(key, tuple(row) if row else (0, 0.0))
        return count < self.MAX_TRIES or time.time() - at >= self.COOLDOWN

    def _attempted(self, key: str, wrote: bool) -> None:
        from .health import HealthStore
        from hashlib import sha256
        digest = sha256(key.encode()).hexdigest()
        store = HealthStore()
        with store.connection() as db:
            if wrote:
                db.execute('DELETE FROM cooldowns WHERE key_hash=?', (digest,))
            else:
                db.execute('INSERT INTO cooldowns VALUES(?,1,?) ON CONFLICT(key_hash) DO UPDATE SET '
                           'failures=failures+1,attempted_at=excluded.attempted_at', (digest, time.time()))
            # Retry metadata has no content or authority; keep a bounded recent window.
            db.execute('DELETE FROM cooldowns WHERE key_hash NOT IN '
                       '(SELECT key_hash FROM cooldowns ORDER BY attempted_at DESC LIMIT 1000)')
        if wrote:
            self._failures.pop(key, None)
            store.success()
        else:
            count, _ = self._failures.get(key, (0, 0.0))
            self._failures[key] = (count + 1, time.time())
            if count + 1 >= self.MAX_TRIES:
                self._say(f"  giving [{key}] a rest — trying again in {self.COOLDOWN // 60} min")

    def _settled(self, key: str, text: str, *, settle: float | None = None,
                 quiet=None) -> bool:
        """True once this exact text has sat unchanged long enough to be finished.

        `quiet`, if given, is asked on first sight how long the note has been
        still by Notes' own clock. A line that is already older than the settle
        window is finished now: measured 2026-09-23, a dictated channel line
        otherwise waited a whole second look, ~6 s, after arriving whole.
        """
        wait = self.settle if settle is None else settle
        was = self._pending.get(key)
        if was is None or was[0] != text:
            self._pending[key] = (text, time.time())
            self._say(f"  saw [{key}]: {text[:60]}")
            still = quiet() if quiet is not None else None
            return still is not None and still >= wait
        return time.time() - was[1] >= wait

    def _quiet_for(self, note_id: str):
        """How long a note has gone unchanged, for `_settled`; None if unknown.

        Asked after its body was read, so an edit in between only makes the
        note look busier. Notes stamps whole seconds, so one is taken off; a
        clock that disagrees (a future stamp) reads as not quiet at all.
        """
        from datetime import datetime
        try:
            at = notes.modified_at(note_id)
        except NotesBusy:
            raise
        except Exception:
            return None
        if at is None:
            return None
        still = (datetime.now() - at).total_seconds() - 1
        return still if still >= 0 else None

    def _ask_settle(self, text: str) -> float:
        return min(self.settle, QUESTION_SETTLE) if text.rstrip().endswith("?") else self.settle

    def next_wake(self, now: float) -> float | None:
        """When the earliest line now settling will have sat long enough."""
        due = [at + (min(self.settle, self.channel_settle) if key.startswith("chan:")
                     else self._ask_settle(text))
               for key, (text, at) in self._pending.items()
               if key.startswith(("chan:", "ask:"))]
        # Only deadlines still ahead. A key goes stale when its text is edited
        # (a new request id) and stays in `_pending` until an answer; counted
        # as overdue it kept the loop at MIN_PAUSE — a spin (review 2026-09-23).
        due = [d for d in due if d > now]
        return min(due) if due else None

    # ------------------------------------------------------------- the two jobs

    def check_ask(self) -> None:
        policy.require_ready()
        from . import retention
        retention.require_ready()
        # A note's id is stable, so look it up once rather than listing the
        # folder every few seconds — each listing is a request Notes must serve.
        if self._ask_id is None:
            note = notes.find_note(workspace.FOLDER, workspace.ASK)
            if not note:
                return
            self._ask_id = note.id
        if policy.current().system_notes.get(workspace.ASK) != self._ask_id or not policy.current().can_read(self._ask_id):
            raise policy.PolicyError('Ask note requires setup or permission recovery.')
        try:
            body = notes.read_body(self._ask_id)
        except NotesBusy:
            raise
        except AppleScriptError:
            # The id we cached no longer resolves — almost always because the
            # note was deleted. Forget it so the next poll looks it up by
            # title again; that's what lets a recreated note be found instead
            # of failing forever on the same dead id.
            self._ask_id = None
            self._say(f"  {workspace.ASK} not found — will look again")
            return
        asks = [q for q in conversation.unanswered(body, ignore=ASK_FURNITURE) if len(q.text) >= MIN_CHARS]
        store = requests.current()
        envelopes = store.observe(self._ask_id, body, asks, source='ask',
                                  title=workspace.ASK, folder=workspace.FOLDER,
                                  legacy_review=self._ask_id in self.scanner.review)
        if self._ask_id in self.scanner.review:
            self.scanner.review.discard(self._ask_id)
            self.scanner._save()
        for q, envelope in zip(asks, envelopes):
            record = store.get(envelope.request_id)
            from . import recovery
            if record.status != 'prepared' and not recovery.available(record):
                if record.status in {'running', 'needs_review'}:
                    self._say('  request needs review before it can run again')
                continue
            key = f"ask:{envelope.request_id}"
            if not self._worth_trying(key):
                continue
            # No `quiet` here: a line half-typed before the lid closed, or typed
            # on a phone and synced late, carries an old stamp while the person
            # is still writing (review 2026-09-23). Only whole Siri lines use it.
            if not self._settled(key, q.text, settle=self._ask_settle(q.text)):
                continue
            wrote = self._answer(q.text, title=workspace.ASK, folder=workspace.FOLDER,
                                 after=q.after, source=q.text, note_id=self._ask_id, envelope=envelope)
            self._attempted(key, wrote)
            # Only the Ask note moved underneath us; a tag elsewhere is still
            # settling on its own clock and keeps its timer.
            for stale in [k for k in self._pending if k.startswith("ask:")]:
                self._pending.pop(stale, None)
            return              # one at a time; the note has moved underneath us

    def check_channels(self) -> bool:
        """Answer one settled line in a project channel. True if she answered.

        A channel reads exactly like 📥 Ask Notron — no tag, every new run of
        lines is a request — because a line Siri dictated into it carries no
        tag and must still be heard. What it may touch is the registry's
        business (`channels.py`), never the note's.
        """
        from . import channels
        policy.require_ready()
        from . import retention
        retention.require_ready()
        snap = policy.current()
        for ch in channels.load():
            if ch.note_id not in snap.channels or not snap.can_read(ch.note_id):
                continue
            try:
                body = notes.read_body(ch.note_id)
            except NotesBusy:
                raise
            except AppleScriptError:
                self._say(f"  {ch.title} not found — remove it with `notron channel remove {ch.name}`")
                continue
            asks = [q for q in conversation.unanswered(body, ignore=(ch.title, *ASK_FURNITURE))
                    if len(q.text) >= MIN_CHARS]
            store = requests.current()
            envelopes = store.observe(ch.note_id, body, asks, source='ask',
                                      title=ch.title, folder=workspace.FOLDER)
            for q, envelope in zip(asks, envelopes):
                record = store.get(envelope.request_id)
                from . import recovery
                if record.status != 'prepared' and not recovery.available(record):
                    if record.status in {'running', 'needs_review'}:
                        self._say('  request needs review before it can run again')
                    continue
                key = f"chan:{envelope.request_id}"
                if not self._worth_trying(key):
                    continue
                quiet = None if self._just_resumed else (lambda nid=ch.note_id: self._quiet_for(nid))
                if not self._settled(key, q.text, settle=min(self.settle, self.channel_settle),
                                     quiet=quiet):
                    continue
                wrote = self._answer(q.text, title=ch.title, folder=workspace.FOLDER,
                                     after=q.after, source=q.text, note_id=ch.note_id, envelope=envelope)
                self._attempted(key, wrote)
                for stale in [k for k in self._pending if k.startswith("chan:")]:
                    self._pending.pop(stale, None)
                return True
        return False

    def check_inbox(self) -> bool:
        """Answer one request from the Notron list in Reminders. True if she wrote.

        The request runs through the graph like a line typed into the note it
        belongs in — Nemotron picks that note (`inbox.route`), then decides what
        the request is in the `project` node — and the reply is appended there.
        The reminder is ticked once the reply has landed, never before.
        """
        from . import eventkit, handoff, inbox, recovery
        try:
            items = inbox.waiting()
            problem = ""
        except eventkit.EventKitError as exc:
            items, problem = [], f"can't read Reminders ({exc})"
        if problem != self._inbox_problem:
            # Said once when it starts and once when it clears, never per poll.
            self._inbox_problem = problem
            self._say(f"  Reminders inbox: {problem or 'reading the Notron list again'}")
        for r in items:
            request_id = f"reminder:{r.id}"
            key = f"rem:{r.id}"
            if not self._worth_trying(key):
                continue
            record = requests.current().get(request_id)
            if record is not None and record.status == 'completed':
                # Answered before a crash stopped the tick: tick it, never re-answer.
                self._take(r)
                continue
            if record is not None and record.status != 'prepared' and not recovery.available(record):
                continue
            ch, why = inbox.remembered_route(r.id, r.title, brain=self.brain)
            if ch is None:
                if self._inbox_problem != "no Tasks note":
                    self._inbox_problem = "no Tasks note"
                    self._say("  Reminders inbox: no Tasks note yet — run `notron tasks setup`")
                return False
            self._say(f"\n> [Reminders] {r.title[:60]} → {ch.title}" + (f" ({why})" if why else ""))
            with policy.explicit_reply(ch.note_id):
                state = graph.run(r.title, brain=self.brain, trigger="reminder",
                                  source_note_id=ch.note_id, reply_to=(ch.title, workspace.FOLDER, 0),
                                  request_id=request_id)
            for line in state.trace:
                self._say(f"  {line}")
            if state.receipt_complete:
                self._take(r)
            self._attempted(key, state.receipt_complete)
            return True
        return False

    def _take(self, r) -> None:
        from . import eventkit, inbox
        try:
            inbox.take(r.id)
        except (LookupError, eventkit.EventKitError) as exc:
            self._say(f"  could not tick “{r.title[:40]}” in Reminders: {exc}")

    def tend_reminders(self) -> None:
        """Approve reminders out, ticked approvals in. Never stops the tasks loop."""
        from . import eventkit, handoff, inbox
        try:
            for task in handoff.all_tasks():
                if task.status == "proposed" and task.source_reminder and not task.approve_reminder:
                    inbox.ask_approval(task)
                    self._say(f"  asked for approval in Reminders: {task.goal[:60]}")
            for task in inbox.sync_approvals():
                self._say(f"  approved in Reminders: {task.goal[:60]}")
        except (LookupError, eventkit.EventKitError, inbox.InboxError, handoff.TaskError) as exc:
            self._say(f"  Reminders approvals: {exc}")

    def check_tasks(self) -> bool:
        """Move hand-off work along: collect, start one, report one. True if she wrote.

        The coding agent runs in its own process and never holds the Notes lock
        or this loop: a run of minutes costs the listener one cheap look per
        pass. Only the report is a Notes write, and it goes through the graph —
        Nemotron's review, the Guard, the executor — like any other reply.
        """
        from . import channels, handoff
        self.tend_reminders()
        for task in handoff.poll():
            self._say(f"  task {task.id[:8]} {task.status} — {len(task.changed)} files changed")
        started = handoff.dispatch_next()
        if started is not None:
            self._say(f"  task {started.id[:8]} {started.status}: {started.hand_name} — {started.goal[:60]}")
        task = handoff.next_report()
        if task is None:
            return False
        ch = next((c for c in channels.load() if c.name == task.channel and c.note_id == task.note_id), None)
        if ch is None or ch.note_id not in policy.current().channels:
            # The channel is gone; its note is not hers to write in any more.
            handoff.reported(task.id)
            return False
        key = f"task:{task.id}"
        if not self._worth_trying(key):
            return False
        record = requests.current().get(f"task-report:{task.id}")
        if record is not None and record.status == 'completed':
            # Posted before a crash stopped it being marked: never post it twice.
            handoff.reported(task.id)
            return False
        with policy.explicit_reply(ch.note_id):
            state = graph.run(f"task-report {task.id}", brain=self.brain, trigger="task",
                              source_note_id=ch.note_id, reply_to=(ch.title, workspace.FOLDER, 0),
                              request_id=f"task-report:{task.id}")
        for line in state.trace:
            self._say(f"  {line}")
        if state.receipt_complete:
            handoff.reported(task.id)
            if task.source_reminder:
                from . import eventkit, inbox
                try:
                    inbox.say_done(task)
                except (eventkit.EventKitError, inbox.InboxError) as exc:
                    self._say(f"  could not add the Done reminder: {exc}")
        self._attempted(key, state.receipt_complete)
        return True

    def sweep_mentions(self) -> None:
        from . import retention
        live = retention.reconcile()
        if self._ask_id not in live:
            self._ask_id = None
        if self._dump_id not in live:
            self._dump_id = None
        def retain(key):
            if key.startswith('tag:'):
                record = requests.current().get(key[4:])
                return record is not None and record.envelope is not None and record.envelope.note_id in live
            if key.startswith('ask:'): return self._ask_id is not None
            if key.startswith('chan:'):
                record = requests.current().get(key[5:])
                return record is not None and record.envelope is not None and record.envelope.note_id in live
            if key == 'dump': return self._dump_id is not None
            return False
        self._pending = {key: value for key, value in self._pending.items() if retain(key)}
        self._failures = {key: value for key, value in self._failures.items() if retain(key)}
        if hasattr(self.scanner, 'seen'):
            self.scanner.seen = {nid: stamp for nid, stamp in self.scanner.seen.items() if nid in live}
            self.scanner.pending.intersection_update(live)
        found = self.scanner.scan()
        if found:
            self._say(f"  {len(found)} note(s) mention me")
        for m in found:
            if m.envelope is None:
                continue
            record = requests.current().get(m.envelope.request_id)
            from . import recovery
            if record.status != 'prepared' and not recovery.available(record):
                if record.status in {'running', 'needs_review'}:
                    self._say('  request needs review before it can run again')
                continue
            key = f"tag:{m.envelope.request_id}"
            if not self._worth_trying(key):
                continue
            if not self._settled(key, m.question):
                continue
            if not policy.current().readable(notes.Note(m.note_id, m.title, m.folder, m.modified)):
                continue
            wrote = self._answer(m.question, title=m.title, folder=m.folder, after=m.after,
                                 source=m.raw, note_id=m.note_id, modified=m.modified, envelope=m.envelope)
            self._attempted(key, wrote)
            self._pending.pop(key, None)
            return

    def check_dump(self) -> None:
        """File the Brain Dump once it has gone quiet.

        Not on a timer: a timer files half a thought. The dump is filed when
        the set of unfiled lines has not changed for `dump_settle` seconds —
        the session is over. Lines she has already judged (proposals waiting
        on a yes) do not count, so a dump that is only waiting on the user
        never wakes the model.
        """
        policy.require_ready()
        from . import retention
        retention.require_ready()
        if self._dump_id is None:
            note = notes.find_note(workspace.FOLDER, workspace.DUMP)
            if not note:
                return
            self._dump_id = note.id
        if policy.current().system_notes.get(workspace.DUMP) != self._dump_id or not policy.current().can_read(self._dump_id):
            raise policy.PolicyError('Brain Dump requires setup or permission recovery.')
        try:
            body = notes.read_body(self._dump_id)
        except NotesBusy:
            raise
        except AppleScriptError:
            # Same stale-id story as check_ask: the cached id died, most
            # likely a deletion. Drop it and re-resolve by title next time.
            self._dump_id = None
            self._say(f"  {workspace.DUMP} not found — will look again")
            return
        items = filer.unfiled(body)
        fingerprint = "\n".join(it.anchor for it in items)
        store = requests.current()
        envelopes = store.observe(self._dump_id, body,
                                  [conversation.Question(fingerprint, items[-1].near)] if items else [],
                                  source='ask', title=workspace.DUMP, folder=workspace.FOLDER)
        if not filer.worth_a_pass(body) or not envelopes:
            self._pending.pop("dump", None)
            return
        envelope = envelopes[0]
        from . import recovery
        record = store.get(envelope.request_id)
        if record.status != 'prepared' and not recovery.available(record):
            self._say('  filing batch already completed or needs review')
            return
        if not self._settled("dump", fingerprint, settle=self.dump_settle):
            return
        self._say(f"\n> [{workspace.DUMP}] quiet for {int(self.dump_settle // 60)} min — filing")
        from .brain import batch_deadline
        with batch_deadline():
            outcome = requests.run_job(envelope, lambda: filer.run(self.brain, on_step=lambda m: self._say(f"  filer: {m}")))
        if outcome.result is None:
            self._say(outcome.message)
            return
        out = outcome.result
        for result in out.results:
            self._say(f"  {result}")
        self._say(f"\n{out.summary()}\n")
        if outcome.status == 'completed':
            from .health import HealthStore
            HealthStore().success()
        self._pending.pop("dump", None)

    def recover_pending(self, *, local_only=False) -> bool:
        """Repair one persisted request, including one hidden by its own receipt."""
        from contextlib import nullcontext
        from . import recovery, operations
        for record in requests.current().pending():
            if not recovery.available(record):
                continue
            envelope = record.envelope
            if local_only:
                checkpoint=recovery.get(envelope.request_id+':checkpoint:writer')
                actions=checkpoint.get('actions',[]) if checkpoint else []
                if not checkpoint or checkpoint.get('intent') not in ('remind','schedule') or len(actions)!=1:
                    continue
                primary=operations.current().get(actions[0].get('operation_id',''))
                if not primary or primary.status not in (operations.S.APPLIED,operations.S.RECEIPTED):
                    continue
            key = 'recover:' + envelope.request_id
            if not self._worth_trying(key):
                continue
            if envelope.note_id:
                note = notes.get_note(envelope.note_id)
                if not note or not policy.current().readable(note):
                    continue
            graph_plan = any(op.request_id == envelope.request_id and ':checkpoint:' in op.operation_id
                             for op in operations.current().pending())
            # This is the watcher's scoped delivery authority for an already
            # admitted Ask/tag occurrence, never a capability in cached model data.
            scope = (policy.explicit_reply(envelope.note_id)
                     if envelope.note_id and envelope.source in {'ask', 'mention'} else nullcontext())
            with scope:
                if graph_plan:
                    state = graph.run_request(envelope, brain=self.brain)
                    complete = state.receipt_complete
                else:
                    from .brain import batch_deadline
                    with batch_deadline():
                        result = requests.run_job(envelope, lambda: filer.run(self.brain))
                    complete = result.status == 'completed'
            self._attempted(key, complete)
            return True
        return False

    # ------------------------------------------------------------------- loop

    def _report_blind_spots(self, checker=None) -> None:
        """Report the listener process's own grants, which differ from a terminal."""
        from . import permissions
        try:
            missing = permissions.blind(checker=checker)
        except Exception as exc:
            self._say(f"  (couldn't check permissions: {type(exc).__name__})")
            return
        for check in missing:
            self._say(f"  ⚠️  {check.app} {check.detail} — she will say so rather than "
                      "plan around it." + (f" Fix: {check.fix}" if check.fix else ""))

    def prepare_runtime(self) -> bool:
        from . import worker
        from .health import HealthStore
        try:
            from . import credentials
            if credentials._provider is None:
                credentials.startup()
            from .worker_migration import migrate_filer
            migrate_filer()
            brain = worker.probe()
            if self.brain is None:
                self.brain = brain
            self._report_blind_spots()
            if not self.scanner.primed:
                self.scanner.prime()
                self.scanner.primed = True
            worker.Queue().recover_interrupted()
            HealthStore().update(state='ready', reason_code=None)
            self._runtime_ready = True
            return True
        except Exception as exc:
            self._runtime_ready = False
            worker.failure(exc)
            return False

    def tick(self, *, resumed=False):
        from . import worker, audit
        from .health import HealthStore
        worker.require_owner()
        store = HealthStore()
        intent = store.row()
        if intent['stop_requested']:
            return
        if intent['paused']:
            self._runtime_ready = False
            store.update(state='paused', reason_code='user_paused')
            return
        if resumed or self._intent_version != intent['intent_version']:
            self._runtime_ready = False
            self._pending.clear()  # Sleep/pause never makes a half-typed turn settled.
            self._just_resumed = True
            self._intent_version = intent['intent_version']
            store.update(state='starting', reason_code='resuming')
        # Local verified receipt repair does not depend on cloud entitlement or
        # connectivity. Its executor still validates durable identity and policy.
        try:
            from . import credentials
            if credentials._provider is not None:
                # Only while the runtime is down: when it is up, receipts wait
                # until after the new-line checks below. Measured 2026-09-23:
                # a 📊 Log receipt costs ~9 s of Notes, and five of them here
                # ran before she looked for a new line at all.
                if not self._runtime_ready:
                    audit.drain(limit=1)
                if self.recover_pending(local_only=True):return
        except Exception:
            pass
        if not self._runtime_ready and not self.prepare_runtime():
            return
        try:
            from .managed_lease import require_effect
            require_effect()
            # Recovery wins over fresh jobs after every restart/resume.
            if self.recover_pending():
                return
            if store.paused or store.row()['stop_requested']:
                return
            if worker.drain_one():
                return
            self.check_ask()
            if store.paused or store.row()['stop_requested']:
                return
            now = time.time()
            if now - self._last_channels >= self.channel_poll:
                self._last_channels = now
                if self.check_channels():
                    return
            if now - self._last_inbox >= self.inbox_poll:
                self._last_inbox = now
                if self.check_inbox():
                    return
            if now - self._last_tasks >= self.channel_poll:
                self._last_tasks = now
                if self.check_tasks():
                    return
            if store.paused or store.row()['stop_requested']:
                return
            if now - self._last_sweep >= self.sweep_every:
                self._last_sweep = now
                self.sweep_mentions()
            if store.paused or store.row()['stop_requested']:
                return
            if not store.paused and not self._dump_on_hold and now - self._last_dump >= self.dump_poll:
                self._last_dump = now
                from .worker_migration import FilingReviewRequired
                try:
                    self.check_dump()
                except FilingReviewRequired:
                    # Measured 2026-09-23: this one Brain Dump state raised on
                    # every tick, and each raise reset the whole runtime, so the
                    # listener spent its life restarting and a project channel
                    # line waited more than seven minutes unanswered. Filing is
                    # one optional surface; it waits for review on its own.
                    self._dump_on_hold = True
                    self._say("  Brain Dump filing is on hold until its old outcomes are reviewed")
            # One receipt a tick, and none while a line is settling: receipts
            # are best effort (Invariant 4); a person waiting is not.
            if (not store.paused and not store.row()['stop_requested']
                    and self.next_wake(time.time()) is None):
                audit.drain(limit=1)
            self._just_resumed = False
            store.update(state='ready', reason_code=None)
        except Exception as exc:
            self._runtime_ready = False
            worker.failure(exc)
            self._say(f"  (paused: {type(exc).__name__}) — retrying")

    def pause(self, now: float) -> float:
        """How long to sleep after a tick: the usual poll, or less when a line
        seen settling becomes due sooner. Measured 2026-09-23: a channel line
        seen on one tick was answered only on the tick after next, ~6.4 s
        later, though it was due 3 s after first sight."""
        due = self.next_wake(now)
        if due is None:
            return self.ask_poll
        return min(self.ask_poll, max(MIN_PAUSE, due - now))

    def run_forever(self) -> None:
        from .health import HealthStore, Heartbeat, WorkerLock, STALE_AFTER
        store = HealthStore()
        with WorkerLock() as lock:
            if not lock.acquired:
                self._say('A worker already owns the queue.')
                return
            with Heartbeat(store) as heartbeat:
                previous = (time.time(), time.monotonic())
                self._last_sweep = self._last_dump = previous[0]
                while not heartbeat.failed.is_set() and not store.row()['stop_requested']:
                    now = (time.time(), time.monotonic())
                    self.tick(resumed=slept(previous, now, STALE_AFTER))
                    previous = now
                    time.sleep(self.pause(time.time()))
            from .transport import configured
            managed=configured()
            if managed is not None:managed.stop()


def slept(previous: tuple[float, float], now: tuple[float, float], threshold: float) -> bool:
    """Did the Mac sleep (or the clock jump) between two (wall, monotonic) readings?

    Not "did the last tick take long". That was the old test, and a busy tick is
    not a sleep: measured 2026-09-23, ticks of 30–74 s (Notes is ~1 s a lookup)
    were each read as a wake-up, which reset the runtime and cleared the settle
    timers — so no line ever settled and the background listener answered
    nothing for over seven minutes while the same code answered in the
    foreground. macOS's monotonic clock stops while asleep and the wall clock
    does not, so the gap between their advances is the time spent asleep.
    """
    wall, mono = now[0] - previous[0], now[1] - previous[1]
    return wall < 0 or wall - mono > threshold


WATCH_LABEL = "io.m1labs.notron.listen"


def is_running(runner=None) -> bool:
    """True if the listener's launchd job is loaded — not whether it's healthy,
    just whether `notron listen --install` (or a reboot) has it running."""
    import os, subprocess
    runner = runner or (lambda: subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{WATCH_LABEL}"],
        capture_output=True).returncode)
    return runner() == 0


def plist(python: str, project: str) -> str:
    import plistlib

    return plistlib.dumps({
        "Label": WATCH_LABEL,
        "ProgramArguments": [python, "-m", "notron", "listen"],
        "WorkingDirectory": project,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }).decode("utf-8")
