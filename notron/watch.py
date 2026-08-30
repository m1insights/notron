"""Listening, so Notes itself is the interface.

Two ways to reach Notron, both of them just typing in the Notes app:

  * write in `📥 Ask Notron`, anywhere in the note; or
  * write `#notron` in any note you own — the book idea, the meeting note, the
    half-finished plan — and ask about that thing, in that place.

Either way she answers directly underneath what you wrote, and draws no more
attention to herself than that.

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

from . import conversation, graph, mentions, notes, workspace

ASK_POLL = 5           # seconds between checks of the Ask note
SWEEP_EVERY = 20       # seconds between sweeps for #notron mentions (a survey is ~1s)
SETTLE = 6             # how long your typing must be still before she answers
MIN_CHARS = 2

# The Ask note's own standing text, which nobody said out loud.
ASK_FURNITURE = (workspace.ASK, "Type anything below this line")


@dataclass
class Watcher:
    brain: object
    ask_poll: float = ASK_POLL
    sweep_every: float = SWEEP_EVERY
    settle: float = SETTLE
    on_event: object = None
    scanner: mentions.Scanner = field(default_factory=mentions.Scanner)

    _pending: dict = field(default_factory=dict)   # key -> (text, first seen at)
    _ask_id: str | None = None

    # ---------------------------------------------------------------- output

    def _say(self, msg: str) -> None:
        if self.on_event:
            self.on_event(msg)

    # --------------------------------------------------------------- answering

    def _answer(self, question: str, *, title: str, folder: str, after: int,
                here: str = "") -> None:
        self._say(f"\n> [{title}] {question}")
        state = graph.run(
            question, brain=self.brain, trigger="notes",
            reply_to=(title, folder, after), here=here,
        )
        for line in state.trace:
            self._say(f"  {line}")
        for result in state.results:
            self._say(f"  {result}")
        if not any(r.startswith("✓") for r in state.results):
            self._say("  nothing was written — she will read this again")
        self._say(f"\n{state.answer}\n")

    def _settled(self, key: str, text: str) -> bool:
        """True once this exact text has sat unchanged long enough to be finished."""
        was = self._pending.get(key)
        if was is None or was[0] != text:
            self._pending[key] = (text, time.time())
            self._say(f"  saw [{key}]: {text[:60]}")
            return False
        return time.time() - was[1] >= self.settle

    # ------------------------------------------------------------- the two jobs

    def check_ask(self) -> None:
        # A note's id is stable, so look it up once rather than listing the
        # folder every few seconds — each listing is a request Notes must serve.
        if self._ask_id is None:
            note = notes.find_note(workspace.FOLDER, workspace.ASK)
            if not note:
                return
            self._ask_id = note.id
        body = notes.read_body(self._ask_id)
        for q in conversation.unanswered(body, ignore=ASK_FURNITURE):
            if len(q.text) < MIN_CHARS:
                continue
            if not self._settled(f"ask:{q.text[:40]}", q.text):
                continue
            self._answer(q.text, title=workspace.ASK, folder=workspace.FOLDER, after=q.after)
            self._pending.clear()
            return              # one at a time; the note has moved underneath us

    def sweep_mentions(self) -> None:
        found = self.scanner.scan()
        if found:
            self._say(f"  {len(found)} note(s) mention me")
        for m in found:
            if not self._settled(f"tag:{m.note_id}", m.question):
                continue
            body = notes.read_body(m.note_id)
            from .markup import to_text
            self._answer(m.question, title=m.title, folder=m.folder, after=m.after,
                         here=to_text(body)[:4000])
            self._pending.pop(f"tag:{m.note_id}", None)
            return

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

        last_sweep = last_beat = time.time()
        while True:
            time.sleep(self.ask_poll)
            try:
                self.check_ask()
                if self.scanner.primed and time.time() - last_sweep >= self.sweep_every:
                    last_sweep = time.time()
                    self.sweep_mentions()
                if time.time() - last_beat >= 300:
                    last_beat = time.time()
                    self._say(f"  still listening ({time.strftime('%H:%M')})")
            except Exception as e:
                # Notes quits, the machine sleeps, a request times out. None of
                # these should end the day.
                self._say(f"  (paused: {type(e).__name__}) — retrying")
                time.sleep(self.ask_poll)


WATCH_LABEL = "io.m1labs.notron.listen"


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
