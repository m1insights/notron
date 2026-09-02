# "Your notes" setup — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The design it implements is `docs/plans/2026-09-01-your-notes-setup-design.md` — read it first (5 min). Tests run with no API key and no network; never run a Notes-touching command while the background listener is mid-request (see CLAUDE.md).

**Goal:** the user tells Notron, once per note, whether it is a **home** (she may file lines into it), **read only** (she may read it to answer questions — the default), or **ignore** (she never reads it), plus a "start from [year]" bulk cutoff — chosen in a Mac screen that pre-fills every choice and can be reopened any time.

**Architecture:** one new Python module `notron/library.py` owns the choices (`.notron/library.json`, keyed by note id) and is the *only* place the "never reads it" rule lives: every reader in the core (`index`, `retrieval`, `mentions`, `care`, `filer`) lists notes through `library.user_notes()`. The Filer's destinations become the homes. The Mac app (SwiftUI, `mac/`) gets a "Your notes" window that asks the core for the list (`notron library scan`, JSON on stdout), lets the user flip a three-way control per row, and writes the JSON file the core reads. No model call anywhere in this feature.

**Tech Stack:** Python 3.11 (plain functions + dataclasses, pytest, monkeypatched Notes), SwiftUI on macOS 14 (`mac/Package.swift`, tokens from `mac/Sources/Notron/DesignSystem.swift` — never add a colour/size/radius not in that file), the existing subprocess bridge pattern from `AskNotronIntent.swift`.

**Run everything from `apps/juno`:** `.venv/bin/python -m pytest tests -q` (236 tests green at the start), `cd mac && swift build` for the app.

---

## The file both sides share — the contract

`.notron/library.json` (written by the Mac app or `notron library`, read by the core):

```json
{
  "homes":      ["x-coredata://…/ICNote/p123", "…"],
  "ignore":     ["x-coredata://…/ICNote/p77"],
  "decided":    ["…every note id the user saw when they pressed Done…"],
  "start_from": "2026-01-01",
  "chosen_at":  "2026-09-01T21:40"
}
```

Semantics, in order:
1. id in `ignore` → ignored.
2. id in `homes` or `decided` → not ignored by date (per-row choice wins over the year).
3. otherwise, if `start_from` is set and the note was last edited before it → ignored.

`homes` empty means "no setup yet": the Filer keeps today's heuristic (every readable note is a candidate).

---

### Task 1: `library.py` — the choices, loaded and applied

**Files:**
- Create: `notron/library.py`
- Test: `tests/test_library.py`

**Step 1: Write the failing tests**

```python
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
```

**Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_library.py -q`
Expected: `ImportError: cannot import name 'library'`

**Step 3: Write the module**

```python
"""Which of the user's notes Notron may read, and which she may file into.

A notes library is years deep: three notes called "Supps", a password note in
the main list, experiments from 2019. Two things go wrong without a say-so —
she files a line into the wrong-but-similar note (a THC log landed in an App
Store listing whose title said "supplements"), and she reads something that was
never meant for a model. So the user tells her, once, per note:

  home     a note she may file lines into — the Filer's only destinations
  read     the default: she may read it to answer questions, never file into it
  ignore   she never reads it — not for filing, not for questions, not even a
           tag inside it

Choices live in `.notron/library.json`, keyed by note id, so a rename changes
nothing. The Mac app writes the file; every reader in the core lists notes
through `user_notes()` here, so "never reads it" is one rule in one place.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime

from . import notes, workspace

STATE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "library.json"

HOME, READ, IGNORE = "home", "read", "ignore"


@dataclass
class Library:
    homes: set[str] = field(default_factory=set)
    ignore: set[str] = field(default_factory=set)
    #: Every note the user had in front of them when they pressed Done. A note
    #: they decided on is governed by that decision, never by the year cutoff.
    decided: set[str] = field(default_factory=set)
    start_from: datetime | None = None
    chosen_at: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.homes or self.ignore or self.decided or self.start_from)

    def hides(self, note_id: str, modified: str) -> bool:
        """The one rule. Explicit choices first, the year only for notes the
        user never looked at, and an unreadable date is read rather than hidden."""
        if note_id in self.ignore:
            return True
        if note_id in self.homes or note_id in self.decided or self.start_from is None:
            return False
        at = notes.Note(note_id, "", "", modified).modified_at
        return at is not None and at < self.start_from

    def is_ignored(self, note: notes.Note) -> bool:
        return self.hides(note.id, note.modified)

    def state_of(self, note: notes.Note) -> str:
        if self.is_ignored(note):
            return IGNORE
        return HOME if note.id in self.homes else READ


def parse_start(text: str) -> datetime:
    """'2026' or '2026-06-15'. Anything else is a ValueError, said plainly."""
    text = text.strip()
    for fmt in ("%Y", "%Y-%m", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"couldn't read {text!r} as a year or a date — try 2026 or 2026-06-15")


def load() -> Library:
    try:
        raw = json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return Library()
    start = raw.get("start_from")
    try:
        start_at = parse_start(start) if start else None
    except ValueError:
        start_at = None
    return Library(
        homes=set(raw.get("homes") or []),
        ignore=set(raw.get("ignore") or []),
        decided=set(raw.get("decided") or []),
        start_from=start_at,
        chosen_at=raw.get("chosen_at") or "",
    )


def save(lib: Library) -> None:
    lib.chosen_at = lib.chosen_at or datetime.now().isoformat(timespec="minutes")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "homes": sorted(lib.homes),
        "ignore": sorted(lib.ignore),
        "decided": sorted(lib.decided),
        "start_from": lib.start_from.strftime("%Y-%m-%d") if lib.start_from else None,
        "chosen_at": lib.chosen_at,
    }, indent=1))


def user_notes(lib: Library | None = None) -> list[notes.Note]:
    """Every note of theirs she is allowed to read. The only way the core
    should ever list the user's notes."""
    lib = lib or load()
    return [n for n in notes.list_all_notes()
            if n.folder != workspace.FOLDER and not lib.is_ignored(n)]
