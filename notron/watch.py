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

from . import attachments, conversation, filer, graph, mentions, notes, workspace
from .markup import to_text
from .applescript import AppleScriptError, NotesBusy

ASK_POLL = 5           # seconds between checks of the Ask note
SWEEP_EVERY = 20       # seconds between sweeps for #notron mentions (a survey is ~1s)
SETTLE = 6             # how long your typing must be still before she answers
MIN_CHARS = 2

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
                here: str = "", source: str = "",
                carried: list | None = None) -> bool:
        self._say(f"\n> [{title}] {question}")
        state = graph.run(
            question, brain=self.brain, trigger="notes",
            reply_to=(title, folder, after), here=here, source=source,
            carried=carried,
        )
        for line in state.trace:
            self._say(f"  {line}")
        for result in state.results:
            self._say(f"  {result}")
        wrote = any(r.startswith("✓") for r in state.results)
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
        # A note's id is stable, so look it up once rather than listing the
        # folder every few seconds — each listing is a request Notes must serve.
        if self._ask_id is None:
            note = notes.find_note(workspace.FOLDER, workspace.ASK)
            if not note:
                return
            self._ask_id = note.id
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
        for q in conversation.unanswered(body, ignore=ASK_FURNITURE):
            if len(q.text) < MIN_CHARS:
                continue
            key = f"ask:{q.text[:40]}"
            if not self._worth_trying(key):
                continue
            if not self._settled(key, q.text):
                continue
            wrote = self._answer(q.text, title=workspace.ASK, folder=workspace.FOLDER,
                                 after=q.after, source=q.text,
                                 carried=self._carried(self._ask_id))
            self._attempted(key, wrote)
            # Only the Ask note moved underneath us; a tag elsewhere is still
            # settling on its own clock and keeps its timer.
            for stale in [k for k in self._pending if k.startswith("ask:")]:
                self._pending.pop(stale, None)
            return              # one at a time; the note has moved underneath us

    def sweep_mentions(self) -> None:
        found = self.scanner.scan()
        if found:
            self._say(f"  {len(found)} note(s) mention me")
        for m in found:
            key = f"tag:{m.note_id}"
            if not self._worth_trying(key):
                continue
            if not self._settled(key, m.question):
                continue
            body = notes.read_body(m.note_id)
            wrote = self._answer(m.question, title=m.title, folder=m.folder, after=m.after,
                                 here=here_text(body, m.raw or m.question), source=m.raw,
                                 carried=self._carried(m.note_id, m.modified))
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
        if self._dump_id is None:
            note = notes.find_note(workspace.FOLDER, workspace.DUMP)
            if not note:
                return
            self._dump_id = note.id
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
        if not filer.worth_a_pass(body):
            self._pending.pop("dump", None)
            return
        fingerprint = "\n".join(it.anchor for it in filer.unfiled(body))
        if not self._settled("dump", fingerprint, settle=self.dump_settle):
            return
        self._say(f"\n> [{workspace.DUMP}] quiet for {int(self.dump_settle // 60)} min — filing")
        out = filer.run(self.brain, on_step=lambda m: self._say(f"  filer: {m}"))
        for result in out.results:
            self._say(f"  {result}")
        self._say(f"\n{out.summary()}\n")
        self._pending.pop("dump", None)

    # ------------------------------------------------------------------- loop

    def run_forever(self) -> None:
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
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{WATCH_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string><string>-m</string><string>notron</string><string>listen</string>
  </array>
  <key>WorkingDirectory</key><string>{project}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>{project}/.notron/listen.log</string>
  <key>StandardErrorPath</key><string>{project}/.notron/listen.log</string>
</dict>
</plist>
"""
