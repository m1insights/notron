# Journal Filing — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A thought with a list under it files as ONE entry, and a filed entry lands in its note laid out like a journal (one bold date per day, prose then bullets) or like a list (plain bullets, no dates) — decided per note, remembered per note, and used from line one when Notron creates a note for someone who has none.

**Why (the live failure, 2026-09-02):** the user dumped

```
The following stack did really well today once the trazodone wore off. …
- Concerta 36mg
- Avmacol
- Cialis 5mg
… (9 lines)
```

and `Supps` received ten separate bullets, each stamped `2 Sep —`, the sentence and its list scattered into peers. It read as a machine's log, not a journal. Becky will never pre-format a destination note; the layout is Notron's job.

**Architecture:** three small changes to the existing Filer, no new graph node, no new write mode.

1. **Runs.** `notedoc.lines()` learns where the blank lines are (`Line.after_gap`). `filer.unfiled()` keeps one `Item` per line but stamps each with a `run` — contiguous lines share one; a blank line, a ticked line, furniture, or one of Notron's own turns starts the next. A blank line is a hard boundary the model can never cross.
2. **The model groups within a run, code validates.** The classification prompt already sees every line. It gains one more verdict kind — `{"line": 3, "part_of": 2}` — and one more field, `"shapes": {"Supps": "log"}`. Code accepts a `part_of` only if it points at an earlier line in the same run; anything else is ignored and the line is judged on its own (today's behaviour). Parts are folded under their lead as `Item.parts`. Grouping is remembered in `.notron/filer.json` (`judged[part] = {"kind": "part", "of": <lead digest>}`) so a replay (a proposal answered next pass, an append the Guard refused) regroups without a model call.
3. **Layout.** A new pure module `notron/layout.py` turns entries into Markdown for `Executor.append`: a `log` note gets `**Wed 2 Sep 2026**` once per day (omitted when the note already ends under today's heading), then each entry as prose + bullets; a `list` note gets bullets, and a multi-line entry as a plain lead with bullets under it. The shape per note is what the model said the first time and is cached in `filer.json["shapes"]`; default `log` (an undated supplement log is worse than a dated recipe).

Everything still goes through the Executor and the Guard. Nothing is deleted. The receipt goes on the lead line; the lines under it get a bare `✓`.

**Tech Stack:** Python 3.12, `pytest` (`.venv/bin/python -m pytest tests -q`, no key, no network), Apple Notes HTML subset via `notron/markup.py`, Nebius "Super" tier via `brain.ask_json` (`filer.TIER = "smart"`).

**Branch:** `feature/journal-filing` off `main` in `apps/juno`. Commit trailer on every commit:

```
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013eMNwa5bLqWv8YtL8zQD14
```

**Two things found while planning (2026-09-02), both fixed inside this plan:**

- **The suite is red on this machine before any change.** `tests/test_filer.py`'s `store` fixture patches Notes but not `library.STATE`, so `filer.masters()` reads the developer's real `.notron/library.json`. Since the user chose six homes on 2026-09-02, no in-memory Store note is ever a candidate and several Filer tests fail (`assert [] == ['Supplements']`). Task 0.5 isolates it. Verified: with `library.STATE` pointed at `tmp_path`, the whole suite is green again.
- **After a `yes`, the line is filed twice.** `_approve` creates the note and ticks the line, then returns the same line in `rest`; `_file` finds it remembered as `note → <new title>`, the new note is now a candidate, and appends it again (`out.filed` lists it twice, the note holds two copies). Existing tests use `in`, so it slipped. Task 6 fixes it and pins `count == 1`.

**Not in scope (parked, say so in the final report):** tidying the existing `Supps` note by hand (nothing is ever deleted — the user does it); a per-note shape override in the Mac app; the tagged path `items_from_turn` keeps one `run` for the whole turn, which means grouping inside a tagged turn works for free but is not separately tested.

---

## Task 0: Branch

**Step 1: Create the branch**

```bash
cd /Users/m1labs/Dev/apps/juno && git checkout -b feature/journal-filing main
.venv/bin/python -m pytest tests -q 2>&1 | tail -2
```

Expected: **some Filer tests fail** (see the finding above). Note the exact count; Task 0.5 turns it green.

---

## Task 0.5: Tests must never read the developer's real library

**Files:**
- Modify: `tests/test_filer.py:92-101` (the `store` fixture)

**Step 1: Patch the fixture.** Add one line so the Filer's candidate list comes from the in-memory Store, never from `.notron/library.json` on the developer's Mac:

```python
@pytest.fixture
def store(monkeypatch, tmp_path):
    s = Store()
    from notron import notes, index, library
    for name in ("find_note", "read_body", "write_body", "create_note", "list_all_notes"):
        monkeypatch.setattr(notes, name, getattr(s, name))
    monkeypatch.setattr(index, "glimpses", lambda chars=100, **kw: {})
    monkeypatch.setattr(filer, "STATE", tmp_path / "filer.json")
    monkeypatch.setattr(library, "STATE", tmp_path / "library.json")   # never the developer's own choices
    s.add(workspace.LOG, "Everything Notron did.\n\n———\n", folder=workspace.FOLDER)
    return s
```

The three tests that already set `library.STATE` themselves point it at the same `tmp_path`, so they keep working.

**Step 2: Run the suite**

```bash
.venv/bin/python -m pytest tests -q 2>&1 | tail -2
```

Expected: all green. Record the count — it is the baseline every later task adds to.

**Step 3: Commit**

```bash
git add tests/test_filer.py
git commit -m "tests: the Filer suite never reads the developer's own library.json

Once the user chose homes, no in-memory note was a candidate and five
Filer tests failed on this Mac. The fixture now isolates library.STATE
the way it already isolates filer.STATE.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013eMNwa5bLqWv8YtL8zQD14"
```

---

## Task 1: `notedoc.Line.after_gap` — a line knows a blank line sits above it

**Files:**
- Modify: `notron/notedoc.py:103-125` (`Line`, `lines`)
- Test: `tests/test_filer.py` (section "lines and marks", after `test_a_bulleted_dump_is_still_one_thought_per_line`)

**Step 1: Write the failing test**

```python
def test_a_line_knows_when_a_blank_line_sits_above_it():
    """A blank line is how someone separates thoughts. Whitespace Apple Notes
    puts between elements is not a blank line; an empty <div><br></div> is."""
    html = markup.render("x", "first\nsecond\n\nthird\n- a\n- b\n\n\nlast")
    got = [(ln.text, ln.after_gap) for ln in notedoc.lines(html)]
    assert got == [("x", False), ("first", False), ("second", False),
                   ("third", True), ("a", False), ("b", False), ("last", True)]
```

**Step 2: Run it**

```bash
.venv/bin/python -m pytest tests/test_filer.py::test_a_line_knows_when_a_blank_line_sits_above_it -v
```

Expected: FAIL — `AttributeError: 'Line' object has no attribute 'after_gap'`.

**Step 3: Implement**

Replace the `Line` dataclass and `lines()` in `notron/notedoc.py`:

```python
@dataclass(frozen=True)
class Line:
    """One line of a note: a block, or one item inside a list block."""
    block: int      # index into `blocks`
    item: int       # which <li> inside the block, or -1 for the block itself
    text: str
    after_gap: bool = False   # an empty line sits between this and the one above


def lines(html: str) -> list[Line]:
    """Every line someone could have typed, in order. A list block yields one
    line per item, because a brain dump written as bullets is still one
    thought per line.

    An empty element — the <div><br></div> a blank line leaves behind — is not
    a line, but the next line remembers it (`after_gap`): a blank line is how
    people separate one thought from the next."""
    out: list[Line] = []
    gap = False
    for i, block in enumerate(blocks(html)):
        items = list(_LI.finditer(block))
        if items:
            for k, m in enumerate(items):
                out.append(Line(i, k, to_text(m.group(1)).strip(), gap))
                gap = False
            continue
        text = to_text(block).strip()
        if text:
            out.append(Line(i, -1, text, gap))
            gap = False
        elif block.strip():
            gap = True          # an element with nothing in it; bare "\n" between elements is not
    return out
```

**Step 4: Run the test and the whole suite**

```bash
.venv/bin/python -m pytest tests/test_filer.py::test_a_line_knows_when_a_blank_line_sits_above_it -v
.venv/bin/python -m pytest tests -q 2>&1 | tail -2
```

Expected: PASS; suite green (the new field has a default, so every existing `Line(i, k, text)` still constructs).

**Step 5: Commit**

```bash
git add notron/notedoc.py tests/test_filer.py
git commit -m "notedoc: a line remembers the blank line above it

A blank line is how someone separates one thought from the next. lines()
now says so (Line.after_gap) instead of dropping the empty element on the
floor, so the Filer can treat a run of adjacent lines as candidates for
one thought and a blank line as a boundary the model can never cross.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013eMNwa5bLqWv8YtL8zQD14"
```

---

## Task 2: `Item.run` and `Item.parts` — runs from the dump, parts round-trip

**Files:**
- Modify: `notron/filer.py:72-89` (`Item`), `notron/filer.py:147-174` (`unfiled`)
- Test: `tests/test_filer.py` (section "lines and marks")

**Step 1: Write the failing tests**

```python
def test_adjacent_lines_share_a_run_and_a_blank_line_or_a_ticked_line_starts_the_next(store):
    """A run is the most the model may ever group into one thought."""
    dump(store, "the stack today:\nmagnesium\nzinc\n\nact two needs a storm\n✓ old one → Book idea\nthat serum\n")
    items = filer.unfiled(store.body(workspace.DUMP))
    assert [(it.text, it.run) for it in items] == [
        ("the stack today:", 0), ("magnesium", 0), ("zinc", 0),
        ("act two needs a storm", 1),
        ("that serum", 2),                       # the ticked line between them broke the run
    ]


def test_an_item_carries_its_parts_through_json():
    lead = filer.Item("the stack today:", "the stack today:", 3, workspace.DUMP, "Notron",
                      parts=(filer.Item("magnesium", "magnesium", 4, workspace.DUMP, "Notron"),))
    back = filer.Item.from_dict(lead.as_dict())
    assert back == lead
    assert back.parts[0].text == "magnesium"
    assert lead.digest() == filer.Item("the stack today:", "x", 0, "y", "z").digest(), \
        "a digest is the line's own words — parts and position do not change it"
```

**Step 2: Run them**

```bash
.venv/bin/python -m pytest tests/test_filer.py -k "share_a_run or carries_its_parts" -v
```

Expected: FAIL — `TypeError: Item.__init__() got an unexpected keyword argument 'parts'` / `AttributeError: run`.

**Step 3: Implement**

Replace `Item` in `notron/filer.py`:

```python
@dataclass(frozen=True)
class Item:
    """One line to file, and where it lives so it can be ticked afterwards.

    `run` groups adjacent lines: a blank line, a ticked line, furniture or one
    of Notron's turns starts a new run, and the model may only ever fold lines
    together inside one. `parts` are the lines folded under this one — the
    list beneath "the stack I took today:"."""
    text: str          # what gets copied — tag and "file this" verb stripped
    anchor: str        # the line as it reads in the note, to find it again
    near: int          # block index hint
    note_title: str
    folder: str
    run: int = 0
    parts: tuple["Item", ...] = ()

    def digest(self) -> str:
        return hashlib.sha1(re.sub(r"\s+", " ", self.text.strip().lower()).encode()).hexdigest()

    def as_dict(self) -> dict:
        d = {"text": self.text, "anchor": self.anchor, "near": self.near,
             "note_title": self.note_title, "folder": self.folder}
        if self.parts:
            d["parts"] = [p.as_dict() for p in self.parts]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        return cls(d["text"], d["anchor"], int(d.get("near", 0)), d["note_title"], d["folder"],
                   parts=tuple(cls.from_dict(p) for p in d.get("parts", [])))
```

Replace the loop in `unfiled()`:

```python
    out: list[Item] = []
    in_turn = False
    run = 0
    for ln in notedoc.lines(body_html):
        text = ln.text.strip()
        if ln.after_gap:
            run += 1
        if in_turn:
            if text == conversation.RULE:
                in_turn = False
            run += 1
            continue
        if text.startswith(conversation.SIGNATURE) or text.startswith(f"**{conversation.SIGNATURE}**"):
            in_turn = True
            run += 1
            continue
        if _is_furniture(text, furniture) or text.startswith(FILED) or privacy.contains_secret(text):
            run += 1
            continue
        clean = VERB.sub("", conversation.strip_tag(text)).strip()
        out.append(Item(clean or text, text, ln.block, note_title, folder, run))
    return out
```

Then renumber runs densely so the test's `0, 1, 2` holds regardless of how many skipped lines sat between — add just before `return out`:

```python
    dense: dict[int, int] = {}
    return [replace(it, run=dense.setdefault(it.run, len(dense))) for it in out]
```

and add `from dataclasses import dataclass, field, replace` at the top.

**Step 4: Run tests + suite**

```bash
.venv/bin/python -m pytest tests/test_filer.py -k "share_a_run or carries_its_parts" -v
.venv/bin/python -m pytest tests -q 2>&1 | tail -2
```

Expected: PASS, suite green.

**Step 5: Commit**

```bash
git add notron/filer.py tests/test_filer.py
git commit -m "filer: lines carry a run, and an item can hold the lines under it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013eMNwa5bLqWv8YtL8zQD14"
```

---

## Task 3: `notron/layout.py` — how a filed thought is laid out in its note

**Files:**
- Create: `notron/layout.py`
- Create: `tests/test_layout.py`

**Step 1: Write the failing tests**

```python
"""How a filed thought is laid out in the note it lands in: a journal for
things that happen over time, a plain list for things that simply exist."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from datetime import date

from notron import layout, markup

DAY = date(2026, 9, 2)
STACK = ("The stack did really well today.", ["Concerta 36mg", "Avmacol", "PQQ"])
SINGLE = ("took vitamin D today", [])


def test_a_log_gets_one_bold_date_then_prose_then_bullets():
    md = layout.markdown([STACK, SINGLE], shape=layout.LOG, existing_text="", day=DAY)
    assert md == ("\n**Wed 2 Sep 2026**\n"
                  "The stack did really well today.\n- Concerta 36mg\n- Avmacol\n- PQQ\n"
                  "\n"
                  "took vitamin D today\n")
    html = markup.to_html(md)
    assert "<b>Wed 2 Sep 2026</b>" in html
    assert "<ul><li>Concerta 36mg</li><li>Avmacol</li><li>PQQ</li></ul>" in html


def test_a_second_pass_the_same_day_sits_under_the_heading_already_there():
    first = layout.markdown([SINGLE], shape=layout.LOG, existing_text="", day=DAY)
    note_text = "Supps\n\nMagnesium\n" + markup.to_text(markup.to_html(first))
    second = layout.markdown([STACK], shape=layout.LOG, existing_text=note_text, day=DAY)
    assert "Wed 2 Sep 2026" not in second
    assert second.startswith("\nThe stack did really well today.\n- Concerta 36mg")


def test_a_new_day_gets_its_own_heading_even_under_an_older_one():
    note_text = "Supps\n\nTue 1 Sep 2026\ntook vitamin D today\n"
    md = layout.markdown([SINGLE], shape=layout.LOG, existing_text=note_text, day=DAY)
    assert md.startswith("\n**Wed 2 Sep 2026**\n")


def test_a_list_is_bullets_with_no_date_and_a_multi_line_thought_keeps_its_shape():
    md = layout.markdown([SINGLE, ("that serum from the pop-up", []), STACK],
                         shape=layout.LIST, existing_text="", day=DAY)
    assert md == ("\n- took vitamin D today\n- that serum from the pop-up\n"
                  "\n"
                  "The stack did really well today.\n- Concerta 36mg\n- Avmacol\n- PQQ\n")
    assert "2026" not in md


def test_an_unknown_shape_is_a_log():
    assert layout.shape("whatever") == layout.LOG
    assert layout.shape("list") == layout.LIST
    assert layout.shape(" Log ") == layout.LOG
```

**Step 2: Run them**

```bash
.venv/bin/python -m pytest tests/test_layout.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'notron.layout'`.

**Step 3: Implement `notron/layout.py`**

```python
"""How a filed thought is laid out in the note it lands in.

Two shapes, chosen per note and remembered:

  * a **log** — things that happen over time: what someone took, ate, did,
    trained, felt. It reads like a journal: one bold date per day, and under
    it each thought as it was written — a sentence, then the list that came
    with it. No stamp on every line; the date is the heading.

  * a **list** — things that simply exist: recipes, ideas, names, places,
    things to buy. Plain bullets, no dates. A thought that arrived as a
    sentence with lines under it keeps that shape.

Pure functions, Markdown out, for `markup.to_html` to render. Nothing here
reads or writes a note.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Sequence

LOG = "log"
LIST = "list"

#: "Wed 2 Sep 2026" — the day, as a person would write it at the top of a page.
STAMP = "%a %-d %b %Y"
_HEADING = re.compile(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2} \d{4}$")

#: (lead line, the lines under it)
Entry = tuple[str, Sequence[str]]


def shape(said: str | None) -> str:
    """The shape a model named, or LOG when it said nothing usable — an
    undated supplement log is a worse mistake than a dated recipe."""
    return LIST if (said or "").strip().casefold() == LIST else LOG


def heading(day: date) -> str:
    return f"{day:{STAMP}}"


def under_today(existing_text: str, day: date) -> bool:
    """Does the note already end under today's date? The last dated heading in
    the plain text is the one everything below belongs to."""
    last = None
    for line in existing_text.split("\n"):
        if _HEADING.match(line.strip()):
            last = line.strip()
    return last == heading(day)


def _block(entry: Entry, *, shape: str) -> str:
    lead, parts = entry
    if shape == LIST and not parts:
        return f"- {lead}"
    return "\n".join([lead, *(f"- {p}" for p in parts)])


def markdown(entries: Sequence[Entry], *, shape: str, existing_text: str, day: date) -> str:
    """The Markdown to append for these entries, in this shape, to a note
    whose plain text currently reads `existing_text`."""
    blocks = [_block(e, shape=shape) for e in entries]
    if shape == LIST:
        chunks: list[str] = []
        for b in blocks:
            if "\n" not in b and chunks and "\n" not in chunks[-1]:
                chunks[-1] += "\n" + b          # adjacent bullets, one list
            else:
                chunks.append(b)
        return "\n" + "\n\n".join(chunks) + "\n"
    body = "\n\n".join(blocks)
    if under_today(existing_text, day):
        return "\n" + body + "\n"
    return f"\n**{heading(day)}**\n{body}\n"
```

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_layout.py -v
```

Expected: 5 PASS. If `test_a_log_gets_one_bold_date…` fails on the `<ul>` assertion, check `markup.to_html` joined the three `- ` lines into one `<ul>` (it does for adjacent lines).

**Step 5: Commit**

```bash
git add notron/layout.py tests/test_layout.py
git commit -m "layout: a filed thought reads like a journal, or like a list

One bold date per day for a log, prose then bullets, no stamp on every
line. Plain bullets for a list. A thought that came as a sentence with
lines under it keeps that shape in both.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013eMNwa5bLqWv8YtL8zQD14"
```

---

## Task 4: The model contract — `part_of`, `shapes`, blank lines the model can see

**Files:**
- Modify: `notron/filer.py:244-329` (`FILER_SYSTEM`, `_prompt`, `classify`)
- Modify: `tests/test_filer.py:66-89` (`FilerBrain`)
- Test: `tests/test_filer.py` (section after `test_a_title_the_model_invented_becomes_a_proposal_never_a_write`)

**Step 1: Update the fake brain** so a verdict can be `{"part_of": "<lead text>"}` and the fake can name shapes. Replace `FilerBrain` in `tests/test_filer.py`:

```python
class FilerBrain:
    """Answers the Filer's question from a table keyed by line text.

    A verdict is {"note": title}, {"new": title}, or {"part_of": "<the lead
    line's text>"} — the fake turns the lead's text into its number in the
    prompt, the way the model would. `shapes` is returned as-is."""

    def __init__(self, verdicts: dict[str, dict], shapes: dict[str, str] | None = None):
        self.verdicts = verdicts
        self.shapes = shapes or {}
        self.calls = 0
        self.prompts = []

    def ask_json(self, *, system, user, **kw):
        self.calls += 1
        self.prompts.append(user)
        assert kw.get("tier") == filer.TIER
        numbered = {m.group(2): int(m.group(1)) for m in re.finditer(r"^(\d+)\. (.*)$", user, re.M)}
        rows = []
        for text, n in numbered.items():
            v = self.verdicts.get(text)
            if not v:
                continue
            if "part_of" in v:
                rows.append({"line": n, "part_of": numbered.get(v["part_of"], 0)})
            else:
                rows.append({"line": n, **v})
        return {"filed": rows, "shapes": dict(self.shapes)}

    def ask(self, **kw):
        raise AssertionError("the Filer must never pay for a text model call")
```

**Step 2: Write the failing tests**

```python
def test_the_model_sees_a_blank_line_between_runs():
    items = [filer.Item("a", "a", 0, "d", "f", run=0), filer.Item("b", "b", 1, "d", "f", run=0),
             filer.Item("c", "c", 3, "d", "f", run=1)]
    assert filer._prompt(items, []).endswith("# Lines to file\n1. a\n2. b\n\n3. c")


def test_a_part_points_at_an_earlier_line_in_the_same_run_or_it_is_ignored():
    items = [filer.Item("the stack today:", "x", 0, "d", "f", run=0),
             filer.Item("magnesium", "x", 1, "d", "f", run=0),
             filer.Item("zinc", "x", 2, "d", "f", run=0),
             filer.Item("act two needs a storm", "x", 4, "d", "f", run=1),
             filer.Item("a lighthouse", "x", 5, "d", "f", run=1)]
    brain = FilerBrain({"the stack today:": {"note": "Supps"},
                        "magnesium": {"part_of": "the stack today:"},
                        "zinc": {"part_of": "magnesium"},                 # a part of a part → the lead
                        "act two needs a storm": {"part_of": "zinc"},      # crosses a run → ignored
                        "a lighthouse": {"part_of": "a lighthouse"}},     # points at itself → ignored
                       shapes={"Supps": "log", "Nonsense": "list"})
    verdicts, shapes = filer.classify(brain, items, [filer.Master("Supps", "Notes")])
    assert verdicts == [("note", "Supps"), ("part", "0"), ("part", "0"), None, None]
    assert shapes == {"Supps": "log", "Nonsense": "list"}
```

**Step 3: Run them**

```bash
.venv/bin/python -m pytest tests/test_filer.py -k "blank_line_between_runs or earlier_line_in_the_same_run" -v
```

Expected: FAIL (prompt has no blank line; `classify` returns a list, not a tuple).

**Step 4: Implement**

Replace `FILER_SYSTEM`, `_prompt` and `classify` in `notron/filer.py`:

```python
FILER_SYSTEM = """You file lines from someone's brain dump into their own notes.

You are given the notes they already have — each title in quotes, then a glimpse
of what is inside — and numbered lines they wrote. For each line, choose the one
note it belongs in.

Reply with JSON only:
{"filed": [{"line": 1, "note": "<the title between the quotes, copied exactly>"},
           {"line": 2, "new": "<a short Title Case name for a note that does not exist yet>"},
           {"line": 3, "part_of": 2}],
 "shapes": {"<title>": "log" or "list", ...}}

Rules:
- "note" is only ever the title between the quotes — never the glimpse, never a
  title you made up.
- A line belongs in a note when it is about the same subject. A supplement they
  took goes in their supplements note even if that supplement is not in the glimpse;
  a thought about their book goes in the book note.
- Use "new" only when none of their notes is about that subject. A wrong note is
  worse than a question — never force a line into the nearest bucket.
- Lines that belong together get the same "new" title, so one note can hold them.
- Some lines are not thoughts of their own but belong under the line above them:
  the list under "the stack I took today:", the steps under a recipe, the items
  under "to pack:". Give each of those {"line": N, "part_of": M} where M is the
  line they hang from, and nothing else — they go wherever line M goes. Only a
  line directly below, in the same group; a blank line always separates thoughts.
- For every title you used, say in "shapes" what kind of note it is: "log" if it
  collects things that happen over time — what they took, ate, did, trained,
  felt, spent — or "list" if it collects things that simply exist — recipes,
  ideas, names, places, things to buy.
- Every line appears exactly once. Output nothing but the JSON object."""


def _prompt(items: list[Item], candidates: list[Master]) -> str:
    listing = "\n".join(
        f'- "{m.title}"' + (f" — {m.glimpse}" if m.glimpse else "") for m in candidates
    ) or "(they have no notes yet)"
    numbered: list[str] = []
    for i, it in enumerate(items, start=1):
        if numbered and it.run != items[i - 2].run:
            numbered.append("")                      # the blank line they left, so the model sees it
        numbered.append(f"{i}. {privacy.redact(it.text)}")
    return f"# Their notes\n{listing}\n\n# Lines to file\n" + "\n".join(numbered)
```

```python
def classify(brain, items: list[Item], candidates: list[Master]
             ) -> tuple[list[tuple[str, str] | None], dict[str, str]]:
    """One verdict per item — ("note", existing title), ("new", proposed title),
    ("part", index of its lead as a string) or None when the model said nothing
    usable — and the shape the model named for each title it used.

    The model proposes; this validates. A title that is not on the list is
    treated as a proposal, never as a place to write. A part may only hang from
    an earlier line in the same run; a part of a part hangs from the lead."""
    by_key = {m.title.casefold(): m.title for m in candidates}
    verdicts: list[tuple[str, str] | None] = [None] * len(items)
    shapes: dict[str, str] = {}
    for start in range(0, len(items), MAX_LINES):
        batch = items[start:start + MAX_LINES]
        out = brain.ask_json(system=FILER_SYSTEM, user=_prompt(batch, candidates),
                             tier=TIER, max_tokens=min(240 + 40 * len(batch), 1600))
        parts: dict[int, int] = {}                   # batch index -> batch index it hangs from
        for row in out.get("filed") or []:
            if not isinstance(row, dict):
                continue
            try:
                n = int(row.get("line"))
            except (TypeError, ValueError):
                continue
            if not 1 <= n <= len(batch):
                continue
            if row.get("part_of") is not None:
                try:
                    m = int(row["part_of"])
                except (TypeError, ValueError):
                    continue
                if 1 <= m < n and batch[m - 1].run == batch[n - 1].run:
                    parts[n - 1] = m - 1
                continue
            note = str(row.get("note") or "").strip()
            new = str(row.get("new") or "").strip()
            hit = _match(note, by_key) if note else None
            if hit:
                verdicts[start + n - 1] = ("note", hit)
            elif note or new:
                verdicts[start + n - 1] = ("new", _title(new or note))
        for k, lead in parts.items():
            hops = 0
            while lead in parts and hops < len(batch):     # a part of a part → its lead
                lead, hops = parts[lead], hops + 1
            if lead not in parts and verdicts[start + lead] is not None:
                verdicts[start + k] = ("part", str(start + lead))
        for title, said in (out.get("shapes") or {}).items():
            if isinstance(title, str) and title.strip():
                shapes[title.strip()] = str(said)
    return verdicts, shapes
```

Note the `("part", …)` verdict is dropped when its lead got no verdict of its own — a part with nowhere to go is judged as a line next time, not lost.

**Step 5: Run tests + suite**

```bash
.venv/bin/python -m pytest tests/test_filer.py -k "blank_line_between_runs or earlier_line_in_the_same_run" -v
.venv/bin/python -m pytest tests -q 2>&1 | tail -3
```

Expected: the two new tests PASS. Several existing Filer tests now FAIL with `ValueError: too many values to unpack` at `_file` (classify's new return). That is Task 5's job — do not patch around it here. Commit anyway (a red suite between two tasks of one feature is fine on the branch; say so in the message).

**Step 6: Commit**

```bash
git add notron/filer.py tests/test_filer.py
git commit -m "filer: the model may hang a line under the one above it, and name a note's shape

classify() returns (verdicts, shapes). A part_of is accepted only for an
earlier line in the same run; a blank line is a boundary the model sees
and code enforces. _file still expects the old return — next commit.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013eMNwa5bLqWv8YtL8zQD14"
```

---

## Task 5: `_file` — fold parts, remember them, write in shape

**Files:**
- Modify: `notron/filer.py` (`Outcome.summary`, `_state`, `_bullets` → removed, `_file`, new helpers `_fold`, `_marks_for`, `_shape_for`, `_existing_text`)
- Test: `tests/test_filer.py` (section "passes"; update two pins)

**Step 1: Update the two pinned summaries** (`tests/test_filer.py:302` and `:399`):

```python
    assert "Filed 2 thoughts" in out.summary()
```
```python
    assert "Filed 1 thought" in out.summary()
```

**Step 2: Write the failing tests** (add after `test_a_dump_pass_copies_each_line_into_its_note_and_ticks_it_with_a_receipt`):

```python
STACK_DUMP = ("The stack did really well today once the trazodone wore off.\n"
              "Concerta 36mg\nAvmacol\nPQQ\n\ntook vitamin D today\n")
STACK_BRAIN = {"The stack did really well today once the trazodone wore off.": {"note": "Supps"},
               "Concerta 36mg": {"part_of": "The stack did really well today once the trazodone wore off."},
               "Avmacol": {"part_of": "The stack did really well today once the trazodone wore off."},
               "PQQ": {"part_of": "The stack did really well today once the trazodone wore off."},
               "took vitamin D today": {"note": "Supps"}}


def test_a_sentence_and_the_list_under_it_file_as_one_thought_in_journal_shape(store, monkeypatch):
    """The live failure of 2026-09-02: ten dated bullets where one entry belonged."""
    store.add("Supps", "Magnesium\nSeriphos\n")
    dump(store, STACK_DUMP)
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    brain = FilerBrain(STACK_BRAIN, shapes={"Supps": "log"})

    out = filer.run(brain)

    assert [(it.text[:9], [p.text for p in it.parts], t) for it, t in out.filed] == [
        ("The stack", ["Concerta 36mg", "Avmacol", "PQQ"], "Supps"),
        ("took vita", [], "Supps")]
    assert "Filed 2 thoughts → Supps (2)." in out.summary()
    supps = store.text("Supps")
    assert supps == ("Supps\n\nMagnesium\nSeriphos\n\nWed 2 Sep 2026\n"
                     "The stack did really well today once the trazodone wore off.\n"
                     "• Concerta 36mg\n• Avmacol\n• PQQ\n\ntook vitamin D today")
    d = store.text(workspace.DUMP)
    assert "✓ The stack did really well today once the trazodone wore off. → Supps" in d
    assert "✓ Concerta 36mg\n✓ Avmacol\n✓ PQQ" in d, "the lines under it get a tick and no receipt"
    assert "✓ took vitamin D today → Supps" in d
    assert brain.calls == 1


def test_a_second_pass_the_same_day_joins_the_entry_already_there(store, monkeypatch):
    store.add("Supps", "-")
    dump(store, "took vitamin D today\n")
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    filer.run(FilerBrain({"took vitamin D today": {"note": "Supps"}}, shapes={"Supps": "log"}))
    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>zinc at lunch</div>"

    filer.run(FilerBrain({"zinc at lunch": {"note": "Supps"}}, shapes={"Supps": "list"}))

    assert store.text("Supps").count("Wed 2 Sep 2026") == 1
    assert store.text("Supps").endswith("Wed 2 Sep 2026\ntook vitamin D today\n\nzinc at lunch")
    assert filer._state()["shapes"] == {"Supps": "log"}, "the first shape sticks; a later 'list' is ignored"


def test_a_list_shaped_note_gets_bullets_and_no_date(store, monkeypatch):
    store.add("Recipes", "-")
    dump(store, "Pasta that worked:\ntomatoes\nbasil\n\nthat serum from the pop-up\n")
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    brain = FilerBrain({"Pasta that worked:": {"note": "Recipes"},
                        "tomatoes": {"part_of": "Pasta that worked:"},
                        "basil": {"part_of": "Pasta that worked:"},
                        "that serum from the pop-up": {"note": "Recipes"}},
                       shapes={"Recipes": "list"})
    filer.run(brain)
    assert store.text("Recipes") == "Recipes\n\n-\n\nPasta that worked:\n• tomatoes\n• basil\n\n• that serum from the pop-up"


def test_a_part_is_remembered_so_a_replay_regroups_without_the_model(store, monkeypatch):
    """The append is refused once (the note moved); next pass the group is
    still one thought, and the model is not asked about the parts again."""
    store.add("Supps", "-")
    dump(store, STACK_DUMP)
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    brain = FilerBrain(STACK_BRAIN, shapes={"Supps": "log"})
    monkeypatch.setattr(guard, "check", lambda **kw: guard.Verdict(False, "moved")
                        if kw["mode"] == "append" else guard.ALLOW)
    out = filer.run(brain)
    assert out.filed == [] and "✓" not in store.text(workspace.DUMP)

    monkeypatch.undo()          # the Guard is itself again
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    out = filer.run(brain)
    assert brain.calls == 2, "the leads are judged again (a refused copy is never remembered) …"
    assert "Concerta" not in brain.prompts[1], "… but the lines under them are not: the group came from memory"
    assert [[p.text for p in it.parts] for it, _ in out.filed] == [["Concerta 36mg", "Avmacol", "PQQ"], []]
    assert "• Concerta 36mg\n• Avmacol\n• PQQ" in store.text("Supps")
```

Add `from datetime import date` to the test file's imports. Note `monkeypatch.undo()` also undoes the `store` fixture's patches — so the last test needs the `store` patches re-applied. Simplest: instead of `undo()`, patch the guard with a flag:

```python
    refuse = {"on": True}
    monkeypatch.setattr(guard, "check", lambda **kw: guard.Verdict(False, "moved")
                        if (refuse["on"] and kw["mode"] == "append") else guard.ALLOW)
    out = filer.run(brain)
    …
    refuse["on"] = False
    out = filer.run(brain)
```

Use that form; drop the `undo()` and the repeated `_today` patch.

**Step 3: Run them**

```bash
.venv/bin/python -m pytest tests/test_filer.py -k "journal_shape or joins_the_entry or bullets_and_no_date or replay_regroups" -v
```

Expected: FAIL (`filer._today` missing; unpack error).

**Step 4: Implement.** In `notron/filer.py`:

Imports: `from datetime import date, datetime`, and add `layout, markup` to the `from . import …` line.

`Outcome.summary` — change the filed line:

```python
            lines.append(f"Filed {n} thought{'s' if n != 1 else ''} → {where}.")
```

`_state` — add `data.setdefault("shapes", {})`.

Delete `_bullets`. Add, in the "passes" section:

```python
def _today() -> date:
    return date.today()


def _fold(items: list[Item], parts_of: dict[int, list[int]]) -> list[Item]:
    """The same list, with each lead carrying its parts. Indices do not move —
    a part stays in the list, verdict-less, so nothing downstream reindexes."""
    return [replace(it, parts=tuple(items[j] for j in parts_of[i])) if i in parts_of else it
            for i, it in enumerate(items)]


def _marks_for(it: Item, receipt: str) -> list[tuple[str, int, str]]:
    """The lead gets the receipt; the lines under it get a bare tick."""
    return [(it.anchor, it.near, receipt)] + [(p.anchor, p.near, "") for p in it.parts]


def _shape_for(title: str, said: dict[str, str], state: dict) -> str:
    """The note's shape: what was decided the first time, else what the model
    just said (and that becomes the decision), else a log."""
    shapes: dict = state["shapes"]
    if title not in shapes:
        shapes[title] = layout.shape(said.get(title))
    return shapes[title]


def _existing_text(folder: str, title: str) -> str:
    note = notes.find_note(folder, title)
    return markup.to_text(notes.read_body(note.id)) if note else ""


def _entry_markdown(group: list[Item], *, shape: str, folder: str, title: str) -> str:
    entries = [(it.text, [p.text for p in it.parts]) for it in group]
    return layout.markdown(entries, shape=shape, existing_text=_existing_text(folder, title),
                           day=_today())
```

Replace the body of `_file` from `# 1. Verdicts` through the end of `# 2.`:

```python
    # 1. Verdicts — from memory where she already judged this exact line.
    #    A remembered part rejoins its lead if the lead is still here, in the
    #    same run; otherwise it is a line of its own again.
    by_digest = {it.digest(): i for i, it in enumerate(items)}
    parts_of: dict[int, list[int]] = {}
    verdicts: dict[int, tuple[str, str]] = {}
    ask: list[int] = []
    for i, it in enumerate(items):
        prior = judged.get(it.digest())
        if prior and prior.get("kind") == "part":
            lead = by_digest.get(prior.get("of", ""))
            if lead is not None and lead < i and items[lead].run == it.run:
                parts_of.setdefault(lead, []).append(i)
            else:
                ask.append(i)
        elif prior and prior.get("kind") == "declined":
            out.left.append((it, "you said no to a note for it"))
        elif prior and prior.get("kind") == "note" and prior.get("title") in known:
            verdicts[i] = ("note", prior["title"])
        elif prior and prior.get("kind") == "new" and prior.get("title", "").casefold() in pending_titles:
            verdicts[i] = ("new", pending_titles[prior["title"].casefold()])
        else:
            ask.append(i)

    said_shapes: dict[str, str] = {}
    if ask:
        say(f"judging {len(ask)} line(s) against {len(candidates)} notes")
        try:
            fresh, said_shapes = classify(brain, [items[i] for i in ask], candidates)
            out.model_calls += 1
        except Exception as e:
            say(f"could not judge them ({type(e).__name__}) — leaving them for next time")
            fresh = [None] * len(ask)
        for i, v in zip(ask, fresh):
            if v is None:
                out.left.append((items[i], "I couldn't decide where it goes"))
            elif v[0] == "part":
                lead = ask[int(v[1])]
                parts_of.setdefault(lead, []).append(i)
                judged[items[i].digest()] = {"kind": "part", "of": items[lead].digest()}
            else:
                verdicts[i] = v
    items = _fold(items, parts_of)

    # 2. Copy into existing notes, then tick — only what actually landed.
    by_master: dict[str, list[int]] = {}
    for i, (kind, title) in verdicts.items():
        if kind == "note":
            by_master.setdefault(title, []).append(i)
    marks: dict[tuple[str, str], list[tuple[str, int, str]]] = {
        k: list(v) for k, v in (extra_marks or {}).items()}
    for title, idxs in by_master.items():
        master = known[title]
        group = [items[i] for i in idxs]
        shape = _shape_for(title, said_shapes, state)
        r = ex.append(title, _entry_markdown(group, shape=shape, folder=master.folder, title=title),
                      folder=master.folder)
        out.results.append(f"{'✓' if r.ok else '✗'} {title} — {r.reason}")
        if not r.ok:
            for it in group:
                out.left.append((it, r.reason))
            continue
        for it in group:
            out.filed.append((it, title))
            judged[it.digest()] = {"kind": "note", "title": title}
            marks.setdefault((it.folder, it.note_title), []).extend(_marks_for(it, f"{RECEIPT}{title}"))
```

Section 3 (proposals) stays as it is — `items[i]` there is now the folded lead, so `it.as_dict()` carries its parts into the proposal record. Keep the rest of `_file` unchanged.

**Step 5: Run tests + suite**

```bash
.venv/bin/python -m pytest tests/test_filer.py -v 2>&1 | tail -15
.venv/bin/python -m pytest tests -q 2>&1 | tail -2
```

Expected: everything green **except** `test_yes_creates_the_note_files_the_lines_and_ticks_the_yes` may still pass on the old `_bullets`-free path only if `_approve` compiles — it references `_bullets`, which is gone, so it FAILS with `NameError`. That is Task 6. Everything else green.

If `test_a_sentence_and_the_list_under_it…` fails on the exact `supps ==` string: print `repr(store.text("Supps"))` and check the blank-line count; `markup.to_text` collapses three newlines to two, and `to_html("\n**…**")` emits a leading `<div><br></div>` — the expected string above already accounts for both.

**Step 6: Commit**

```bash
git add notron/filer.py tests/test_filer.py
git commit -m "filer: a sentence and the list under it file as one thought, in the note's own shape

Parts fold under their lead (and stay folded on a replay, from memory,
with no second model call). A log note gets one bold date per day and
the thought as written beneath it; a list note gets bullets. The receipt
sits on the lead; the lines under it get a bare tick. Summary counts
thoughts, not lines.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013eMNwa5bLqWv8YtL8zQD14"
```

---

## Task 6: `_approve` — a note Notron makes starts in shape from line one

**Files:**
- Modify: `notron/filer.py` (`_approve`, `_file` section 3)
- Test: `tests/test_filer.py`

**Step 1: Write the failing test** (after `test_yes_creates_the_note_files_the_lines_and_ticks_the_yes`):

```python
def test_a_note_she_makes_starts_in_shape_with_the_whole_thought(store, monkeypatch):
    """Becky never pre-builds a note. When Notron makes one it must already
    read like a journal: title, today's date, the sentence, the list under it."""
    store.add("Supplements", "-")
    dump(store, "Pasta that worked:\ntomatoes\nbasil\n")
    monkeypatch.setattr(filer, "_today", lambda: date(2026, 9, 2))
    brain = FilerBrain({"Pasta that worked:": {"new": "Recipes"},
                        "tomatoes": {"part_of": "Pasta that worked:"},
                        "basil": {"part_of": "Pasta that worked:"}},
                       shapes={"Recipes": "list"})
    filer.run(brain)
    assert list(filer.pending_proposals()) == ["Recipes"]
    assert [p.text for p in filer.pending_proposals()["Recipes"][0].parts] == ["tomatoes", "basil"]
    nid = store.find_note(workspace.FOLDER, workspace.DUMP).id
    store.rows[nid]["body"] += "<div>yes</div>"

    out = filer.run(brain)

    assert out.created == ["Recipes"]
    assert store.text("Recipes") == "Recipes\n\nPasta that worked:\n• tomatoes\n• basil"
    d = store.text(workspace.DUMP)
    assert "✓ Pasta that worked: → Recipes\n✓ tomatoes\n✓ basil" in d
    assert "✓ yes → made “Recipes”, 1 filed" in d
    assert filer._state()["shapes"] == {"Recipes": "list"}
    assert brain.calls == 1
    assert store.text("Recipes").count("Pasta that worked:") == 1, "a yes files the thought once, not twice"
```

Also pin the same in the existing yes test — add to `test_yes_creates_the_note_files_the_lines_and_ticks_the_yes`, after the `"the serum from that brand" in store.text("Skincare Brand")` line:

```python
    assert store.text("Skincare Brand").count("the serum from that brand") == 1, "filed once — see Task 6"
    assert [t for _, t in out.filed] == ["Skincare Brand"]
```

**Step 2: Run it**

```bash
.venv/bin/python -m pytest tests/test_filer.py -k "starts_in_shape" -v
```

Expected: FAIL — `NameError: _bullets` (and, once that is fixed, the `count(...) == 1` pins fail with 2 — the duplicate).

**Step 3: Implement.** The shape the model named for a *new* title must survive until the yes. In `_file` section 3, where a proposal record is created, remember it:

```python
        record = proposals.setdefault(canonical, {"asked": None, "items": []})
        if canonical not in state["shapes"] and canonical in said_shapes:
            state["shapes"][canonical] = layout.shape(said_shapes[canonical])
```

(Put those two lines right after the `record = …` line.)

In `_approve`, replace the `ex.append(title, _bullets(waiting), folder=FILING_FOLDER)` call and the marks loop:

```python
            say(f"making “{title}” with {len(waiting)} line(s)")
            shape = _shape_for(title, {}, state)
            r = ex.append(title, _entry_markdown(waiting, shape=shape, folder=FILING_FOLDER, title=title),
                          folder=FILING_FOLDER)
            out.results.append(f"{'✓' if r.ok else '✗'} {title} — {r.reason}")
            if not r.ok:
                proposals[title] = record            # keep the question open
                receipts.append(f"couldn't make “{title}”: {r.reason}")
                continue
            out.created.append(title)
            if r.note_id:
                from . import library
                library.add_home(r.note_id)
            marks: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
            for w in waiting:
                out.filed.append((w, title))
                judged[w.digest()] = {"kind": "note", "title": title}
                marks.setdefault((w.folder, w.note_title), []).extend(_marks_for(w, f"{RECEIPT}{title}"))
            _tick(ex, marks, out)
            receipts.append(f"made “{title}”, {len(waiting)} filed")
```

And make `_approve` hand back only the lines it did **not** just file. It already builds `rest` as it goes; the just-filed lines are in `rest` too, because the `yes` sits below them in the dump and the loop had already passed them. Replace the final `return rest` of `_approve` with:

```python
    landed = {digest for digest, v in judged.items() if v.get("kind") == "note" and v.get("title") in out.created}
    return [it for it in rest if it.digest() not in landed]
```

(`out.created` holds the titles made in this call; a line remembered as filed into one of them was filed by this very `yes`, and `_file` must not see it again.)

**Step 4: Run tests + full suite**

```bash
.venv/bin/python -m pytest tests -q 2>&1 | tail -2
```

Expected: all green, count = baseline + 12 (1 + 2 + 5 + 2 + 4 + 1 new tests; the two pins were edits). Also confirm the "list" shape wrote the new note with a blank line after the title and no date: `store.text("Recipes")` in the test pins it.

**Step 5: Commit**

```bash
git add notron/filer.py tests/test_filer.py
git commit -m "filer: a note she makes starts in shape, with the whole thought

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013eMNwa5bLqWv8YtL8zQD14"
```

---

## Task 7: Words — the dump's own instruction, README, CLAUDE.md, this plan's status

**Files:**
- Modify: `notron/workspace.py:53` (DUMP seed)
- Modify: `README.md:69-83` (the dump section)
- Modify: `CLAUDE.md` (the Filer paragraph near line 205; the module table near line 79)
- Modify: `docs/plans/2026-09-02-journal-filing.md` (this file — status line at the top)

**Step 1: Seed.** Replace the DUMP seed in `notron/workspace.py`:

```python
    DUMP: """Throw anything in here, one thought per line — a list under a thought stays with it. When you've stopped for a while, Notron files each thought into the right note and ticks it — nothing is ever deleted.

———
""",
```

`FURNITURE` matches on the prefix "Throw anything in here", so the existing live dump note (old wording) is still recognised as scenery. Run `.venv/bin/python -m pytest tests -q -k "setup or furniture or unfiled"` — expected green.

**Step 2: README.** In the "Or just dump" paragraph, after the ticked example, add:

```markdown
A sentence with lines under it is one thought — "the stack today:" and the list
beneath it arrive together. In a note that logs things over time she writes the
way you would in a journal: today's date once, in bold, then what you said:

    Wed 2 Sep 2026
    The stack did really well today once the trazodone wore off.
    • Concerta 36mg
    • Avmacol
    • PQQ

A note that collects things — recipes, ideas, names — gets plain bullets and no
dates. She decides which a note is the first time she files into it, and sticks
to it. A note she makes for you starts that way from its first line.
```

**Step 3: CLAUDE.md.** Add `layout.py` to the module table (`| \`layout.py\` | How a filed thought is laid out — journal (one bold date a day) or list; pure Markdown |`) and, in the Filer paragraph, one sentence: "A blank line is a run boundary set in code; within a run the model may hang lines under a lead (`part_of`), and `classify` validates it. Shape per note is cached in `filer.json["shapes"]`, first decision wins."

**Step 4: This plan's header.** Insert as line 3:

```markdown
**Status:** BUILT <date> on `feature/journal-filing` — <commit>. Tests: <n>.
```

**Step 5: Full suite, then commit**

```bash
.venv/bin/python -m pytest tests -q 2>&1 | tail -2
git add notron/workspace.py README.md CLAUDE.md docs/plans/2026-09-02-journal-filing.md
git commit -m "docs: journal filing — seed, README, CLAUDE.md, plan status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013eMNwa5bLqWv8YtL8zQD14"
```

---

## Task 8: Prove it live, then merge

**Step 1: Dry run against the real dump** (no write, one Super call):

```bash
cd /Users/m1labs/Dev/apps/juno && .venv/bin/python -m notron file --dry-run 2>&1 | tail -20
```

If the live dump has nothing unfiled, type two lines into `🧠 Brain Dump` first — a sentence, then two short lines directly under it, no blank — then rerun. Expected in the trace: `judging 3 line(s)`, one `✓ <note> — dry run`, and `Filed 1 thought`.

(If `notron file` has no `--dry-run` flag, check `.venv/bin/python -m notron file --help`; `filer.run(brain, dry_run=True)` is the call, and `tests/test_filer.py::test_the_cli_has_a_file_command` shows how the CLI is wired.)

**Step 2: Real run** on those lines:

```bash
.venv/bin/python -m notron file 2>&1 | tail -10
```

Then read the destination note back:

```bash
osascript -l JavaScript -e 'const N=Application("Notes"); const n=N.notes.whose({name:"<title>"})()[0]; console.log(n.body().slice(-1200))'
```

Expected: a `<div><b>Wed 2 Sep 2026</b></div>` (or the day it is), the sentence in its own `<div>`, one `<ul>` under it. No per-line stamp.

**Step 3: Reinstall the listener** so the running one picks up the new code (it imports at start):

```bash
.venv/bin/python -m notron listen --install 2>&1 | tail -3
```

**Step 4: Merge**

```bash
git checkout main && git merge --no-ff feature/journal-filing -m "Merge branch 'feature/journal-filing'" && git log --oneline -1
```

**Step 5: Report** — in the reply: what changed in one line, the exact note to open to see it, test count, and the one parked item (hand-tidying the old ten bullets in `Supps` is the user's call; nothing is ever deleted by Notron).
