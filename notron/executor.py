"""The only code in NOTRON that is allowed to change a note.

No language model runs here. The model proposes; the Guard judges; this applies
and records. Keeping the write path dumb is what makes the agent safe to leave
running on your own machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import booked, calendar, guard, markup, notedoc, notes, reminders, undo, workspace


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    reason: str
    note_id: str | None = None
    ref: str | None = None       # reminder id / event uid
    permanent: bool = False      # trying again can never help — see guard.Verdict


class Executor:
    def __init__(self, *, dry_run: bool = False, audit: bool = True) -> None:
        self.dry_run = dry_run
        self.audit = audit

    # -- public API ------------------------------------------------------

    def replace(self, title: str, body_markdown: str, *, folder: str = workspace.FOLDER,
                rewrite_allowed: bool = False) -> WriteResult:
        """Rewrite a note Notron owns — or, with `rewrite_allowed`, one of the
        user's own notes they have opted into rewrite-in-place."""
        return self._apply(folder, title, body_markdown, mode="replace",
                           rewrite_allowed=rewrite_allowed)

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

    def restore(self, title: str, raw_html_body: str, *, folder: str = workspace.FOLDER) -> WriteResult:
        """Put a note back exactly as it was — the undo path. `raw_html_body` is
        already-rendered HTML (what `undo.save` captured), never Markdown — it
        must not go through `markup.render`/`markup.to_html` a second time.
        Refused on a note that no longer exists — there is nothing to put back."""
        return self._apply(folder, title, raw_html_body, mode="restore")

    # -- internals -------------------------------------------------------

    def _apply(self, folder: str, title: str, body_markdown: str, *, mode: str,
               after: int | None = None, anchor: str = "",
               marks: list[tuple[str, int, str]] | None = None,
               rewrite_allowed: bool = False) -> WriteResult:
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
        elif mode == "restore":
            if not old_body:
                return WriteResult(False, "cannot restore a note that does not exist")
            # Already the note's own HTML, saved before she wrote over it.
            # Rendering it again would turn her markup into visible text.
            new_body = body_markdown
        else:
            new_body = markup.render(title, body_markdown)

        verdict = guard.check(
            folder=folder, title=title, old_body=old_body, new_body=new_body, mode=mode,
            rewrite_allowed=rewrite_allowed if mode == "replace" else False,
        )
        if not verdict:
            self._log(f"**BLOCKED** {mode} on *{title}* — {verdict.reason}")
            return WriteResult(False, verdict.reason, note.id if note else None,
                               permanent=verdict.permanent)

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
            # One step back, per note: what this note held a second before she
            # wrote, so "undo that" can put it back. Last thing before the
            # write, so a blocked or skipped write leaves no slot pointing at a
            # write that never happened. Only for a note that already existed —
            # a brand-new note has nothing to go back to. Not for `restore`
            # itself (design decision 4 lists append/insert/mark/replace, not
            # restore) — saving here would let a second `@notron undo` pop a
            # slot holding Notron's own overwritten body, tag line and all,
            # and write it right back: an undo/redo loop the mention scanner
            # would keep re-triggering forever, with no receipt to break it.
            if mode != "restore":
                undo.save(note.id, old_body)
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

        # She books before she writes the note, and the note write can fail —
        # after which the watcher retries the whole graph and the doer proposes
        # the identical reminder again. Nothing downstream could tell the two
        # apart. See booked.py. A match is a *blocked* write, so it is logged
        # like one (invariant #4), and the caller gets the reference of the
        # thing that already exists rather than a second one.
        seen = booked.already(action)
        if seen:
            self._log(f"**BLOCKED** {action.op} {action.kind} *{action.title}* — "
                      f"already set a moment ago")
            return WriteResult(True, "already set", ref=seen)

        try:
            ref, detail = self._perform(action)
        except LookupError as e:
            self._log(f"**BLOCKED** {action.op} {action.kind} *{action.title}* — {e}")
            return WriteResult(False, str(e))
        except Exception as e:
            # An app that is not approved yet hangs rather than failing, so a real
            # exception here is worth saying out loud instead of swallowing.
            #
            # And say what macOS said, not what Python called it. This used to
            # read "event app said no (EventKitError)", which is the same
            # sentence whether the calendar is denied, read-only or missing —
            # the user cannot act on a class name. `eventkit.failure` has
            # already folded `err.localizedDescription` into the message by the
            # time it reaches here; all this has to do is not throw it away.
            self._log(f"**FAILED** {action.op} {action.kind} *{action.title}* — {type(e).__name__}: {e}")
            return WriteResult(False, _plainly(e, action.kind))

        booked.remember(action, ref)
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
        if log:
            body = notes.read_body(log.id)
            notes.write_body(log.id, body + markup.to_html(entry))
        elif notes.folder_exists(workspace.FOLDER):
            # Every write is supposed to land here (invariant #4). If the note
            # itself got deleted, recreate it from its seed instead of
            # silently losing the audit trail from here on — the same
            # self-heal 📖 Lessons already gets from `replace` recreating it
            # the next time there's something to write.
            #
            # But only once the folder itself has answered. A missing 📊 Log
            # can also mean the *folder* read went astray, and then the honest
            # move is to wait, not to build a replacement: on 2026-09-03 a
            # shifted folder index made every read of 🤖 NOTRON return
            # Recently Deleted, and this branch fired on each write — fourteen
            # duplicate 📊 Log notes in two minutes. `folder_exists` asks Notes
            # again rather than trusting the cached folder list.
            seed = markup.render(workspace.LOG, workspace.SEEDS[workspace.LOG])
            notes.create_note(workspace.FOLDER, seed + markup.to_html(entry))


#: How long a macOS failure may be before it stops being readable in a note.
MAX_REASON_CHARS = 200


def _plainly(e: Exception, kind: str) -> str:
    """One failed action, in words the user can act on.

    The exception text is the interesting part — "could not create the event:
    save failed — Calendar access denied" names a switch in System Settings.
    The class name never did. A message with nothing in it (some ObjC bridges
    raise bare) falls back to naming the app, which is still more than nothing.
    """
    said = " ".join(str(e).split())
    if not said:
        return f"the {kind} app refused it and said nothing"
    if len(said) > MAX_REASON_CHARS:
        said = said[:MAX_REASON_CHARS - 1].rstrip() + "…"
    return said
