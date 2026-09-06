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


# ------------------------------------------------- pulling the file out (B)

@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(attachments, "CACHE", tmp_path / "attachments")
    return tmp_path / "attachments"


def test_a_file_comes_out_of_notes_onto_disk(_notes_is_never_the_real_one, cache):
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("pasta.png", "att/2")]
    app.files["att/2"] = b"\x89PNG pretend"

    att = attachments.on_note("Notes/Recipes")[0]
    path = attachments.fetch(att)
    assert path.read_bytes() == b"\x89PNG pretend"
    assert path.suffix == ".png"


def test_notes_is_asked_for_the_same_file_only_once(_notes_is_never_the_real_one, cache):
    """An attachment does not change. A note asked about twice should not pay
    Notes twice — every request to it is a request nothing else can make."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("pasta.png", "att/2")]
    app.files["att/2"] = b"bytes"

    att = attachments.on_note("Notes/Recipes")[0]
    attachments.fetch(att)
    attachments.fetch(att)
    assert app.calls.count("extract") == 1


def test_a_note_ignored_since_the_list_was_made_is_not_pulled(
        _notes_is_never_the_real_one, cache):
    """`index.search` re-checks the ignore list at query time because the index
    can be older than the choice. An Attachment handed around is older than the
    choice in exactly the same way."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Private"] = [("passport.png", "att/9")]
    app.files["att/9"] = b"bytes"

    att = attachments.on_note("Notes/Private")[0]
    library.save(library.Library(ignore={"Notes/Private"}))

    with pytest.raises(attachments.NotAllowed):
        attachments.fetch(att)
    assert "extract" not in app.calls


def test_a_text_file_comes_back_as_text(_notes_is_never_the_real_one, cache):
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("log.txt", "att/3")]
    app.files["att/3"] = "line one\nline two\n".encode()

    att = attachments.on_note("Notes/Recipes")[0]
    assert attachments.read_text(att) == "line one\nline two\n"


def test_a_dropped_in_file_is_exactly_where_a_key_arrives(_notes_is_never_the_real_one, cache):
    """A .env or a diagnostics dump is the normal way a secret enters a note
    that holds no secrets at all. Redaction on the way in is not optional."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("diagnostics.txt", "att/4")]
    app.files["att/4"] = b"host: prod\nAPI_KEY = sk-abcdefghijklmnopqrstuvwx\n"

    att = attachments.on_note("Notes/Recipes")[0]
    out = attachments.read_text(att)
    assert "sk-abcdefghijklmnopqrstuvwx" not in out
    assert "host: prod" in out


def test_a_file_named_like_a_password_note_is_masked_value_by_value(
        _notes_is_never_the_real_one, cache):
    """`passwords.txt` is `privacy.is_vault` by title. Inside a file that exists
    to hold credentials every value is one, and none of them carry a keyword."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("passwords.txt", "att/5")]
    app.files["att/5"] = b"gmail: hunter2\nbank: swordfish\n"

    att = attachments.on_note("Notes/Recipes")[0]
    out = attachments.read_text(att)
    assert "hunter2" not in out and "swordfish" not in out
    assert "gmail" in out          # she can still say which accounts exist


def test_a_huge_file_keeps_its_head_and_its_tail(_notes_is_never_the_real_one, cache):
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("big.txt", "att/6")]
    app.files["att/6"] = (b"START" + b"x" * 60_000 + b"END")

    att = attachments.on_note("Notes/Recipes")[0]
    out = attachments.read_text(att)
    assert out.startswith("START") and out.endswith("END")
    assert len(out) < 30_000
    assert attachments.ELIDED.strip() in out


# ------------------------------------------------------- looking at it (C)

class LookingBrain:
    """Answers about a picture, and counts how many times it was asked."""

    def __init__(self, answer="Text: BUY MILK. A whiteboard, three columns."):
        self.answer = answer
        self.seen = []

    def see(self, **kw):
        self.seen.append(kw)
        return self.answer


def test_she_reads_the_words_in_a_picture(_notes_is_never_the_real_one, cache, monkeypatch):
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("board.png", "att/7")]
    app.files["att/7"] = b"\x89PNG pretend"
    monkeypatch.setattr(attachments, "downscale", lambda p: p)

    att = attachments.on_note("Notes/Recipes")[0]
    brain = LookingBrain()
    assert "BUY MILK" in attachments.describe(att, brain)
    assert brain.seen[0]["mime"] == "image/png"
    assert brain.seen[0]["image"] == b"\x89PNG pretend"