```

**Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_library.py -q`
Expected: `8 passed`

**Step 5: Commit**

```bash
git add notron/library.py tests/test_library.py
git commit -m "feat: library — which notes she may read, which she may file into"
```

---

### Task 2: the pre-fill — `library.suggest()`

Plain code, no model. Becky unticks; she never audits 300 notes from zero.

**Files:**
- Modify: `notron/library.py` (append)
- Test: `tests/test_library.py` (append)

**Step 1: Write the failing tests**

```python
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
```

**Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_library.py -q`
Expected: 4 failures, `AttributeError: module 'notron.library' has no attribute 'suggest'`

**Step 3: Append the implementation to `notron/library.py`**

```python
# ------------------------------------------------------------- the pre-fill

import re as _re

from . import privacy

#: How many homes the screen opens with. A short list gets unticked; a long
#: one gets ignored.
MAX_HOMES = 10
HOME_SCORE = 3


@dataclass(frozen=True)
class Suggestion:
    note: notes.Note
    state: str
    reason: str          # shown under the row — why she guessed this


def _recency(note: notes.Note, now: datetime) -> int:
    at = note.modified_at
    if at is None:
        return 0
    days = (now - at).days
    return 2 if days <= 60 else 1 if days <= 365 else 0


def _list_shaped(text: str) -> int:
    """A note people keep things in is many short lines, not paragraphs."""
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < 6:
        return 0
    short = sum(len(l) <= 60 for l in lines) / len(lines)
    return 2 if short >= 0.6 else 0


def _title_like_a_place(title: str) -> int:
    return 1 if len(title) <= 40 and not _re.search(r"[.!?]", title) else 0


def suggest(all_notes: list[notes.Note], texts: dict[str, str], *,
            now: datetime | None = None) -> list[Suggestion]:
    """Notron's guess for every note. Plain code — no model reads anything here.

    `texts` is the opening of each note by id (from the index; empty when the
    user has not run `notron index` yet, in which case shape is unknown and
    only recency and the title count)."""
    now = now or datetime.now()
    by_title: dict[str, list[notes.Note]] = {}
    for n in all_notes:
        by_title.setdefault(n.title, []).append(n)

    scored: list[tuple[int, notes.Note, str]] = []
    out: dict[str, Suggestion] = {}
    for n in all_notes:
        if privacy.is_vault(n.title):
            out[n.id] = Suggestion(n, IGNORE, "looks like passwords")
            continue
        if privacy.is_private(n.title):
            out[n.id] = Suggestion(n, IGNORE, "looks private")
            continue
        twins = by_title[n.title]
        newest = max(twins, key=lambda t: t.modified_at or datetime.min)
        if len(twins) > 1 and n is not newest:
            out[n.id] = Suggestion(n, READ, f"{len(twins)} notes share this name")
            continue
        score = _recency(n, now) + _list_shaped(texts.get(n.id, "")) + _title_like_a_place(n.title)
        why = ", ".join(w for w, on in (("edited recently", _recency(n, now) == 2),
                                        ("list-shaped", _list_shaped(texts.get(n.id, "")) > 0)) if on)
        scored.append((score, n, why))

    scored.sort(key=lambda s: (-s[0], -(s[1].modified_at or datetime.min).timestamp()))
    homes = 0
    for score, n, why in scored:
        if score >= HOME_SCORE and homes < MAX_HOMES:
            out[n.id] = Suggestion(n, HOME, why)
            homes += 1
        else:
            out[n.id] = Suggestion(n, READ, "")
    return [out[n.id] for n in all_notes]
```

**Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_library.py -q`
Expected: `12 passed`

**Step 5: Commit**

```bash
git add notron/library.py tests/test_library.py
git commit -m "feat: library.suggest — pre-fill homes and ignores in plain code"
```

---

### Task 3: `library.scan()` for the GUI, and `add_home()`

**Files:**
- Modify: `notron/library.py` (append)
- Modify: `notron/index.py` — nothing; `index.glimpses(chars)` already exists (added with the Filer)
- Test: `tests/test_library.py` (append)

**Step 1: Write the failing tests**

```python
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
```

**Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_library.py -q`
Expected: 3 failures, `no attribute 'scan'` / `'add_home'`

**Step 3: Append to `notron/library.py`**

```python
# ------------------------------------------------------------- for the GUI

_ORDER = {HOME: 0, READ: 1, IGNORE: 2}


