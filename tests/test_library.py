"""Which notes Notron may read, and which she may file into — chosen once, per note."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from datetime import datetime, timedelta

import pytest

from notron import library, workspace
from notron.notes import Note


def stamp(days_ago: int) -> str:
    """A modification date in the format Apple Notes hands back."""
    return f"{datetime.now() - timedelta(days=days_ago):%A, %d %B %Y at %H:%M:%S}"


def note(id, title="Supps", folder="Notes", days_ago=1) -> Note:
    return Note(id=id, title=title, folder=folder, modified=stamp(days_ago))


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "STATE", tmp_path / "library.json")
    return tmp_path / "library.json"


def test_choices_survive_a_round_trip(state):
    lib = library.Library(homes={"n1"}, ignore={"n2"}, decided={"n1", "n2", "n3"},
                          start_from=datetime(2026, 1, 1))
    library.save(lib)
    back = library.load()
    assert back.homes == {"n1"} and back.ignore == {"n2"} and back.decided == {"n1", "n2", "n3"}
    assert back.start_from == datetime(2026, 1, 1)
    assert back.chosen_at, "saving stamps when the choice was made"


def test_no_file_means_nothing_is_configured(state):
    lib = library.load()
    assert not lib.configured
    assert lib.state_of(note("n1")) == library.READ


def test_an_ignored_note_is_ignored_by_id_whatever_its_name_is(state):
    lib = library.Library(ignore={"n2"})
    assert lib.is_ignored(note("n2", title="Groceries"))
    assert not lib.is_ignored(note("n1", title="Passwords")), "the list is the rule, not the title"


def test_start_from_hides_old_notes_the_user_never_looked_at():
    lib = library.Library(start_from=datetime(2026, 1, 1))
    old = Note("n9", "2019 experiments", "Notes", "Monday, 4 March 2019 at 09:00:00")
    assert lib.is_ignored(old)
    assert not lib.is_ignored(note("n1", days_ago=1))


def test_a_note_the_user_decided_on_is_never_hidden_by_the_year():
    """The year is a bulk convenience; a row the user flipped back wins."""
    lib = library.Library(start_from=datetime(2026, 1, 1), decided={"n9"})
    old = Note("n9", "2019 experiments", "Notes", "Monday, 4 March 2019 at 09:00:00")
    assert not lib.is_ignored(old)
    lib = library.Library(start_from=datetime(2026, 1, 1), homes={"n9"})
    assert not lib.is_ignored(old)


def test_an_unreadable_date_is_read_not_hidden():
    lib = library.Library(start_from=datetime(2026, 1, 1))
    assert not lib.is_ignored(Note("n1", "x", "Notes", "???"))


def test_user_notes_drops_her_own_folder_and_everything_ignored(monkeypatch, state):
    from notron import notes
    monkeypatch.setattr(notes, "list_all_notes", lambda: [
        note("n1"), note("n2", title="Old", days_ago=900),
        note("n3", folder=workspace.FOLDER), note("n4")])
    library.save(library.Library(ignore={"n4"}, start_from=datetime.now() - timedelta(days=400)))
    assert [n.id for n in library.user_notes()] == ["n1"]


def test_parse_start_accepts_a_year_or_a_date():
    assert library.parse_start("2026") == datetime(2026, 1, 1)
    assert library.parse_start("2025-06-15") == datetime(2025, 6, 15)
    with pytest.raises(ValueError):
        library.parse_start("last year")


def test_password_and_private_notes_are_suggested_as_ignore():
    s = {x.note.id: x for x in library.suggest([note("n1", "Passwords"), note("n2", "Journal 2024"),
                                                note("n3", "Groceries")], {})}
    assert s["n1"].state == library.IGNORE and "passwords" in s["n1"].reason
    assert s["n2"].state == library.IGNORE and "private" in s["n2"].reason
    assert s["n3"].state == library.READ


def test_a_recent_list_shaped_note_is_suggested_as_a_home():
    body = "Supps\n" + "\n".join(f"- thing {i}" for i in range(12))
    prose = "Essay\n" + "A long paragraph about something that goes on and on for quite a while. " * 8
    s = {x.note.id: x for x in library.suggest(
        [note("n1", "Supps", days_ago=3), note("n2", "Essay", days_ago=3)],
        {"n1": body, "n2": prose})}
    assert s["n1"].state == library.HOME and s["n1"].reason
    assert s["n2"].state == library.READ


def test_only_the_newest_of_duplicate_titles_can_be_a_home():
    body = "Supps\n" + "\n".join(f"- thing {i}" for i in range(12))
    s = {x.note.id: x for x in library.suggest(
        [note("old", "Supps", days_ago=400), note("new", "Supps", days_ago=2)],
        {"old": body, "new": body})}
    assert s["new"].state == library.HOME
    assert s["old"].state == library.READ and "share this name" in s["old"].reason


def test_homes_are_capped_so_the_screen_opens_with_a_short_list():
    body = "x\n" + "\n".join(f"- thing {i}" for i in range(12))
    many = [note(f"n{i}", f"List {i}", days_ago=1) for i in range(30)]
    out = library.suggest(many, {n.id: body for n in many})
    assert sum(x.state == library.HOME for x in out) == library.MAX_HOMES


def test_scan_reports_suggestions_until_the_user_has_chosen(monkeypatch, state):
    from notron import notes, index
    monkeypatch.setattr(notes, "list_all_notes", lambda: [
        note("n1", "Passwords"), note("n2", "Groceries", days_ago=2), note("n3", "Groceries", days_ago=500),
        note("n4", "x", folder=workspace.FOLDER)])
    monkeypatch.setattr(index, "glimpses", lambda chars=100: {})
    out = library.scan()
    assert out["configured"] is False
    rows = {r["id"]: r for r in out["notes"]}
    assert set(rows) == {"n1", "n2", "n3"}, "her own folder is never listed"
    assert rows["n1"]["state"] == "ignore" and rows["n1"]["suggested"] == "ignore"
    assert out["duplicates"] == {"Groceries": ["n2", "n3"]}
    assert out["counts"] == {"home": 0, "read": 2, "ignore": 1}
    assert [r["state"] for r in out["notes"]] == sorted(
        [r["state"] for r in out["notes"]], key=["home", "read", "ignore"].index), "guess order: homes first"


def test_scan_reports_the_users_choices_once_made(monkeypatch, state):
    from notron import notes, index
    monkeypatch.setattr(notes, "list_all_notes", lambda: [note("n1", "Passwords"), note("n2", "Groceries")])
    monkeypatch.setattr(index, "glimpses", lambda chars=100: {})
    library.save(library.Library(homes={"n1"}, decided={"n1", "n2"}))
    rows = {r["id"]: r for r in library.scan()["notes"]}
    assert rows["n1"]["state"] == "home", "the user's choice, even against the guess"
    assert rows["n1"]["suggested"] == "ignore", "the guess is still shown"


def test_a_note_she_made_after_a_yes_becomes_a_home_only_if_homes_exist(state):
    library.add_home("new")
    assert not library.load().homes, "no setup yet — adding one home would shut every other note out"
    library.save(library.Library(homes={"n1"}))
    library.add_home("new")
    assert library.load().homes == {"n1", "new"}


def _ignoring(monkeypatch, state, *ids):
    library.save(library.Library(ignore=set(ids)))


def test_the_keyword_search_never_opens_an_ignored_note(monkeypatch, state):
    from notron import notes, retrieval
    monkeypatch.setattr(notes, "list_all_notes", lambda: [note("n1", "Groceries"), note("n2", "Groceries")])
    opened = []
    monkeypatch.setattr(notes, "read_body", lambda i: opened.append(i) or "<div>Groceries</div><div>oat milk</div>")
    _ignoring(monkeypatch, state, "n2")
    retrieval.search("groceries oat milk")
    assert opened == ["n1"]


def test_the_index_never_embeds_an_ignored_note(monkeypatch, state):
    from notron import notes, index
    monkeypatch.setattr(notes, "list_all_notes", lambda: [note("n1", "A"), note("n2", "B")])
    monkeypatch.setattr(notes, "read_body", lambda i: f"<div>{i}</div><div>body text</div>")
    monkeypatch.setattr(index, "_load", lambda: {})
    saved = {}
    monkeypatch.setattr(index, "_save", lambda data: saved.update(data))

    class Brain:
        def embed(self, texts): return [[0.0] for _ in texts]

    _ignoring(monkeypatch, state, "n2")
    index.build(Brain())
    assert set(saved) == {"n1"}


def test_a_stale_index_still_hides_an_ignored_note_at_search_time(state):
    """The user ignores a note; the index was built last week. It must not surface."""
    from notron import index
    rows = [{"note_id": "n1", "modified": stamp(1)}, {"note_id": "n2", "modified": stamp(1)}]
    library.save(library.Library(ignore={"n2"}))
    assert [r["note_id"] for r in index._readable(rows)] == ["n1"]


def test_a_tag_inside_an_ignored_note_is_never_answered(monkeypatch, state):
    from notron import mentions
    monkeypatch.setattr(mentions, "STATE", state.parent / "seen.json")
    monkeypatch.setattr(mentions.notes, "list_all_notes", lambda: [note("n1"), note("n2")])
    _ignoring(monkeypatch, state, "n2")
    s = mentions.Scanner()
    assert [n.id for n in s.changed()] == ["n1"]
    assert "n2" in s.seen, "remembered as seen, so un-ignoring later does not replay old tags"


def test_care_counts_only_notes_she_is_allowed_to_read(monkeypatch, state):
    """`care.check()` also reads About Me, Memory, usage and permissions — every
    one of those is stubbed so the test never touches Notes."""
    from notron import care, notes, index, permissions
    monkeypatch.setattr(notes, "list_all_notes", lambda: [note("n1"), note("n2")])
    monkeypatch.setattr(notes, "find_note", lambda folder, title: None)
    monkeypatch.setattr(index, "exists", lambda: False)
    monkeypatch.setattr(permissions, "check", lambda: [])
    monkeypatch.setattr(care, "_usage", lambda days=7: {"calls": 0, "in": 0, "out": 0})
    _ignoring(monkeypatch, state, "n2")
    hit = [s for s in care.check() if s.key == "index"]
    assert hit and "your 1 notes" in hit[0].fact
