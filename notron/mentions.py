"""Finding the places you have tagged Notron in your own notes.

The Ask note is a good front door, but it is not where thinking happens. Thinking
happens in the note about the book, the note about the move, the note from a
meeting eight months ago. Writing `#notron` in one of those turns it into a
conversation without moving anything out of it.

She answers directly underneath the line you tagged, and only there. In a note
that is not hers she never speaks unprompted, and never rewrites a word of yours.

Scanning cost is the whole engineering problem: Apple Notes serves one script
request at a time, and reading several hundred note bodies takes minutes. So we
read titles and timestamps once, then open only the notes that actually changed.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

from . import conversation, notes, workspace

STATE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "seen.json"


@dataclass
class Mention:
    note_id: str
    title: str
    folder: str
    question: str
    after: int


@dataclass
class Scanner:
    """Remembers what it has seen so each pass reads as little as possible."""

    seen: dict[str, str] = field(default_factory=dict)   # note id -> modified stamp
    pending: set[str] = field(default_factory=set)       # notes that still owe an answer
    primed: bool = False

    def prime(self) -> int:
        """Get ready to watch, without answering everything ever written.

        On the very first run this records the current state of every note, so
        that a `#notron` you typed two years ago does not suddenly get a reply.

        On every run after that it loads what was recorded last time — because a
        restart must not lose a tag. If you write `#notron` and the machine reboots
        before she gets to it, she still owes you an answer, and she knows it.
        """
        if STATE.exists():
            try:
                saved = json.loads(STATE.read_text())
                self.seen = saved.get("seen", {})
                self.pending = set(saved.get("pending", []))
                self.primed = True
                return len(self.seen)
            except (OSError, ValueError):
                pass

        for n in notes.list_all_notes():
            self.seen[n.id] = n.modified
        self._save()
        self.primed = True
        return len(self.seen)

    def _save(self) -> None:
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps({"seen": self.seen, "pending": sorted(self.pending)}))
        except OSError:
            pass

    def changed(self) -> list:
        """Notes worth reading this pass.

        That means notes touched since the last look — and also any note already
        known to be waiting on an answer. A note that changed once and was then
        marked seen would otherwise be reported a single time, which is not
        enough: Notron waits for your typing to settle before she replies, and
        settling takes at least two looks.
        """
        out = []
        for n in notes.list_all_notes():
            if n.folder == workspace.FOLDER:
                self.seen[n.id] = n.modified
                continue
            if self.seen.get(n.id) != n.modified or n.id in self.pending:
                out.append(n)
            self.seen[n.id] = n.modified
        self._save()
        return out

    def scan(self) -> list[Mention]:
        """Every unanswered `#notron` in a note that changed since the last look."""
        found: list[Mention] = []
        for n in self.changed():
            try:
                body = notes.read_body(n.id)
            except Exception:
                # Notes was busy. Keep this note on the list rather than dropping
                # it: forgetting it here means the tag is never answered until
                # the note happens to change again, which may be never.
                self.pending.add(n.id)
                self._save()
                continue
            asks = conversation.unanswered(body, ignore=(n.title,), require_tag=True)
            if asks:
                self.pending.add(n.id)
            else:
                self.pending.discard(n.id)
            for q in asks:
                found.append(Mention(
                    note_id=n.id, title=n.title, folder=n.folder,
                    question=conversation.strip_tag(conversation.tagged_lines(q.text)),
                    after=q.after,
                ))

        # Save *after* working out what is still owed. Saving inside `changed()`
        # records the new timestamps but an empty pending list, so a restart
        # believes every note is up to date and the tag is never answered.
        self._save()
        return found
