"""What she saw and heard, made searchable.

`notron/index.py` had no tests of its own; attachments give it a reason. The
question these answer is not "does search work" but "can a file she looked at
in one note answer a question asked somewhere else — and only if she is still
allowed to read the note it hangs off".
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from notron import attachments, index, library
from notron.outbound import Passage


@pytest.fixture(autouse=True)
def _index_is_disposable(monkeypatch, tmp_path, _policy_is_disposable):
    monkeypatch.setattr(index, "CACHE", tmp_path / "index.json")
    monkeypatch.setattr(index, "VECTORS", tmp_path / "vectors.npy")
    monkeypatch.setattr(index, "_MMAP", None)
    monkeypatch.setattr(library, "STATE", tmp_path / "library.json")
    monkeypatch.setattr(attachments, "CACHE", tmp_path / "attachments")
    library.save(library.Library(allow_new_notes=True))
    from dataclasses import replace
    original = attachments.notes.get_note
    def metadata(nid):
        note = original(nid)
        return replace(note, modified='Wednesday, 2 September 2026 at 21:30:00') if note else None
    monkeypatch.setattr(attachments.notes, 'get_note', metadata)


class CountingBrain:
    def __init__(self):
        self.embedded = []

    def embed(self, texts):
        self.embedded.extend(p.text for p in texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    def see(self, **kw):
        raise AssertionError("a vision call during `notron index` without --attachments")


def _carrying(app, note_title, name, att_id):
    app.attachments[f"Notes/{note_title}"] = [(name, att_id)]


def _remember(text):
    att = attachments.on_note('Notes/Parking Garages')[0]
    attachments._put(att, words=text)


def test_what_she_already_read_is_indexed_for_free(_notes_is_never_the_real_one):
    """A picture she looked at while answering one question becomes searchable
    everywhere, and costs nothing to add — the words are already on disk."""
    app = _notes_is_never_the_real_one
    _carrying(app, "Parking Garages", "ticket.png", "att/1")
    _remember("Bay 4, expires 18:00")

    brain = CountingBrain()
    index.build(brain)
    assert any("Bay 4, expires 18:00" in t for t in brain.embedded)


def test_the_chunk_says_which_file_and_which_note(_notes_is_never_the_real_one):
    app = _notes_is_never_the_real_one
    _carrying(app, "Parking Garages", "ticket.png", "att/1")
    _remember("Bay 4")

    index.build(CountingBrain())
    titles = [c["title"] for rows in index._load().values() for c in rows]
    assert "ticket.png (attached to Parking Garages)" in titles


def test_nothing_is_described_or_transcribed_without_being_asked(_notes_is_never_the_real_one):
    """A first index run on a library of screenshots must not quietly spend a
    vision call on each one. CountingBrain.see raises if it is ever reached."""
    app = _notes_is_never_the_real_one
    _carrying(app, "Parking Garages", "ticket.png", "att/1")
    app.files["att/1"] = b"png"
    index.build(CountingBrain())      # no --attachments: no look, no error


def test_asking_for_it_reads_the_files(_notes_is_never_the_real_one, monkeypatch):
    app = _notes_is_never_the_real_one
    _carrying(app, "Parking Garages", "ticket.png", "att/1")
    app.files["att/1"] = b"png"
    monkeypatch.setattr(attachments, "describe", lambda a, brain, **kw: "Bay 4, expires 18:00")

    brain = CountingBrain()
    index.build(brain, extract=True)
    assert any("Bay 4" in t for t in brain.embedded)


def test_an_ignored_note_never_has_its_files_indexed(_notes_is_never_the_real_one):
    """Invariant 11. `library.user_notes()` stays the only enumeration, so this
    holds without the index having to know anything about ignoring."""
    app = _notes_is_never_the_real_one
    _carrying(app, "Parking Garages", "ticket.png", "att/1")
    _remember("Bay 4")
    library.save(library.Library(ignore={"Notes/Parking Garages"}))

    brain = CountingBrain()
    index.build(brain)
    assert not any("Bay 4" in t for t in brain.embedded)


def test_a_note_ignored_after_it_was_indexed_stops_answering(_notes_is_never_the_real_one):
    """The index can be a week older than the choice, so `search` re-checks —
    and that check has to cover attachment chunks too, not only bodies."""
    app = _notes_is_never_the_real_one
    _carrying(app, "Parking Garages", "ticket.png", "att/1")
    _remember("Bay 4")
    index.build(CountingBrain())

    assert any(c.attachment for c in index.search([Passage('parking', 'user_request')], CountingBrain()))

    library.save(library.Library(ignore={"Notes/Parking Garages"}))
    hits = index.search([Passage('parking', 'user_request')], CountingBrain())
    assert not any(c.note_id == "Notes/Parking Garages" for c in hits)


def test_a_file_read_since_the_last_index_is_picked_up(_notes_is_never_the_real_one):
    """Nothing about the note changed — only what she now knows about the file
    hanging off it. Reusing the cached chunks would lose it forever."""
    app = _notes_is_never_the_real_one
    _carrying(app, "Parking Garages", "ticket.png", "att/1")
    index.build(CountingBrain())          # no text for it yet

    _remember("Bay 4, expires 18:00")
    brain = CountingBrain()
    index.build(brain)
    assert any("Bay 4" in t for t in brain.embedded)
