"""The pin guide — what the onboarding screen offers the user to pin.

Apple's Notes scripting has no `pinned` property (checked 2026-09-03 against
the Notes.app sdef), so nothing here pins anything. It names notes and finds
their ids; a human does the Control-clicking.
"""

from notron import notes, workspace


def fake(title, id="x", modified="Monday, 1 September 2026 at 09:00:00"):
    return notes.Note(id=id, title=title, folder=workspace.FOLDER, modified=modified)


def test_every_system_note_is_offered_and_has_a_why():
    # A note bootstrap creates but the guide forgets is a note the user can
    # never be told to pin — keep the three lists locked together.
    assert set(workspace.PIN_ORDER) == set(workspace.SYSTEM_NOTES)
    assert set(workspace.PIN_WHY) == set(workspace.SYSTEM_NOTES)


def test_the_three_suggested_are_the_ones_the_user_types_in():
    assert workspace.PIN_SUGGESTED == (workspace.ABOUT, workspace.ASK, workspace.DUMP)
    assert workspace.PIN_ORDER[:3] == workspace.PIN_SUGGESTED


def test_pin_guide_carries_live_ids_and_marks_the_suggested(monkeypatch):
    monkeypatch.setattr(notes, "list_notes",
                        lambda folder: [fake(t, f"id-{i}")
                                        for i, t in enumerate(workspace.SYSTEM_NOTES)])
    rows = workspace.pin_guide()
    assert [r["title"] for r in rows] == list(workspace.PIN_ORDER)
    assert all(r["id"] for r in rows)
    assert [r["suggested"] for r in rows[:3]] == [True, True, True]
    assert not any(r["suggested"] for r in rows[3:])
    assert all(r["why"] for r in rows)


def test_a_note_bootstrap_has_not_made_yet_is_left_out(monkeypatch):
    # A row with no id is a row whose "Show in Notes" button does nothing.
    # Better absent than dead.
    monkeypatch.setattr(notes, "list_notes",
                        lambda folder: [fake(workspace.ASK, "id-ask")])
    rows = workspace.pin_guide()
    assert [r["title"] for r in rows] == [workspace.ASK]


def test_a_duplicate_title_offers_the_newest_note(monkeypatch):
    # Notes allows two notes with the same name (CLAUDE.md's Performance
    # section). library.py's suggest() keeps the newest as the real one —
    # match that convention here instead of silently picking whichever the
    # AppleScript happened to return last.
    # Newest listed *first* — a plain "last one wins" dict build would pick
    # the wrong (older) note here, which is the bug this test catches.
    monkeypatch.setattr(notes, "list_notes", lambda folder: [
        fake(workspace.ASK, "id-new", modified="Wednesday, 3 September 2026 at 09:00:00"),
        fake(workspace.ASK, "id-old", modified="Monday, 1 September 2026 at 09:00:00"),
    ])
    rows = workspace.pin_guide()
    assert [r["id"] for r in rows] == ["id-new"]


def test_the_cli_prints_the_guide_as_json(monkeypatch, capsys):
    import json

    from notron import cli

    monkeypatch.setattr(notes, "list_notes", lambda folder: [fake(workspace.ASK, "id-ask")])
    cli.cmd_pins(type("A", (), {"json": True})())
    rows = json.loads(capsys.readouterr().out)
    assert rows == [{"title": workspace.ASK, "id": "id-ask",
                     "why": workspace.PIN_WHY[workspace.ASK], "suggested": True}]
