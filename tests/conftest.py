"""Tests run with no API key, no network and no real Notes app — always.

Every world question now routes to the researcher by design, so a Tavily key
exported in the developer's shell would quietly turn the graph tests into
live web searches. Strip it before every test; a test that wants a key sets
one itself.

The same went for the Notes app itself, and that one had teeth. Any test that
reached code calling `applescript.run` without patching it first talked to the
developer's own Apple Notes — 358 real notes, read as if they were fixtures.
On 2026-09-03 live work on this machine left fourteen junk `📊 Log` notes in
that library. `_notes_is_never_the_real_one` puts a small fake Notes app under
every test instead, so a forgotten patch produces a deterministic fake library
rather than whatever the person running the suite happens to have written down
— and no test can ever write into it.

EventKit was the same hole, one door along, and open until 2026-09-07: the fake
Notes app never covered `eventkit.run`, which does not go through
`applescript.run` at all. Any test reaching it ran a real `osascript` against
the developer's own 1,263 reminders and 1,757 events. Every EventKit test in
this suite passes its own fake `caller`; `_eventkit_is_never_the_real_one`
makes that a rule rather than a habit.
"""

import pytest

from notron import applescript, attachments, eventkit, mentions, notes, rewrite, undo


@pytest.fixture(autouse=True)
def _no_search_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _undo_state_is_disposable(monkeypatch, tmp_path):
    """Every successful write now saves an undo slot (`Executor._apply`), so any
    test that lets Executor perform a real write — not just tests/test_executor.py,
    also the Filer and action tests — touches `undo.STATE` whether it means to or
    not. Redirect it everywhere, the same way `_no_search_key` strips a live key
    everywhere, so no test run pollutes the real .notron/undo.json with fake
    note ids and fake bodies. A test that specifically exercises undo.py itself
    still overrides this per-test, same as before."""
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")


@pytest.fixture(autouse=True)
def _mentions_state_is_disposable(monkeypatch, tmp_path):
    """A `mentions.Scanner()` constructed without patching `STATE` first and
    then exercised (`.prime()`/`.changed()`/`.scan()`) writes straight to the
    real, live `.notron/seen.json` — the background listener's own "what has
    she already looked at" record. One test that skipped this (predating this
    fixture, not this branch) overwrote the developer's real file with two
    lines of fake test data during this branch's own work — no Notes content
    was lost, but the listener's next restart would otherwise have treated
    every real note as newly changed and rescanned the whole library for old
    `#notron` tags. Same blanket redirect as undo/rewrite above, so no future
    test can do this again by omission."""
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")


@pytest.fixture(autouse=True)
def _rewrite_state_is_disposable(monkeypatch, tmp_path):
    """`nodes.organizer` calls `rewrite.allow(note.id)` when it reads a `yes`
    under its own offer, so a node-level or graph-level test can now write to
    the real .notron/rewrite.json — and a fake note id left in there is a
    standing permission to rewrite a real note in place. Redirect it
    everywhere, same as undo above."""
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")


