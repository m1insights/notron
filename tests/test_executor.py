"""Guarding against overwriting text the user is still typing.

`Executor._apply` builds the new body from a body it read a moment ago. Between
that read and the write, several seconds of model latency sit in the way, and
Notes is not locked — the user can keep typing in the very note being answered.
Writing that stale body back with `set body of note to ...` replaces the whole
document, silently eating or mangling whatever they typed in the gap. This is
the "sentence gets cut off and Notron is confused" bug: her reply lands on top
of half-typed text, the note comes out garbled, and she reads the garble back
as a new question next pass.

The fix re-reads immediately before writing and refuses to write if the note
moved underneath it, leaving the retry to the watcher's normal next pass
instead of a caller ever seeing a torn note.
"""

from __future__ import annotations

from notron import executor as ex_mod
from notron.notes import Note


def _note(id: str = "n1") -> Note:
    return Note(id=id, title="📥 Ask Notron", folder="🤖 NOTRON", modified="x")


def test_a_write_is_skipped_if_the_note_changed_since_it_was_read(monkeypatch):
    old_body = "<div>📥 Ask Notron</div><div>How does</div>"
    # The user kept typing while she was thinking.
    live_body = "<div>📥 Ask Notron</div><div>How does the moon landing footage hold up</div>"

    reads = [old_body, live_body]
    monkeypatch.setattr(ex_mod.notes, "find_note", lambda folder, title: _note())
    monkeypatch.setattr(ex_mod.notes, "read_body", lambda note_id: reads.pop(0))
    written = []
    monkeypatch.setattr(ex_mod.notes, "write_body", lambda note_id, body: written.append(body))

    ex = ex_mod.Executor()
    result = ex.insert("📥 Ask Notron", "an answer", after=1, anchor="How does")

    assert not result.ok
    assert "changed" in result.reason
    assert written == []


def test_a_write_proceeds_when_the_note_is_unchanged(monkeypatch):
    body = "<div>📥 Ask Notron</div><div>How does this work</div>"
    monkeypatch.setattr(ex_mod.notes, "find_note", lambda folder, title: _note())
    monkeypatch.setattr(ex_mod.notes, "read_body", lambda note_id: body)
    written = []
    monkeypatch.setattr(ex_mod.notes, "write_body", lambda note_id, b: written.append(b))

    ex = ex_mod.Executor()
    result = ex.insert("📥 Ask Notron", "an answer", after=1, anchor="How does this work")

    assert result.ok
    assert written and "an answer" in written[0]


def test_replace_mode_is_not_slowed_by_the_extra_read(monkeypatch):
    """Replace builds new_body from the model's output, not from old_body, so a
    concurrent edit to old_body can't corrupt it — no need to pay for a second read."""
    reads = []
    monkeypatch.setattr(ex_mod.notes, "find_note", lambda folder, title: _note())

    def read_body(note_id):
        reads.append(note_id)
        return "<div>☀️ Today</div><div>old plan</div>"

    monkeypatch.setattr(ex_mod.notes, "read_body", read_body)
    monkeypatch.setattr(ex_mod.notes, "write_body", lambda note_id, b: None)

    ex = ex_mod.Executor(audit=False)  # isolate the body write from the separate log-note read
    result = ex.replace("☀️ Today", "new plan")

    assert result.ok
    assert len(reads) == 1


def test_a_write_to_an_existing_note_saves_its_old_body(monkeypatch):
    """One step back, per note: whatever it held before she wrote is kept so
    "undo that" can put it back."""
    body = "<div>Some Note</div><div>a line the user wrote</div>"
    monkeypatch.setattr(ex_mod.notes, "find_note", lambda folder, title: _note())
    monkeypatch.setattr(ex_mod.notes, "read_body", lambda note_id: body)
    monkeypatch.setattr(ex_mod.notes, "write_body", lambda note_id, b: None)
    saved = {}
    monkeypatch.setattr(ex_mod.undo, "save", lambda nid, old: saved.setdefault(nid, old))

    ex = ex_mod.Executor(audit=False)
    result = ex.append("Some Note", "more text", folder="Notes")

    assert result.ok
    assert saved == {"n1": body}