def scan(lib: Library | None = None) -> dict:
    """Every note with its state, for the "Your notes" screen and `notron library`.

    Before the user has chosen, `state` is the guess; after, it is their choice,
    with the guess still alongside so the screen can say why."""
    from . import index

    lib = lib or load()
    live = [n for n in notes.list_all_notes() if n.folder != workspace.FOLDER]
    guesses = {s.note.id: s for s in suggest(live, index.glimpses(1400))}
    rows = []
    for n in live:
        g = guesses[n.id]
        rows.append({
            "id": n.id, "title": n.title, "folder": n.folder, "modified": n.modified,
            "state": lib.state_of(n) if lib.configured else g.state,
            "suggested": g.state, "reason": g.reason,
        })
    rows.sort(key=lambda r: (_ORDER[r["state"]], -(_stamp(r["modified"]))))
    by_title: dict[str, list[str]] = {}
    for r in rows:
        by_title.setdefault(r["title"], []).append(r["id"])
    counts = {s: sum(r["state"] == s for r in rows) for s in (HOME, READ, IGNORE)}
    return {
        "notes": rows,
        "duplicates": {t: ids for t, ids in by_title.items() if len(ids) > 1},
        "start_from": lib.start_from.strftime("%Y-%m-%d") if lib.start_from else None,
        "configured": lib.configured,
        "counts": counts,
    }


def _stamp(modified: str) -> float:
    at = notes.Note("", "", "", modified).modified_at
    return at.timestamp() if at else 0.0


def add_home(note_id: str) -> None:
    """A note Notron created after a `yes` in the Brain Dump is a home from
    then on — but only once the user has chosen homes at all. With none
    chosen, every readable note is a destination, and adding one would
    silently shut the rest out."""
    lib = load()
    if not lib.homes or note_id in lib.homes:
        return
    lib.homes.add(note_id)
    save(lib)
```

**Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_library.py -q`
Expected: `15 passed`

**Step 5: Commit**

```bash
git add notron/library.py tests/test_library.py
git commit -m "feat: library.scan for the Your-notes screen; add_home after a yes"
```

---

### Task 4: "never reads it" — route every reader through `library.user_notes()`

Five places list the user's notes today with `[n for n in notes.list_all_notes() if n.folder != workspace.FOLDER]`. Each becomes one call.

**Files:**
- Modify: `notron/index.py` (`build`, `search`)
- Modify: `notron/retrieval.py` (`search`)
- Modify: `notron/mentions.py` (`Scanner.changed`)
- Modify: `notron/care.py` (the "Notes she has never read" block)
- Test: `tests/test_library.py` (append)

**Step 1: Write the failing tests**

```python
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
```

**Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_library.py -q`
Expected: 5 failures (`opened == ["n1", "n2"]`, `set(saved) == {"n1","n2"}`, `no attribute '_readable'`, `"n2"` reported by the scanner, `your 2 notes`)

**Step 3: Make the five edits**

`notron/index.py` — in `build`, replace

```python
    live = [n for n in notes.list_all_notes() if n.folder != workspace.FOLDER]
```
with
```python
    from . import library

    live = library.user_notes()
```
and in `search`, replace
```python
    rows = [r for chunks in data.values() for r in chunks if r.get("row") is not None]
```
with
```python
    rows = _readable([r for chunks in data.values() for r in chunks if r.get("row") is not None])
```
and add, above `def exists()`:
```python
def _readable(rows: list[dict]) -> list[dict]:
    """The index can be a week older than the user's choices. A note they have
    since ignored must not surface just because it was embedded earlier."""
    from . import library

    lib = library.load()
    return [r for r in rows if not lib.hides(r["note_id"], r.get("modified", ""))]
```

`notron/retrieval.py` — replace
```python
    candidates = [
        n for n in notes.list_all_notes()
        if n.folder != workspace.FOLDER
    ]
```
with
```python
    from . import library

    candidates = library.user_notes()
```

`notron/mentions.py` — in `changed`, replace
```python
        out = []
        for n in notes.list_all_notes():
            if n.folder == workspace.FOLDER:
                self.seen[n.id] = n.modified
                continue
```
with
```python
        from . import library

        lib = library.load()
        out = []
        for n in notes.list_all_notes():
            if n.folder == workspace.FOLDER or lib.is_ignored(n):
                # Hers, or the user's business: remembered as seen, never read.
                # Un-ignoring later then means "read it from now", not "answer
                # every tag it ever held".
                self.seen[n.id] = n.modified
                continue
```

`notron/care.py` — replace
```python
    live = [x for x in notes.list_all_notes() if x.folder != workspace.FOLDER]
```
with
```python
    from . import library

    live = library.user_notes()
```

Remove any `workspace` import that becomes unused in those files only if the linter/tests complain; otherwise leave imports alone.

**Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all green (`256 passed`, ±2 depending on the care assertion)

**Step 5: Commit**

```bash
git add notron/index.py notron/retrieval.py notron/mentions.py notron/care.py tests/test_library.py
git commit -m "feat: an ignored note is never read — index, search, tag sweep, care all go through library"
```

---

### Task 5: the Filer files only into homes

**Files:**
- Modify: `notron/filer.py` (`masters`, `_approve`)
- Test: `tests/test_filer.py` (append)

**Step 1: Write the failing tests** (append to `tests/test_filer.py`; the `store` fixture and `FilerBrain` are already there)

```python
def test_with_homes_chosen_only_homes_are_destinations(store, monkeypatch):
    from notron import library
    monkeypatch.setattr(library, "STATE", filer.STATE.parent / "library.json")
    sup = store.add("Supps", "-")
    store.add("Track which supplements actually improve your sleep…", "App Store listing")
    library.save(library.Library(homes={sup}))
    titles = [m.title for m in filer.masters()]
    assert titles == ["Supps"], "the listing note is readable, but never a place to file"


