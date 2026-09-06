"""What a note carries besides its text."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import attachments


def test_a_table_is_not_a_file(_notes_is_never_the_real_one):
    """Notes models an inline table as an attachment with no name — 41 of the
    58 attachments in the developer's own library are tables. Transcribing one
    is nonsense, so they never reach the caller."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [
        ("missing value", "att/1"),
        ("pasta.png", "att/2"),
    ]
    found = attachments.on_note("Notes/Recipes")
    assert [a.name for a in found] == ["pasta.png"]


def test_each_file_is_classified_by_what_she_could_do_with_it(_notes_is_never_the_real_one):
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [
        ("board.png", "att/1"), ("memo.m4a", "att/2"),
        ("spec.pdf", "att/3"), ("log.txt", "att/4"), ("thing.zip", "att/5"),
    ]
    assert [a.kind for a in attachments.on_note("Notes/Recipes")] == [
        "image", "audio", "pdf", "text", "other"]


def test_a_note_with_nothing_attached_costs_one_empty_answer(_notes_is_never_the_real_one):
    assert attachments.on_note("Notes/Parking Garages") == []