def test_a_refused_write_saves_no_undo(monkeypatch):
    """A slot saved for a write that never happened would undo to the wrong
    moment — so the save sits after the Guard and after the changed-note check."""
    old_body = "<div>📥 Ask Notron</div><div>How does</div>"
    live_body = "<div>📥 Ask Notron</div><div>How does the moon landing footage hold up</div>"

    reads = [old_body, live_body]
    monkeypatch.setattr(ex_mod.notes, "find_note", lambda folder, title: _note())
    monkeypatch.setattr(ex_mod.notes, "read_body",
                        lambda note_id: reads.pop(0) if reads else live_body)
    monkeypatch.setattr(ex_mod.notes, "write_body", lambda note_id, b: None)
    saved = {}
    monkeypatch.setattr(ex_mod.undo, "save", lambda nid, old: saved.setdefault(nid, old))

    ex = ex_mod.Executor(audit=False)
    changed = ex.insert("📥 Ask Notron", "an answer", after=1, anchor="How does")
    blocked = ex.replace("Recipes", "a rewrite", folder="Notes")

    assert not changed.ok and not blocked.ok
    assert saved == {}


def test_restore_writes_the_body_back_verbatim(monkeypatch, tmp_path):
    """`raw_html_body` is already-rendered HTML — what `undo.save` captured —
    so it has to land byte for byte, never through `markup.render` a second time."""
    monkeypatch.setattr(ex_mod.undo, "STATE", tmp_path / "undo.json")
    monkeypatch.setattr(ex_mod.notes, "find_note", lambda folder, title: _note())
    monkeypatch.setattr(ex_mod.notes, "read_body",
                        lambda note_id: "<div>☀️ Today</div><div>what she wrote over it</div>")
    written = []
    monkeypatch.setattr(ex_mod.notes, "write_body", lambda note_id, b: written.append(b))

    original = "<div>☀️ Today</div><div>*original* — her words, not markdown</div>"
    result = ex_mod.Executor(audit=False).restore("☀️ Today", original)

    assert result.ok
    assert written == [original]


def test_replace_outside_her_folder_needs_the_opt_in(monkeypatch, tmp_path):
    """`rewrite_allowed` reaches the Guard from `replace`, and only from it."""
    monkeypatch.setattr(ex_mod.undo, "STATE", tmp_path / "undo.json")
    monkeypatch.setattr(ex_mod.notes, "find_note", lambda folder, title: _note())
    monkeypatch.setattr(ex_mod.notes, "read_body", lambda note_id: "<div>Recipes</div><div>old</div>")
    written = []
    monkeypatch.setattr(ex_mod.notes, "write_body", lambda note_id, b: written.append(b))

    ex = ex_mod.Executor(audit=False)
    assert not ex.replace("Recipes", "a rewrite", folder="Notes").ok
    assert ex.replace("Recipes", "a rewrite", folder="Notes", rewrite_allowed=True).ok
    assert len(written) == 1


def test_the_log_recreates_itself_if_it_was_deleted(monkeypatch):
    """Every write is supposed to land in 📊 Log (invariant #4). If the note
    itself got deleted, the old code just returned — every future write still
    reported success while the audit trail was silently gone for good."""
    monkeypatch.setattr(ex_mod.notes, "find_note", lambda folder, title: None)
    created = []
    monkeypatch.setattr(ex_mod.notes, "create_note",
                         lambda folder, body: created.append((folder, body)) or "new-id")

    ex_mod.Executor()._log("did a thing")

    assert len(created) == 1
    folder, body = created[0]
    assert folder == ex_mod.workspace.FOLDER
    assert "did a thing" in body
    assert ex_mod.workspace.LOG in body  # the seed's title heading survives
