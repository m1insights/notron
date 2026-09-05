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

from . import conversation, filer, graph, mentions, notes, workspace, policy, requests
from .applescript import AppleScriptError, NotesBusy

ASK_POLL = 5           # seconds between checks of the Ask note
SWEEP_EVERY = 20       # seconds between sweeps for #notron mentions (a survey is ~1s)
SETTLE = 6             # how long your typing must be still before she answers
MIN_CHARS = 2

DUMP_POLL = 60         # seconds between looks at the Brain Dump (one cheap read)
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
    on_event: object = None
    scanner: mentions.Scanner = field(default_factory=mentions.Scanner)

    _pending: dict = field(default_factory=dict)   # key -> (text, first seen at)
    _failures: dict = field(default_factory=dict)  # key -> (count, last attempt at)
    _ask_id: str | None = None
    _dump_id: str | None = None

    # A question that produced no write stays "unanswered" in the note, so the
    # next poll would send it to the model again, and again, forever — a quiet
    # API bill for one stuck message. Two honest tries, then a long pause.
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
            state = graph.run_request(envelope, brain=self.brain)
        for line in state.trace:
            self._say(f"  {line}")
        for result in state.results:
            self._say(f"  {result}")
        wrote = any(r.startswith("✓") for r in state.results)
        if not wrote:
            self._say("  nothing was written — she will read this again")
        self._say(f"\n{state.answer}\n")
        return wrote

    def _worth_trying(self, key: str) -> bool:
        count, at = self._failures.get(key, (0, 0.0))
        return count < self.MAX_TRIES or time.time() - at >= self.COOLDOWN

    def _attempted(self, key: str, wrote: bool) -> None:
        if wrote:
            self._failures.pop(key, None)
        else:
            count, _ = self._failures.get(key, (0, 0.0))
            self._failures[key] = (count + 1, time.time())
            if count + 1 >= self.MAX_TRIES:
                self._say(f"  giving [{key}] a rest — trying again in {self.COOLDOWN // 60} min")

    def _settled(self, key: str, text: str, *, settle: float | None = None) -> bool:
        """True once this exact text has sat unchanged long enough to be finished."""
        was = self._pending.get(key)
        if was is None or was[0] != text:
            self._pending[key] = (text, time.time())
            self._say(f"  saw [{key}]: {text[:60]}")
            return False
        return time.time() - was[1] >= (self.settle if settle is None else settle)

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
                                  title=workspace.ASK, folder=workspace.FOLDER)
        for q, envelope in zip(asks, envelopes):
            record = store.get(envelope.request_id)
            if record.status != 'prepared':
                if record.status in {'running', 'needs_review'}:
                    self._say('  request needs review before it can run again')
                continue
            key = f"ask:{envelope.request_id}"
            if not self._worth_trying(key):
                continue
            if not self._settled(key, q.text):
                continue
            wrote = self._answer(q.text, title=workspace.ASK, folder=workspace.FOLDER,
                                 after=q.after, source=q.text, note_id=self._ask_id, envelope=envelope)
            self._attempted(key, wrote)
            # Only the Ask note moved underneath us; a tag elsewhere is still
            # settling on its own clock and keeps its timer.
            for stale in [k for k in self._pending if k.startswith("ask:")]:
                self._pending.pop(stale, None)
            return              # one at a time; the note has moved underneath us

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
            if record.status != 'prepared':
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
            body = notes.read_body(m.note_id)
            from .markup import to_text
            from . import privacy
            wrote = self._answer(m.question, title=m.title, folder=m.folder, after=m.after,
                                 here=privacy.redact(to_text(body))[:4000], source=m.raw, note_id=m.note_id, modified=m.modified, envelope=m.envelope)
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
        if store.get(envelope.request_id).status != 'prepared':
            self._say('  filing batch already completed or needs review')
            return
        if not self._settled("dump", fingerprint, settle=self.dump_settle):
            return
        self._say(f"\n> [{workspace.DUMP}] quiet for {int(self.dump_settle // 60)} min — filing")
        outcome = requests.run_job(envelope, lambda: filer.run(self.brain, on_step=lambda m: self._say(f"  filer: {m}")))
        if outcome.result is None:
            self._say(outcome.message)
            return
        out = outcome.result
        for result in out.results:
            self._say(f"  {result}")
        self._say(f"\n{out.summary()}\n")
        self._pending.pop("dump", None)

    # ------------------------------------------------------------------- loop

    def run_forever(self) -> None:
        from . import retention
        retention.require_ready()
        # Notes may have been idle for hours. Waking it is slow exactly once.
        try:
            took = notes.warm_up()
            if took > 5:
                self._say(f"woke the Notes app ({took:.0f}s — it had been asleep)")
        except Exception as e:
            self._say(f"  (Notes did not wake: {type(e).__name__}) — trying anyway")

        self._say(f"listening to {workspace.ASK} — type in Notes on any device")
        try:
            n = self.scanner.prime()
            self._say(f"watching {n} notes for #notron — tag me anywhere")
        except Exception as e:
            self._say(f"  (couldn't survey your notes: {type(e).__name__}) — Ask note still works")

        last_sweep = last_beat = last_dump = time.time()
        while True:
            time.sleep(self.ask_poll)
            try:
                self.check_ask()
                if self.scanner.primed and time.time() - last_sweep >= self.sweep_every:
                    last_sweep = time.time()
                    self.sweep_mentions()
                if time.time() - last_dump >= self.dump_poll:
                    last_dump = time.time()
                    self.check_dump()
                if time.time() - last_beat >= 300:
                    last_beat = time.time()
                    self._say(f"  still listening ({time.strftime('%H:%M')})")
            except Exception as e:
                # Notes quits, the machine sleeps, a request times out. None of
                # these should end the day.
                self._say(f"  (paused: {type(e).__name__}) — retrying")
                time.sleep(self.ask_poll)


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
