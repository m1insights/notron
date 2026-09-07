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
    raw: str = ""        # the whole turn, tags intact — what the Filer ticks
    modified: str = ""   # the note's timestamp, so anything asked about this
                         # note afterwards can apply the library's year cutoff


@dataclass
class Scanner:
    """Remembers what it has seen so each pass reads as little as possible."""

    seen: dict[str, str] = field(default_factory=dict)   # note id -> modified stamp
    pending: set[str] = field(default_factory=set)       # notes that still owe an answer
    #: note id -> the questions she has already answered somewhere other than in
    #: the note itself. Normally a tag is retired by her reply landing under it,
    #: but a note holding a picture can never carry one: Apple Notes deletes the
    #: picture from any note a script writes to (invariant 13), so she answers in
    #: 📥 Ask Notron instead. Without this the tag stays unanswered for ever —
    #: which means `pending` never clears, which means `scan` re-reads the note
    #: on every sweep. Measured live on 2026-09-06: a 1.8MB AppleScript read
    #: every twenty seconds, for the life of the note, against the one app that
    #: serves a single request at a time.
    #:
    #: Keyed by the question and not just the note, because the note is not
    #: finished — the next thing they ask in it deserves an answer like any
    #: other. And persisted, because in memory alone every restart re-answered
    #: it and appended the same reply to 📥 Ask Notron again.
    answered_elsewhere: dict[str, list[str]] = field(default_factory=dict)
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
                self.answered_elsewhere = saved.get("answered_elsewhere", {})
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
            STATE.write_text(json.dumps({
                "seen": self.seen,
                "pending": sorted(self.pending),
                "answered_elsewhere": self.answered_elsewhere,
            }))
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
        from . import library

        lib = library.load()
        out = []
        live = notes.list_all_notes()
        # An id that is no longer in the library is a deleted note, and it was
        # only ever discarded by being seen again — so it sat in
        # .notron/seen.json for ever, owing an answer nobody could give.
        alive = {n.id for n in live}
        self.pending &= alive
        self.answered_elsewhere = {k: v for k, v in self.answered_elsewhere.items()
                                   if k in alive}
        for n in live:
            if n.folder == workspace.FOLDER or lib.is_ignored(n):
                # Hers, or the user's business: remembered as seen, never read.
                # Un-ignoring later then means "read it from now", not "answer
                # every tag it ever held".
                self.seen[n.id] = n.modified
                continue
            if self.seen.get(n.id) != n.modified or n.id in self.pending:
                out.append(n)
            self.seen[n.id] = n.modified
        self._save()
        return out

    def answered_away(self, mention: Mention) -> None:
        """This question has been answered, but not inside the note itself.

        Retires the tag the way a reply under it normally would, so the note
        stops owing an answer — and stops being re-read every sweep — without
        anything being written into it. See `answered_elsewhere`.
        """
        said = self.answered_elsewhere.setdefault(mention.note_id, [])
        if mention.question not in said:
            said.append(mention.question)
        self._save()

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
            said = self.answered_elsewhere.get(n.id, [])
            asks = [q for q in asks
                    if conversation.strip_tag(conversation.tagged_lines(q.text)) not in said]
            if asks:
                self.pending.add(n.id)
            else:
                # Nothing left owing — including the case where every tag in it
                # was answered in 📥 Ask Notron. Dropping it here is what stops
                # the note being re-read on every sweep.
                self.pending.discard(n.id)
            for q in asks:
                found.append(Mention(
                    note_id=n.id, title=n.title, folder=n.folder,
                    question=conversation.strip_tag(conversation.tagged_lines(q.text)),
                    after=q.after,
                    raw=q.text,
                    modified=n.modified,
                ))

        # Save *after* working out what is still owed. Saving inside `changed()`
        # records the new timestamps but an empty pending list, so a restart
        # believes every note is up to date and the tag is never answered.
        self._save()
        return found