class FakeNotesApp:
    """An in-memory Notes app, addressed exactly the way the real one is.

    It answers the same scripts `notron/notes.py` sends, so it exercises the
    real index-addressing and name-verification logic rather than stubbing it
    out. `insert_folder` reproduces the one move that broke everything: a new
    folder appearing above an existing one and shifting its index.
    """

    def __init__(self, folders=None):
        self.folders = [(name, list(titles)) for name, titles in (folders or [
            ("Notes", ["Parking Garages", "Supps"]),
            ("Recently Deleted", ["an old thing"]),
            ("🤖 NOTRON", ["📌 About Me", "📥 Ask Notron", "🧠 Brain Dump", "📊 Log"]),
        ])]
        self.bodies: dict[str, str] = {}
        #: note id -> [(attachment name, attachment id)]. Apple Notes keeps
        #: these completely out of the body, so they live beside it here too.
        self.attachments: dict[str, list[tuple[str, str]]] = {}
        #: attachment id -> the bytes Notes would write out on `save`.
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []

    def insert_folder(self, position: int, name: str) -> None:
        self.folders.insert(position - 1, (name, []))

    def run(self, script: str, *args: str, **kw) -> str:
        if script is notes._FOLDER_NAMES:
            self.calls.append("folders")
            return notes.US.join(name for name, _ in self.folders)

        if script is notes._LIST_BY_INDEX:
            index = int(args[0])
            self.calls.append(f"list:{index}")
            if index > len(self.folders):
                raise applescript.AppleScriptError(
                    'Can\'t get folder %d of application "Notes"' % index)
            name, titles = self.folders[index - 1]
            ids = [f"{name}/{t}" for t in titles]
            dates = ["Wednesday, 2 September 2026 at 21:30:00"] * len(titles)
            rest = notes.RS.join(
                (notes.US.join(ids), notes.US.join(titles), notes.US.join(dates)))
            return f"{name}{notes.RS}{rest}"

        if script is notes._BODY:
            self.calls.append("body")
            return self.bodies.get(args[0], f"<div>{args[0].split('/')[-1]}</div>")

        if script is notes._CREATE_AT_INDEX:
            index = int(args[0])
            self.calls.append(f"create:{index}")
            name, titles = self.folders[index - 1]
            title = args[1].splitlines()[0]
            titles.append(title)
            self.bodies[f"{name}/{title}"] = args[1]
            return f"{name}/{title}"

        if script is notes._ENSURE_FOLDER:
            self.calls.append("ensure")
            if any(name == args[0] for name, _ in self.folders):
                return "exists"
            self.folders.append((args[0], []))
            return "created"

        if script is attachments._ON_NOTE:
            self.calls.append("attachments")
            rows = self.attachments.get(args[0], [])
            names = notes.US.join(n for n, _ in rows)
            ids = notes.US.join(i for _, i in rows)
            return f"{names}{notes.RS}{ids}"

        if script is attachments._IN_FOLDER:
            index = int(args[0])
            self.calls.append("in_folder")
            name, titles = self.folders[index - 1]
            rows = []
            for t in titles:
                found = self.attachments.get(f"{name}/{t}", [])
                if not found:
                    continue
                rows.append(attachments.GS.join((
                    f"{name}/{t}",
                    notes.US.join(n for n, _ in found),
                    notes.US.join(i for _, i in found))))
            return notes.RS.join([name, *rows])

        if script is attachments._EXTRACT:
            self.calls.append("extract")
            import pathlib as _pathlib
            _pathlib.Path(args[1]).write_bytes(self.files.get(args[0], b""))
            return "ok"

        # A write, a `show note`, an EventKit JXA script — anything that would
        # have reached the real machine. Loud, with the script in the message,
        # so the test that forgot to patch is obvious from the failure alone.
        raise AssertionError(
            "a test reached the real Notes app — patch it:\n" + script.strip()[:200])


@pytest.fixture(autouse=True)
def _notes_is_never_the_real_one(monkeypatch):
    app = FakeNotesApp()
    monkeypatch.setattr(applescript, "run", app.run)
    monkeypatch.setattr(notes, "run", app.run)
    monkeypatch.setattr(notes, "_folders_cache", None)
    yield app
    notes._folders_cache = None


@pytest.fixture(autouse=True)
def _eventkit_is_never_the_real_one(monkeypatch):
    """No test may reach the real Calendar or Reminders store.

    There is no useful fake here the way there is for Notes — every caller of
    `eventkit.run` already takes a `caller=` for exactly this — so this refuses
    instead, naming the script, the same way the fake Notes app refuses a write.
    """
    def refuse(script, timeout):
        raise AssertionError(
            "a test reached the real Calendar/Reminders store — pass caller=:\n"
            + str(script).strip()[:200])

    # `_osascript`, not `run`: `run` is itself under test, and every caller may
    # legitimately hand it a fake `runner`. This is the one line that actually
    # reaches the machine.
    monkeypatch.setattr(eventkit, "_osascript", refuse)


@pytest.fixture(autouse=True)
def _no_cached_permissions():
    """`permissions.cached()` is module state that outlives a test. One test
    finding Calendar denied must not be why the next test thinks so."""
    from notron import permissions

    permissions.forget()
    yield
    permissions.forget()
