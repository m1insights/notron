from notron import undo


def test_save_then_pop_returns_the_body_once(tmp_path, monkeypatch):
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")
    undo.save("note-1", "<div>old</div>")
    assert undo.pop("note-1") == "<div>old</div>"
    assert undo.pop("note-1") is None          # one level — consumed


def test_save_ignores_an_empty_old_body(tmp_path, monkeypatch):
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")
    undo.save("note-2", "")                     # brand-new note, nothing to undo to
    assert undo.pop("note-2") is None


def test_a_second_save_overwrites_the_first(tmp_path, monkeypatch):
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")
    undo.save("note-3", "<div>v1</div>")
    undo.save("note-3", "<div>v2</div>")
    assert undo.pop("note-3") == "<div>v2</div>"


def test_pop_on_an_unknown_note_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")
    assert undo.pop("never-saved") is None
