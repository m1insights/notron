"""Addressing a folder by position, safely.

Folders are addressed by index because that is the only fast way (see
`notron/notes.py`), and the list of names behind those indexes is cached for
ten minutes because enumerating folders is the gateway call for everything
else. Both of those are right. Together, unchecked, they were wrong.

Notes keeps folders in alphabetical order. A folder created anywhere above
`🤖 NOTRON` shifts it down one, and for the rest of the cache window the
cached index addresses the folder *next to* the one she asked for. That is
not a slow read or a visible error — it is a different folder's notes handed
back as if they were hers.

It happened on 2026-09-03. An empty second "Notes" folder appeared, pushing
`🤖 NOTRON` from position 5 to 6, and for the next two minutes every read of
her own folder returned **Recently Deleted**. She could not find `📊 Log`
there, so the self-heal that recreates a deleted system note fired on every
single write: fourteen duplicate `📊 Log` notes in the user's Notes app, and
an audit trail scattered across all of them.

Every folder read now checks the name that comes back in the same request.
"""

from __future__ import annotations

import pytest

from notron import notes
from notron.applescript import AppleScriptError


class FakeNotes:
    """An in-memory Notes app, addressed exactly the way the real one is."""

    def __init__(self, folders: list[tuple[str, list[str]]]):
        self.folders = [(name, list(titles)) for name, titles in folders]
        self.calls: list[str] = []

    def insert_folder(self, position: int, name: str) -> None:
        """A folder appears above an existing one — the whole bug in one line."""
        self.folders.insert(position - 1, (name, []))

    def run(self, script: str, *args: str, **kw) -> str:
        if script is notes._FOLDER_NAMES:
            self.calls.append("folders")
            return notes.US.join(name for name, _ in self.folders)

        if script is notes._LIST_BY_INDEX:
            index = int(args[0])
            self.calls.append(f"list:{index}")
            if index > len(self.folders):
                raise AppleScriptError("Can't get folder 9 of application \"Notes\"")
            name, titles = self.folders[index - 1]
            ids = [f"{name}/{t}" for t in titles]
            dates = ["Wednesday, 2 September 2026 at 21:30:00"] * len(titles)
            body = notes.RS.join(
                (notes.US.join(ids), notes.US.join(titles), notes.US.join(dates))
            )
            return f"{name}{notes.RS}{body}"

        if script is notes._CREATE_AT_INDEX:
            index = int(args[0])
            self.calls.append(f"create:{index}")
            name, titles = self.folders[index - 1]
            title = args[1].splitlines()[0]
            titles.append(title)
            return f"{name}/{title}"

        if script is notes._ENSURE_FOLDER:
            self.calls.append("ensure")
            if any(name == args[0] for name, _ in self.folders):
                return "exists"
            self.folders.append((args[0], []))
            return "created"

        raise AssertionError(f"unexpected script: {script[:60]}")


@pytest.fixture(autouse=True)
def _no_cached_folders():
    notes._folders_cache = None
    yield
    notes._folders_cache = None


def _library() -> FakeNotes:
    return FakeNotes([
        ("AI Sandbox", ["Prompts"]),
        ("Notes", ["Parking Garages"]),
        ("Recently Deleted", ["an old thing"]),
        ("🤖 NOTRON", ["📊 Log", "📥 Ask Notron"]),
    ])


def test_a_folder_that_appears_above_hers_never_makes_her_read_another_one(monkeypatch):
    """The 2026-09-03 bug. A second "Notes" folder shifted 🤖 NOTRON down one,
    and the cached index then pointed at Recently Deleted."""
    fake = _library()
    monkeypatch.setattr(notes, "run", fake.run)

    assert [n.title for n in notes.list_notes("🤖 NOTRON")] == ["📊 Log", "📥 Ask Notron"]

    fake.insert_folder(3, "Notes")  # 🤖 NOTRON is now position 5, cache still says 4

    titles = [n.title for n in notes.list_notes("🤖 NOTRON")]
    assert titles == ["📊 Log", "📥 Ask Notron"], "she read the folder next to hers"
    assert all(n.folder == "🤖 NOTRON" for n in notes.list_notes("🤖 NOTRON"))


def test_a_folder_that_is_gone_reads_as_empty_not_as_its_neighbour(monkeypatch):
    fake = _library()
    monkeypatch.setattr(notes, "run", fake.run)
    notes.list_notes("🤖 NOTRON")

    fake.folders = [f for f in fake.folders if f[0] != "🤖 NOTRON"]

    assert notes.list_notes("🤖 NOTRON") == []


def test_an_empty_answer_is_never_cached_as_the_folder_list(monkeypatch):
    """One timed-out request used to poison every folder lookup for ten
    minutes, because [] was cached as happily as a real answer."""
    answers = ["", "AI Sandbox"]
    monkeypatch.setattr(notes, "run", lambda script, *a, **kw: answers.pop(0))

    assert notes.folders() == []
    assert notes.folders() == ["AI Sandbox"], "the empty answer stuck"


def test_a_note_is_never_created_by_inventing_a_second_folder(monkeypatch):
    """`create_note` used to walk folders by name in AppleScript and quietly
    `make new folder` when nothing matched — so one unmatched name (a typo, a
    rename, a stray space) produced a duplicate folder, which is what shifted
    every index in the first place. Only `ensure_folder` creates folders."""
    fake = _library()
    monkeypatch.setattr(notes, "run", fake.run)

    with pytest.raises(notes.FolderMissing):
        notes.create_note("Notez", "<div>Parking Garages</div>")

    assert [name for name, _ in fake.folders].count("Notez") == 0


def test_a_new_note_lands_in_the_folder_reads_come_from(monkeypatch):
    """Reads and writes resolve the folder the same way, so they can never
    disagree about which of two same-named folders is the real one."""
    fake = _library()
    monkeypatch.setattr(notes, "run", fake.run)
    notes.list_notes("🤖 NOTRON")

    fake.insert_folder(2, "App Dev")

    notes.create_note("🤖 NOTRON", "📖 Lessons\nsomething learned")
    assert "📖 Lessons" in [n.title for n in notes.list_notes("🤖 NOTRON")]
    assert "📖 Lessons" not in dict(fake.folders)["App Dev"]


def test_a_full_sweep_never_reads_recently_deleted(monkeypatch):
    """`list_all_notes` skipped by cached name, not by the name that came
    back — so a stale index meant deleted notes were swept for #notron tags
    and fed to the index."""
    fake = _library()
    monkeypatch.setattr(notes, "run", fake.run)
    notes.folders()

    fake.insert_folder(1, "Archive")

    swept = [n.title for n in notes.list_all_notes()]
    assert "an old thing" not in swept
    assert "📊 Log" in swept


def test_creating_a_folder_refreshes_the_index_immediately(monkeypatch):
    fake = _library()
    monkeypatch.setattr(notes, "run", fake.run)
    notes.folders()

    notes.ensure_folder("🤖 NOTRON")  # exists — nothing moved, nothing to refresh
    assert fake.calls.count("folders") == 1

    notes.ensure_folder("Shared")
    assert fake.calls.count("folders") == 2, "a new folder shifts every index after it"
    assert "Shared" in notes.folders()
    assert fake.calls.count("folders") == 2, "and the refreshed list is the cached one"