def test_with_no_homes_chosen_every_readable_note_is_still_a_candidate(store, monkeypatch):
    from notron import library
    monkeypatch.setattr(library, "STATE", filer.STATE.parent / "library.json")
    store.add("Supps", "-")
    store.add("Groceries", "-")
    assert sorted(m.title for m in filer.masters()) == ["Groceries", "Supps"]


def test_a_note_made_after_a_yes_joins_the_homes(store, monkeypatch):
    from notron import library
    monkeypatch.setattr(library, "STATE", filer.STATE.parent / "library.json")
    sup = store.add("Supps", "-")
    library.save(library.Library(homes={sup}))
    dump(store, "the serum from that brand\n")
    brain = FilerBrain({"the serum from that brand": {"new": "Skincare Brand"}})
    filer.run(brain)
    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>yes</div>"
    filer.run(brain)
    made = store.find_note(filer.FILING_FOLDER, "Skincare Brand").id
    assert made in library.load().homes
```

**Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_filer.py -q -k "homes or joins"`
Expected: first and third fail; second passes already

**Step 3: Edit `notron/filer.py`**

In `masters`, replace
```python
    for n in notes.list_all_notes():
        if n.folder == workspace.FOLDER or not n.title.strip() or n.title in exclude:
            continue
```
with
```python
    from . import library

    lib = library.load()
    for n in library.user_notes(lib):
        if lib.homes and n.id not in lib.homes:
            continue                       # the user said where things go
        if not n.title.strip() or n.title in exclude:
            continue
```
and update its docstring's first sentence to: `"""The notes lines may be filed into: the user's chosen homes, or — before any are chosen — every note of theirs she may read, newest first, …`.

In `_approve`, right after
```python
            out.created.append(title)
```
add
```python
            if r.note_id:
                from . import library
                library.add_home(r.note_id)
```

**Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all green

**Step 5: Commit**

```bash
git add notron/filer.py tests/test_filer.py
git commit -m "feat: the Filer files only into chosen homes; a note made after a yes becomes one"
```

---

### Task 6: `notron library` on the command line

`notron library` shows the state; `notron library scan` prints the JSON the Mac app reads; flags set choices by title (or by id when titles collide).

**Files:**
- Modify: `notron/cli.py`
- Test: `tests/test_library.py` (append)

**Step 1: Write the failing tests**

```python
def test_the_cli_sets_a_home_by_title_and_refuses_an_ambiguous_one(monkeypatch, state, capsys):
    from notron import cli, notes, index
    monkeypatch.setattr(notes, "list_all_notes", lambda: [
        note("n1", "Supps", days_ago=1), note("n2", "Supps", days_ago=300), note("n3", "Groceries")])
    monkeypatch.setattr(index, "glimpses", lambda chars=100: {})
    cli.main(["library", "--home", "Groceries", "--start-from", "2026"])
    lib = library.load()
    assert lib.homes == {"n3"} and lib.start_from == datetime(2026, 1, 1)
    with pytest.raises(SystemExit):
        cli.main(["library", "--home", "Supps"])
    assert "2 notes are called" in capsys.readouterr().out
    cli.main(["library", "--home", "n1"])          # an id always works
    assert library.load().homes == {"n3", "n1"}


def test_the_cli_scan_prints_json_for_the_app(monkeypatch, state, capsys):
    import json
    from notron import cli, notes, index
    monkeypatch.setattr(notes, "list_all_notes", lambda: [note("n1", "Groceries")])
    monkeypatch.setattr(index, "glimpses", lambda chars=100: {})
    cli.main(["library", "scan"])
    out = json.loads(capsys.readouterr().out)
    assert out["notes"][0]["title"] == "Groceries" and "counts" in out
```

**Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_library.py -q -k cli`
Expected: `SystemExit: 2` (argparse: invalid choice 'library')

**Step 3: Add the command to `notron/cli.py`**

Above `def cmd_models(args):` add:

```python
def cmd_library(args):
    """Which notes she may file into, and which she never reads."""
    import json
    from datetime import datetime

    from . import library, notes

    if args.action == "scan":
        print(json.dumps(library.scan()))
        return

    if args.reset:
        library.save(library.Library())
        print("\n  Forgotten — every note is read only again, and the Filer may use any of them.\n")
        return

    lib = library.load()
    live: list | None = None

    def resolve(ref: str) -> str:
        nonlocal live
        if live is None:
            live = [n for n in notes.list_all_notes() if n.folder != workspace.FOLDER]
        if any(n.id == ref for n in live):
            return ref
        hits = [n for n in live if n.title == ref]
        if len(hits) == 1:
            return hits[0].id
        if not hits:
            print(f"\n  No note called {ref!r}.\n")
            raise SystemExit(1)
        print(f"\n  {len(hits)} notes are called {ref!r} — say which by id:")
        for n in hits:
            print(f"    {n.id}   ({n.folder}, edited {n.modified})")
        print()
        raise SystemExit(1)

    changed = False
    for ref in args.home:
        nid = resolve(ref); lib.homes.add(nid); lib.ignore.discard(nid); changed = True
    for ref in args.ignore:
        nid = resolve(ref); lib.ignore.add(nid); lib.homes.discard(nid); changed = True
    for ref in args.read:
        nid = resolve(ref); lib.homes.discard(nid); lib.ignore.discard(nid); changed = True
    if args.start_from:
        try:
            lib.start_from = library.parse_start(args.start_from)
        except ValueError as e:
            print(f"\n  {e}\n"); raise SystemExit(1)
        changed = True
    if changed:
        lib.chosen_at = datetime.now().isoformat(timespec="minutes")
        library.save(lib)

    out = library.scan(lib)
    c = out["counts"]
    since = f" · start from {out['start_from']}" if out["start_from"] else ""
    print(f"\n  {c['home']} homes · {c['read']} read only · {c['ignore']} ignored{since}")
    if not out["configured"]:
        print("  (nothing chosen yet — these are her guesses; open the app or pass --home/--ignore)")
    print()
    for row in out["notes"]:
        if row["state"] == "home":
            print(f"  ⌂ {row['title']}")
    print()
```

