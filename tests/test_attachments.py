"""What a note carries besides its text."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from notron import attachments, library


@pytest.fixture(autouse=True)
def _library_is_never_the_developers(monkeypatch, tmp_path):
    """`on_note` now asks the library whether this note may be read at all, so
    every test here would otherwise consult the developer's own choices."""
    monkeypatch.setattr(library, "STATE", tmp_path / "library.json")


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


def test_an_ignored_note_is_not_even_asked_what_it_carries(_notes_is_never_the_real_one):
    """Invariant 9 says an ignored note is never read. A photo inside one is
    still inside it — and a description of a photo is a read of the photo."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Private"] = [("passport.png", "att/9")]
    lib = library.Library(ignore={"Notes/Private"})
    library.save(lib)

    assert attachments.on_note("Notes/Private") == []
    assert "attachments" not in app.calls      # not filtered afterwards — never asked


def test_a_note_hidden_only_by_the_year_cutoff_is_also_never_asked(_notes_is_never_the_real_one):
    """The cutoff hides a note as completely as an explicit ignore does, so the
    date has to travel with the id — an id alone cannot answer the question."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Old"] = [("scan.png", "att/1")]
    library.save(library.Library(start_from=library.parse_start("2026")))

    old = "Wednesday, 2 September 2019 at 21:30:00"
    assert attachments.on_note("Notes/Old", old) == []
    assert "attachments" not in app.calls

    recent = "Wednesday, 2 September 2026 at 21:30:00"
    assert [a.name for a in attachments.on_note("Notes/Old", recent)] == ["scan.png"]
