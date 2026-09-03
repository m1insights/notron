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


def test_the_cli_sets_the_default_for_new_notes(tmp_path, monkeypatch):
    from notron import cli

    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    cli.main(["rewrite", "--default", "always"])
    assert rewrite.default_for_new_notes() == "always"


def test_a_per_note_allow_does_not_count_as_choosing_the_global_default(tmp_path, monkeypatch):
    # Real bug: allow() and set_default_for_new_notes() used to stamp the same
    # chosen_at field, so the Mac app's onboarding sheet (gated on that field)
    # would never show for a user who'd already said `@notron yes` to a single
    # note's organizer offer, via Notes or the CLI, before ever opening it.
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    rewrite.allow("note-1")
    assert rewrite.default_chosen() is False
    rewrite.set_default_for_new_notes("always")
    assert rewrite.default_chosen() is True