In `main`, after the `file` subparser block add:

```python
    lb = sub.add_parser("library", help="which notes she may file into, and which she never reads")
    lb.add_argument("action", nargs="?", choices=["show", "scan"], default="show",
                    help="scan = JSON for the Mac app")
    lb.add_argument("--home", action="append", default=[], metavar="TITLE_OR_ID",
                    help="a note she may file lines into (repeatable)")
    lb.add_argument("--ignore", action="append", default=[], metavar="TITLE_OR_ID",
                    help="a note she must never read (repeatable)")
    lb.add_argument("--read", action="append", default=[], metavar="TITLE_OR_ID",
                    help="back to the default: readable, never filed into")
    lb.add_argument("--start-from", metavar="YEAR", help="ignore notes last edited before this, e.g. 2026")
    lb.add_argument("--reset", action="store_true", help="forget every choice")
    lb.set_defaults(fn=cmd_library)
```

**Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all green

**Step 5: Try it for real (the listener is running; this queues behind its lock, ~2 s)**

Run: `.venv/bin/python -m notron library`
Expected: her guesses — a handful of `⌂` homes, "(nothing chosen yet …)". Then `.venv/bin/python -m notron library --home Supps` should print the three-way "2 notes are called 'Supps'" refusal with ids.

**Step 6: Commit**

```bash
git add notron/cli.py tests/test_library.py
git commit -m "feat: notron library — show, scan (JSON for the app), --home/--ignore/--read/--start-from"
```

---

### Task 7: docs for the core

**Files:**
- Modify: `CLAUDE.md` (Commands block, module table, Invariants, a short section), `README.md` (Use block + one paragraph after the Brain Dump section)

**Step 1: CLAUDE.md**

- Commands block: add `.venv/bin/python -m notron library         # which notes she may file into / never reads`
- Module table: add `| \`library.py\` | Per-note home / read only / ignore choices; the one place "never reads it" lives |`
- Invariants: add `9. **An ignored note is never read.** \`library.user_notes()\` is the only way the core lists the user's notes; \`index\`, \`retrieval\`, \`mentions\`, \`care\` and \`filer\` all go through it, and \`index.search\` re-checks at query time because the index may be older than the choice.`
- New section before "## The self-improvement loop":

```markdown
## Your notes (`library.py`)

Once per note the user says **home** (the Filer may file into it), **read only**
(default) or **ignore** (never read — not for filing, not for questions, not even a
tag). Choices live in `.notron/library.json` keyed by note id; the Mac app's "Your
notes" window writes it (it lists notes via `notron library scan`), `notron library`
edits it from the terminal. A "start from [year]" cutoff applies only to notes the
user never looked at (`decided`); a row they flipped wins. With no homes chosen the
Filer keeps every readable note as a candidate; a note Notron creates after a `yes`
joins the homes only when homes exist. The pre-fill (`library.suggest`) is plain
code: recency + list-shaped body + short title, `privacy.py` seeds the ignores,
duplicate titles keep only the newest as a home.
```

- Update the test count in the Commands block comment to the real number from `pytest -q`.

**Step 2: README.md**

Under `## Use`, add `.venv/bin/python -m notron library             # which notes she may file into, which she never reads`. After the Brain Dump paragraph add:

```markdown
**Tell her where things go — once.** Years of notes means three notes called "Supps"
and a password note in the main list. In the app, "Your notes" shows every note
with a three-way switch — *Home* (she may file into it), *Read only* (the
default), *Ignore* (she never reads it, not even a tag inside it) — pre-filled
with her guesses and a "start from [year]" for the old stuff. Change it any time.
```

**Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: Your notes — homes / read only / ignore"
```

---

### Task 8 (Mac): one bridge to the core — `Core.swift`

**Files:**
- Create: `mac/Sources/Notron/Core.swift`
- Modify: `mac/Sources/Notron/AskNotronIntent.swift:27-56` (use the bridge)

**Step 1: Create `mac/Sources/Notron/Core.swift`**

```swift
import Foundation

/// The one way the app talks to the Python core: a subprocess, exactly like the
/// CLI. Dev-machine paths for now — shipping a DMG means bundling a Python
/// runtime inside the .app (see mac/README.md); these two environment variables
/// are the seam that will point at it.
enum Core {
    static let home: URL = {
        let path = ProcessInfo.processInfo.environment["NOTRON_HOME"] ?? "/Users/m1labs/Dev/apps/juno"
        return URL(fileURLWithPath: path)
    }()

    static let python: String =
        ProcessInfo.processInfo.environment["NOTRON_PYTHON"] ?? "/Users/m1labs/Dev/apps/juno/.venv/bin/python"

    struct Failure: Error, CustomStringConvertible {
        let description: String
    }

