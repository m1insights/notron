"""Watching the Ask note, so Notes itself is the interface.

This is the part that makes Juno feel like an assistant rather than a command.
You type a question into `📥 Ask Juno` — on your Mac, or on your phone where
iCloud carries it over in a few seconds — and she answers underneath it. No
terminal, no app, no send button.

How a turn is delimited: Juno ends every reply with a horizontal rule, so
whatever sits after the last rule is by definition the thing you just typed and
she has not answered yet. When she replies she writes a fresh rule, which empties
the slot again.

Two problems the naive version gets wrong:

  * Answering while you are still typing. Juno waits until the text has stopped
    changing for a beat before she treats it as a finished thought.
  * Answering herself. Her own reply changes the note, which looks exactly like
    new input; she remembers what the note looked like after she wrote it.

A question left unanswered when she starts is not stale — it is the one you asked
on your phone while this Mac was shut. She picks it up as soon as she wakes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import graph, markup, notes, workspace

SEPARATOR = "———"
POLL_SECONDS = 4
SETTLE_SECONDS = 6          # how long your typing must be still before she answers
MIN_CHARS = 2


def pending_question(body_html: str) -> str:
    """Whatever you typed after Juno's last reply."""
    text = markup.to_text(body_html)
    _, _, tail = text.rpartition(SEPARATOR)
    if not tail and SEPARATOR not in text:
        # She has never replied; everything after the intro line is fair game.
        lines = text.split("\n")
        tail = "\n".join(lines[2:])
    return tail.strip()


@dataclass
class Watcher:
    brain: object
    poll: float = POLL_SECONDS
    settle: float = SETTLE_SECONDS
    on_event: object = None

    def _say(self, msg: str) -> None:
        if self.on_event:
            self.on_event(msg)

    def answer(self, question: str) -> str:
        state = graph.run(question, brain=self.brain, trigger="notes")
        for line in state.trace:
            self._say(f"  {line}")
        return state.answer

    def run_forever(self) -> None:
        note = notes.find_note(workspace.FOLDER, workspace.ASK)
        if not note:
            raise SystemExit(f"{workspace.ASK} is missing — run `juno setup` first.")

        self._say(f"listening to {workspace.ASK} — type in Notes on any device")

        # Anything sitting unanswered at startup is a real question: you asked on
        # your phone while this Mac was asleep. She answers it once she wakes.
        last_seen = ""
        waiting = pending_question(notes.read_body(note.id))
        if waiting:
            self._say(f"  something was waiting for me: {waiting[:60]}")
        candidate, candidate_since = None, 0.0

        while True:
            time.sleep(self.poll)
            try:
                body = notes.read_body(note.id)
            except Exception as e:                      # Notes quits, machine sleeps
                self._say(f"  (couldn't read Notes: {type(e).__name__}) — retrying")
                continue

            question = pending_question(body)

            if question == last_seen or len(question) < MIN_CHARS:
                candidate = None
                continue

            if question != candidate:
                candidate, candidate_since = question, time.time()
                self._say(f"  saw: {question[:60]}…" if len(question) > 60 else f"  saw: {question}")
                continue

            if time.time() - candidate_since < self.settle:
                continue                                # still typing

            self._say(f"\n> {question}")
            try:
                reply = self.answer(question)
                self._say(f"\n{reply}\n")
            except Exception as e:
                self._say(f"  failed: {type(e).__name__}: {e}")

            last_seen = pending_question(notes.read_body(note.id))
            candidate = None


WATCH_LABEL = "io.m1labs.juno.listen"


def plist(python: str, project: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{WATCH_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string><string>-m</string><string>juno</string><string>listen</string>
  </array>
  <key>WorkingDirectory</key><string>{project}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{project}/.juno/listen.log</string>
  <key>StandardErrorPath</key><string>{project}/.juno/listen.log</string>
</dict>
</plist>
"""
