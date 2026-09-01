"""The only code in NOTRON that is allowed to change a note.

No language model runs here. The model proposes; the Guard judges; this applies
and records. Keeping the write path dumb is what makes the agent safe to leave
running on your own machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import calendar, guard, markup, notedoc, notes, reminders, workspace


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    reason: str
    note_id: str | None = None
    ref: str | None = None       # reminder id / event uid


class Executor:
    def __init__(self, *, dry_run: bool = False, audit: bool = True) -> None:
        self.dry_run = dry_run
        self.audit = audit

    # -- public API ------------------------------------------------------

    def replace(self, title: str, body_markdown: str, *, folder: str = workspace.FOLDER) -> WriteResult:
        """Rewrite a note Notron owns."""
        return self._apply(folder, title, body_markdown, mode="replace")

    def append(self, title: str, body_markdown: str, *, folder: str = workspace.FOLDER) -> WriteResult:
        """Add to the end of a note without touching what is already there."""
        return self._apply(folder, title, body_markdown, mode="append")

    def insert(self, title: str, body_markdown: str, *, after: int,
               folder: str = workspace.FOLDER, anchor: str = "") -> WriteResult:
        """Answer directly underneath the block someone wrote, wherever it sits."""
        return self._apply(folder, title, body_markdown, mode="insert", after=after,
                           anchor=anchor)

    def mark(self, title: str, marks: list[tuple[str, int, str]], *,
             folder: str = workspace.FOLDER) -> WriteResult:
        """Tick lines as filed: a ✓ in front, a receipt after, nothing else.
        Each mark is (the line's text, a block hint, the receipt)."""
        return self._apply(folder, title, "", mode="mark", marks=marks)

    # -- internals -------------------------------------------------------

    def _apply(self, folder: str, title: str, body_markdown: str, *, mode: str,
               after: int | None = None, anchor: str = "",
               marks: list[tuple[str, int, str]] | None = None) -> WriteResult:
        note = notes.find_note(folder, title)
        old_body = notes.read_body(note.id) if note else ""

        if mode == "mark":
            if not old_body:
                return WriteResult(False, "cannot mark a note that does not exist")
            # Lines are found by their words, not their position — the user
            # may have added a line above since the Filer read the note.
            new_body, ticked = notedoc.mark_lines(old_body, marks or [])
            if not ticked:
                return WriteResult(False, "nothing to tick — those lines have changed or gone",
                                   note.id if note else None)
        elif mode == "append":
            new_body = (old_body or markup.render(title, "")) + markup.to_html(body_markdown)
        elif mode == "insert":
            if not old_body:
                return WriteResult(False, "cannot insert into a note that does not exist")
            # The note may have changed since the block index was captured —
            # the user keeps typing while the model thinks. Re-find the words.
            at = notedoc.locate(old_body, anchor, near=after or 0)
            new_body = notedoc.insert_after(old_body, at, markup.to_html(body_markdown))
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
            # `new_body` for append/insert is old_body plus something wedged in —
            # built from a read that happened a moment ago. The model's thinking
            # time sits in that gap, unlocked, and the user can keep typing in
            # this exact note. Writing the stale version back is a full-body
            # overwrite that silently eats or mangles whatever they typed in
            # the meantime — that is the "sentence gets cut off" bug. So check
            # the note hasn't moved right before committing, and if it has,
            # skip this write rather than clobber it; the watcher tries again
            # next pass. `replace` doesn't need this: its new_body comes from
            # the model's output, not from old_body, so it can't be corrupted
            # by a concurrent edit the same way.
            if mode in ("append", "insert", "mark") and notes.read_body(note.id) != old_body:
                return WriteResult(False, "the note changed while she was writing — "
                                          "she'll try again next pass", note.id)
            notes.write_body(note.id, new_body)
            note_id = note.id
        else:
            note_id = notes.create_note(folder, new_body)

        self._log(f"{mode} on *{title}* ({len(new_body)} chars)")
        return WriteResult(True, "written", note_id)

    def do(self, action, *, about: str = "", request: str = "") -> WriteResult:
        """Apply one thing outside Notes. Still no model anywhere in this path."""
        verdict = guard.check_action(action, about=about, request=request)
        if not verdict:
            self._log(f"**BLOCKED** {action.op} {action.kind} *{action.title}* — {verdict.reason}")
            return WriteResult(False, verdict.reason)

        if self.dry_run:
            return WriteResult(True, "dry run — nothing created")

        try:
            ref, detail = self._perform(action)
        except LookupError as e:
            self._log(f"**BLOCKED** {action.op} {action.kind} *{action.title}* — {e}")
            return WriteResult(False, str(e))
        except Exception as e:
            # An app that is not approved yet hangs rather than failing, so a real
            # exception here is worth saying out loud instead of swallowing.
            self._log(f"**FAILED** {action.op} {action.kind} *{action.title}* — {type(e).__name__}: {e}")
            return WriteResult(False, f"{action.kind} app said no ({type(e).__name__})")

        self._log(f"{action.op} {action.kind} *{action.title}*{detail}")
        return WriteResult(True, detail.strip(" —") or "done", ref=ref)

    def _perform(self, action) -> tuple[str, str]:
        if action.kind == "reminder" and action.op == "create":
            ref = reminders.create(action.title, notes=action.notes,
                                   list_name=action.where, when_iso=action.when)
            return ref, self._said(action)

        if action.kind == "reminder" and action.op == "complete":
            hit = reminders.find_open(action.title)
            if hit is None:
                raise LookupError(f"couldn't find an open reminder called {action.title!r}")
            reminders.complete(hit.id)
            return hit.id, f" — ticked off “{hit.title}”"

        if action.kind == "event" and action.op == "create":
            ref = calendar.create(action.title, start_iso=action.when, end_iso=action.ends,
                                  calendar_name=action.where, notes=action.notes)
            return ref, self._said(action)

        raise ValueError(f"nothing to do for {action.kind}/{action.op}")

    @staticmethod
    def _said(action) -> str:
        from . import when as when_mod

        moment = when_mod.parse(action.when)
        return f" — {when_mod.human(moment)}" if moment else ""

    def _log(self, line: str) -> None:
        if not self.audit or self.dry_run:
            return
        entry = f"{datetime.now():%Y-%m-%d %H:%M} — {line}"
        log = notes.find_note(workspace.FOLDER, workspace.LOG)
        if not log:
            return
        body = notes.read_body(log.id)
        notes.write_body(log.id, body + markup.to_html(entry))