    /// Runs `python -m notron <args>` and returns what it printed. If it printed
    /// nothing, whatever it wrote to stderr becomes the error.
    static func run(_ args: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-m", "notron"] + args
        process.currentDirectoryURL = home

        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err

        try process.run()
        process.waitUntilExit()

        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if text.isEmpty {
            let problem = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            throw Failure(description: problem.isEmpty ? "Notron said nothing." : problem)
        }
        return text
    }
}
```

**Step 2: Point `AskNotronIntent` at it** — replace the body of `runNotron` (keep its signature and doc comment) with:

```swift
    private static func runNotron(_ request: String) throws -> String {
        do {
            return try Core.run(["ask", "--quiet", request])
        } catch let failure as Core.Failure {
            return failure.description == "Notron said nothing."
                ? "I don't have anything to say to that."
                : "Notron hit a problem: \(failure.description)"
        }
    }
```

**Step 3: Build**

Run: `cd mac && swift build 2>&1 | tail -3`
Expected: `Build complete!`

**Step 4: Commit**

```bash
git add mac/Sources/Notron/Core.swift mac/Sources/Notron/AskNotronIntent.swift
git commit -m "mac: one Core.run bridge to the Python core"
```

---

### Task 9 (Mac): the model — `Library.swift`

Reads `notron library scan`, holds the rows, writes `.notron/library.json` in the exact shape Task 1 reads.

**Files:**
- Create: `mac/Sources/Notron/Library.swift`

**Step 1: Create the file**

```swift
import Foundation

/// One note as the core reports it from `notron library scan`.
struct LibraryNote: Identifiable, Codable, Equatable {
    enum State: String, Codable, CaseIterable {
        case home, read, ignore

        var label: String {
            switch self {
            case .home: return "Home"
            case .read: return "Read only"
            case .ignore: return "Ignore"
            }
        }
    }

    let id: String
    let title: String
    let folder: String
    let modified: String
    var state: State
    let suggested: State
    let reason: String
}

struct LibraryScan: Codable {
    var notes: [LibraryNote]
    var duplicates: [String: [String]]
    var startFrom: String?
    var configured: Bool
}

/// What `.notron/library.json` holds — the contract with `notron/library.py`.
/// Encoded with snake_case keys so the two sides never drift.
struct LibraryFile: Codable {
    var homes: [String]
    var ignore: [String]
    var decided: [String]
    var startFrom: String?
    var chosenAt: String
}

@MainActor
final class LibraryModel: ObservableObject {
    @Published var notes: [LibraryNote] = []
    @Published var duplicates: [String: [String]] = [:]
    @Published var startFrom: Int? = nil        // a year; nil = everything
    @Published var loading = false
    @Published var problem: String?

    static let file = Core.home.appendingPathComponent(".notron/library.json")

    static var exists: Bool { FileManager.default.fileExists(atPath: file.path) }

    var privateCount: Int { notes.filter { $0.suggested == .ignore }.count }
    var counts: (home: Int, read: Int, ignore: Int) {
        (notes.filter { $0.state == .home }.count,
         notes.filter { $0.state == .read }.count,
         notes.filter { $0.state == .ignore }.count)
    }

    /// Asks the core for every note and its state. Off the main thread: a
    /// Notes listing is a second or two, and a cold Notes app can be forty.
    func load() {
        loading = true
        problem = nil
        Task.detached { [weak self] in
            let result: Result<LibraryScan, Error> = Result {
                let json = try Core.run(["library", "scan"])
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                return try decoder.decode(LibraryScan.self, from: Data(json.utf8))
            }
            await MainActor.run {
                guard let self else { return }
                self.loading = false
                switch result {
                case .success(let scan):
                    self.notes = scan.notes
                    self.duplicates = scan.duplicates
                    self.startFrom = scan.startFrom.flatMap { Int($0.prefix(4)) }
                case .failure(let error):
                    self.problem = "\(error)"
                }
            }
        }
    }

    /// Flip one row. Only one note of a shared title can be a home, so
    /// choosing one drops its twins back to read only — the inline picker.
    func set(_ id: String, to state: LibraryNote.State) {
        guard let i = notes.firstIndex(where: { $0.id == id }) else { return }
        notes[i].state = state
        if state == .home, let twins = duplicates[notes[i].title] {
            for twin in twins where twin != id {
                if let j = notes.firstIndex(where: { $0.id == twin }), notes[j].state == .home {
                    notes[j].state = .read
                }
            }
        }
    }

    /// "Start from 2026": one move, every note last edited before it becomes
    /// ignore. Rows can be flipped back afterwards; the core honours the row.
    func applyStartFrom() {
        guard let year = startFrom else { return }
        for i in notes.indices where Self.year(of: notes[i].modified).map({ $0 < year }) ?? false {
            notes[i].state = .ignore
        }
    }

