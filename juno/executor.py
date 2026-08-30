"""The only code in JUNO that is allowed to change a note.

No language model runs here. The model proposes; the Guard judges; this applies
and records. Keeping the write path dumb is what makes the agent safe to leave
running on your own machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import guard, markup, notedoc, notes, workspace


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    reason: str
    note_id: str | None = None


class Executor:
    def __init__(self, *, dry_run: bool = False, audit: bool = True) -> None:
        self.dry_run = dry_run
        self.audit = audit

    # -- public API ------------------------------------------------------

    def replace(self, title: str, body_markdown: str, *, folder: str = workspace.FOLDER) -> WriteResult:
        """Rewrite a note Juno owns."""
        return self._apply(folder, title, body_markdown, mode="replace")

    def append(self, title: str, body_markdown: str, *, folder: str = workspace.FOLDER) -> WriteResult:
        """Add to the end of a note without touching what is already there."""
        return self._apply(folder, title, body_markdown, mode="append")

    def insert(self, title: str, body_markdown: str, *, after: int,
               folder: str = workspace.FOLDER) -> WriteResult:
        """Answer directly underneath the block someone wrote, wherever it sits."""
        return self._apply(folder, title, body_markdown, mode="insert", after=after)

    # -- internals -------------------------------------------------------

    def _apply(self, folder: str, title: str, body_markdown: str, *, mode: str,
               after: int | None = None) -> WriteResult:
        note = notes.find_note(folder, title)
        old_body = notes.read_body(note.id) if note else ""

        if mode == "append":
            new_body = (old_body or markup.render(title, "")) + markup.to_html(body_markdown)
        elif mode == "insert":
            if not old_body:
                return WriteResult(False, "cannot insert into a note that does not exist")
            new_body = notedoc.insert_after(old_body, after or 0, markup.to_html(body_markdown))
        else:
            new_body = markup.render(title, body_markdown)

        verdict = guard.check(
            folder=folder, title=title, old_body=old_body, new_body=new_body, mode=mode
        )
        if not verdict:
            self._log(f"**BLOCKED** {mode} on *{title}* — {verdict.reason}")
            return WriteResult(False, verdict.reason, note.id if note else None)

        if self.dry_run:
            return WriteResult(True, "dry run — nothing written", note.id if note else None)

        if note:
            notes.write_body(note.id, new_body)
            note_id = note.id
        else:
            note_id = notes.create_note(folder, new_body)

        self._log(f"{mode} on *{title}* ({len(new_body)} chars)")
        return WriteResult(True, "written", note_id)

    def _log(self, line: str) -> None:
        if not self.audit or self.dry_run:
            return
        entry = f"{datetime.now():%Y-%m-%d %H:%M} — {line}"
        log = notes.find_note(workspace.FOLDER, workspace.LOG)
        if not log:
            return
        body = notes.read_body(log.id)
        notes.write_body(log.id, body + markup.to_html(entry))
