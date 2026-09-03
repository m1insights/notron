from notron import rewrite


def test_a_note_is_not_rewrite_allowed_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    assert rewrite.allowed("note-1") is False


def test_allow_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    rewrite.allow("note-1")
    assert rewrite.allowed("note-1") is True


def test_allow_does_not_affect_other_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    rewrite.allow("note-1")
    assert rewrite.allowed("note-2") is False


def test_default_for_new_notes_defaults_to_ask(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    assert rewrite.default_for_new_notes() == "ask"


def test_set_default_for_new_notes_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    rewrite.set_default_for_new_notes("always")
    assert rewrite.default_for_new_notes() == "always"


def test_allow_and_default_survive_a_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    rewrite.allow("note-1")
    rewrite.set_default_for_new_notes("never")
    # a fresh read from disk, not the same in-memory object
    assert rewrite.allowed("note-1") is True
    assert rewrite.default_for_new_notes() == "never"