    func save() throws {
        let file = LibraryFile(
            homes: notes.filter { $0.state == .home }.map(\.id),
            ignore: notes.filter { $0.state == .ignore }.map(\.id),
            decided: notes.map(\.id),
            startFrom: startFrom.map { "\($0)-01-01" },
            chosenAt: ISO8601DateFormatter().string(from: Date())
        )
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try FileManager.default.createDirectory(at: Self.file.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try encoder.encode(file).write(to: Self.file, options: .atomic)
    }

    /// Apple Notes dates read "Tuesday, 1 September 2026 at 16:03:12" (or the
    /// US form); the year is the only four-digit run in either.
    static func year(of modified: String) -> Int? {
        let digits = modified.split(whereSeparator: { !$0.isNumber })
        return digits.compactMap { $0.count == 4 ? Int($0) : nil }.first
    }
}
```

**Step 2: Build**

Run: `cd mac && swift build 2>&1 | tail -3`
Expected: `Build complete!`

**Step 3: Prove the file shape matches the core** — append to `tests/test_library.py`:

```python
def test_the_file_the_mac_app_writes_is_the_file_the_core_reads(state):
    """Keep in step with mac/Sources/Notron/Library.swift `LibraryFile`."""
    state.write_text('{"chosen_at":"2026-09-01T21:40:00Z","decided":["n1","n2"],'
                     '"homes":["n1"],"ignore":["n2"],"start_from":"2026-01-01"}')
    lib = library.load()
    assert lib.homes == {"n1"} and lib.ignore == {"n2"} and lib.decided == {"n1", "n2"}
    assert lib.start_from == datetime(2026, 1, 1)
```

Run: `.venv/bin/python -m pytest tests/test_library.py -q` → all green.

**Step 4: Commit**

```bash
git add mac/Sources/Notron/Library.swift tests/test_library.py
git commit -m "mac: Library model — reads notron library scan, writes .notron/library.json"
```

---

### Task 10 (Mac): the screen — `YourNotesView.swift`

720×520 window per DESIGN.md. Tokens only from `DS`. Segmented control = the DESIGN.md "Segmented control"; rows = "Menu-bar list row" chrome (hairline-bottom rows, no cards).

**Files:**
- Create: `mac/Sources/Notron/YourNotesView.swift`

**Step 1: Create the file**

```swift
import SwiftUI

/// Onboarding step 2 and Settings → "Your notes": every note, a three-way
/// switch per row, pre-filled with Notron's guesses. Nothing here reads a note
/// body and nothing calls a model — the core's `library.suggest` is plain code.
struct YourNotesView: View {
    @StateObject private var model = LibraryModel()
    @State private var query = ""
    @State private var saved = false
    @Environment(\.dismiss) private var dismiss

    private var years: [Int?] {
        let now = Calendar.current.component(.year, from: Date())
        return [nil] + Array((now - 12)...now).reversed()
    }

    private var shown: [LibraryNote] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return q.isEmpty ? model.notes
            : model.notes.filter { $0.title.lowercased().contains(q) || $0.folder.lowercased().contains(q) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Space.s4) {
            header
            controls
            list
            footer
        }
        .padding(DS.Space.s6)
        .frame(width: 720, height: 520)
        .background(DS.Color.bg)
        .onAppear { model.load() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: DS.Space.s1) {
            Text("Your notes").font(DS.Font.headline).foregroundStyle(DS.Color.text)
            Text("Home = she may file things here. Read only = she may read it to answer you. Ignore = she never reads it.")
                .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            if model.privateCount > 0 {
                Text("We spotted \(model.privateCount) that look private — they're set to Ignore.")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.accent)
            }
        }
    }

    private var controls: some View {
        HStack(spacing: DS.Space.s3) {
            TextField("Search", text: $query)
                .textFieldStyle(.plain)
                .font(DS.Font.body)
                .padding(.horizontal, DS.Space.s3).padding(.vertical, DS.Space.s2)
                .background(DS.Color.surface)
                .overlay(RoundedRectangle(cornerRadius: DS.Radius.md).stroke(DS.Color.hairlineLight))
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
            Spacer()
            Text("Start from").font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            Picker("Start from", selection: $model.startFrom) {
                ForEach(years, id: \.self) { year in
                    Text(year.map(String.init) ?? "everything").tag(year)
                }
            }
            .labelsHidden()
            .frame(width: 130)
            .onChange(of: model.startFrom) { _, _ in model.applyStartFrom() }
        }
    }

    private var list: some View {
        Group {
            if model.loading {
                VStack(spacing: DS.Space.s2) {
                    ProgressView()
                    Text("Reading your notes… a cold Notes app can take a moment.")
                        .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let problem = model.problem {
                Text(problem).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(shown) { note in
                            row(note)
                            Rectangle().fill(DS.Color.hairlineLight).frame(height: 1)
                        }
                    }
                }
                .background(DS.Color.surface)
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
                .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairlineLight))
            }
        }
    }

    private func row(_ note: LibraryNote) -> some View {
        HStack(alignment: .center, spacing: DS.Space.s3) {
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                Text(note.title).font(DS.Font.body).foregroundStyle(DS.Color.text).lineLimit(1)
                Text("\(note.folder) · \(note.modified)")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim).lineLimit(1)
                if !note.reason.isEmpty {
                    Text(note.reason).font(DS.Font.label).foregroundStyle(DS.Color.textFaint)
                }
            }
            Spacer()
            Picker("", selection: Binding(
                get: { note.state },
                set: { model.set(note.id, to: $0) }
            )) {
                ForEach(LibraryNote.State.allCases, id: \.self) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 260)
        }
        .padding(.horizontal, DS.Space.s4).padding(.vertical, DS.Space.s3)
        .opacity(note.state == .ignore ? 0.55 : 1)      // state by opacity, never by hue
        .animation(DS.Motion.standard, value: note.state)
    }

    private var footer: some View {
        HStack {
            let c = model.counts
            Text("\(c.home) homes · \(c.read) read only · \(c.ignore) ignored")
                .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            Spacer()
            if saved {
                Text("Saved").font(DS.Font.caption).foregroundStyle(DS.Color.success)
            }
            Button("Done") {
                do {
                    try model.save()
                    saved = true
                    dismiss()
                } catch {
                    model.problem = "Couldn't save: \(error)"
                }
            }
            .buttonStyle(.plain)
            .font(DS.Font.body)
            .foregroundStyle(DS.Color.bg)
            .padding(.horizontal, DS.Space.s5).padding(.vertical, DS.Space.s3)
            .background(DS.Color.accent)
            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
            .disabled(model.loading || model.notes.isEmpty)
        }
    }
}
```

**Step 2: Build**

Run: `cd mac && swift build 2>&1 | tail -3`
Expected: `Build complete!` (if `onChange(of:)` with two parameters is rejected, the deployment target is below 14 — it is `.v14` in Package.swift, so it should not be).

**Step 3: Commit**

```bash
git add mac/Sources/Notron/YourNotesView.swift
git commit -m "mac: Your notes screen — home / read only / ignore per row, start-from year"
```

---

### Task 11 (Mac): open it — menu item, first launch, and the counts line

**Files:**
- Modify: `mac/Sources/Notron/NotronApp.swift`

**Step 1: Add a counts watcher** (same shape as `MoodWatcher`, polls the file the screen writes) — below `MoodWatcher`:

```swift
/// "12 homes · 9 ignored" in the menu, read straight from .notron/library.json
/// so the menu bar tells the truth without a round trip to the core.
@MainActor
final class LibraryCounts: ObservableObject {
    @Published var line: String? = nil
    private var timer: Timer?