def test_a_picture_asked_about_twice_costs_one_look(
        _notes_is_never_the_real_one, cache, monkeypatch):
    """The description is cached beside the file. A vision call is the most
    expensive thing in this module; paying for it twice buys nothing."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("board.png", "att/7")]
    app.files["att/7"] = b"png"
    monkeypatch.setattr(attachments, "downscale", lambda p: p)

    att = attachments.on_note("Notes/Recipes")[0]
    brain = LookingBrain()
    attachments.describe(att, brain)
    attachments.describe(att, brain)
    assert len(brain.seen) == 1


def test_a_password_in_a_photo_is_redacted_once_it_becomes_words(
        _notes_is_never_the_real_one, cache, monkeypatch):
    """`privacy.py` only ever sees text, so a photo of a password is the one
    thing it cannot catch. The moment the model reads it out it *is* text —
    but only if the scan is actually run on what the model said."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("screenshot.png", "att/8")]
    app.files["att/8"] = b"png"
    monkeypatch.setattr(attachments, "downscale", lambda p: p)

    att = attachments.on_note("Notes/Recipes")[0]
    out = attachments.describe(att, LookingBrain("The screen reads password: hunter2"))
    assert "hunter2" not in out
    assert "password" in out.lower()


def test_a_heic_is_converted_because_the_endpoint_speaks_png_and_jpeg(
        _notes_is_never_the_real_one, cache, monkeypatch):
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("photo.heic", "att/9")]
    app.files["att/9"] = b"heic"
    ran = []
    monkeypatch.setattr(attachments.subprocess, "run",
                        lambda cmd, **kw: ran.append(cmd) or _touch(cmd[-1]))

    att = attachments.on_note("Notes/Recipes")[0]
    brain = LookingBrain()
    attachments.describe(att, brain)
    assert "png" in " ".join(ran[0])
    assert brain.seen[0]["mime"] == "image/png"


def _touch(path):
    import pathlib as _p
    _p.Path(path).write_bytes(b"converted")
    return type("Done", (), {"returncode": 0})()


# ------------------------------------------------------------ listening (D)

def test_she_transcribes_a_voice_memo(_notes_is_never_the_real_one, cache, monkeypatch):
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("memo.m4a", "att/10")]
    app.files["att/10"] = b"m4a"
    monkeypatch.setattr(attachments, "_listen", lambda path: "pay the plumber on Friday")

    att = attachments.on_note("Notes/Recipes")[0]
    assert attachments.transcribe(att) == "pay the plumber on Friday"


def test_a_memo_played_twice_is_listened_to_once(_notes_is_never_the_real_one, cache, monkeypatch):
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("memo.m4a", "att/10")]
    app.files["att/10"] = b"m4a"
    heard = []
    monkeypatch.setattr(attachments, "_listen",
                        lambda path: heard.append(path) or "pay the plumber")

    att = attachments.on_note("Notes/Recipes")[0]
    attachments.transcribe(att)
    attachments.transcribe(att)
    assert len(heard) == 1


def test_a_password_read_aloud_is_redacted(_notes_is_never_the_real_one, cache, monkeypatch):
    """Someone reads a code into their phone sooner or later. Once it is words
    it is `privacy.py`'s problem again, which only works if the scan is run."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("memo.m4a", "att/10")]
    app.files["att/10"] = b"m4a"
    monkeypatch.setattr(attachments, "_listen", lambda path: "the password is hunter2 ok")

    att = attachments.on_note("Notes/Recipes")[0]
    assert "hunter2" not in attachments.transcribe(att)


def test_a_recording_with_no_speech_in_it_is_not_cached_as_an_empty_answer(
        _notes_is_never_the_real_one, cache, monkeypatch):
    """The real `recording.m4a` in the developer's library answers "No speech
    detected". Caching that as the transcript means she can never try again
    after a better recogniser, and reads as if she listened and heard nothing."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [("memo.m4a", "att/10")]
    app.files["att/10"] = b"m4a"
    monkeypatch.setattr(attachments, "_listen", lambda path: "")

    att = attachments.on_note("Notes/Recipes")[0]
    assert attachments.transcribe(att) == ""
    assert not (attachments.CACHE / "att-10.m4a.txt").exists()