    init() {
        reload()
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.reload() }
        }
    }

    private func reload() {
        guard let data = try? Data(contentsOf: LibraryModel.file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { line = nil; return }
        let homes = (json["homes"] as? [String])?.count ?? 0
        let ignored = (json["ignore"] as? [String])?.count ?? 0
        line = "\(homes) homes · \(ignored) ignored"
    }
}
```

**Step 2: Wire the scene.** Replace the `NotronApp` struct with:

```swift
@main
struct NotronApp: App {
    @StateObject private var mood = MoodWatcher()
    @StateObject private var library = LibraryCounts()

    init() {
        NotronShortcuts.updateAppShortcutParameters()
    }

    var body: some Scene {
        MenuBarExtra {
            Text(mood.label)
            if let line = library.line {
                Text(line)
            }
            Text("Say \u{201C}Hey Siri, ask Notron\u{2026}\u{201D} anytime.")
            Divider()
            OpenLibraryButton(title: LibraryModel.exists ? "Your notes\u{2026}" : "Set up your notes\u{2026}")
            Divider()
            Button("Quit Notron") { NSApplication.shared.terminate(nil) }
        } label: {
            MenuBarLabel(emoji: mood.emoji)
        }
        .menuBarExtraStyle(.menu)

        Window("Your notes", id: "library") {
            YourNotesView()
        }
        .windowResizability(.contentSize)
    }
}

/// The menu bar glyph — and, because it is the one view that exists from
/// launch, the place a first run opens "Your notes" on its own.
private struct MenuBarLabel: View {
    let emoji: String
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Text(emoji)
            .onAppear {
                if !LibraryModel.exists {
                    NSApp.activate(ignoringOtherApps: true)
                    openWindow(id: "library")
                }
            }
    }
}

private struct OpenLibraryButton: View {
    let title: String
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button(title) {
            NSApp.activate(ignoringOtherApps: true)
            openWindow(id: "library")
        }
    }
}
```

**Step 3: Build and run it**

Run: `cd mac && swift build 2>&1 | tail -3 && swift run &` (or `swift run` in a terminal you keep open — a run started from a tool shell dies when the shell returns).
Expected: the window "Your notes" opens on its own if `.notron/library.json` does not exist; the list fills within a few seconds (the core lists ~360 notes); password/journal notes already say Ignore; ~10 rows say Home.

**Step 4: Manual checks (2 min)**

1. Flip one row to Ignore, one to Home, pick "Start from 2026", press Done.
2. `cat .notron/library.json` — the three lists and `"start_from": "2026-01-01"`.
3. `.venv/bin/python -m notron library` — same counts as the footer showed.
4. Reopen from the menu ("Your notes…") — the rows show your choices, not the guesses.
5. With two "Supps" rows: set Home on one — the other drops to Read only by itself.

**Step 5: Commit**

```bash
git add mac/Sources/Notron/NotronApp.swift
git commit -m "mac: open Your notes from the menu and on first launch; counts line"
```

---

### Task 12: finish

1. `.venv/bin/python -m pytest tests -q` → note the count; put it in CLAUDE.md's Commands comment.
2. `cd mac && swift build` → `Build complete!`.
3. Reinstall the listener so the running core honours the choices: `.venv/bin/python -m notron listen --install` (if `launchctl` answers `Bootstrap failed: 5`, run `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.m1labs.notron.listen.plist` once more — it was booted out first and needs a second try).
4. Update `docs/plans/2026-09-01-your-notes-setup-design.md`'s title line to `(approved; BUILT <date>)`.
5. Commit: `git commit -am "docs: Your notes — built"`.
6. Merge to main: `git checkout main && git merge --no-ff feature/your-notes-setup`.

**Out of scope, on purpose (YAGNI):** the Tidy scan; per-folder rules; the permissions onboarding screen (spec §1 — not built yet; when it is, chain it into `openWindow(id: "library")`); bundling Python in the .app.
