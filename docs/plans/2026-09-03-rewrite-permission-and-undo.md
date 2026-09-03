# Rewrite permission + undo — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: `superpowers:executing-plans`, task-by-task, TDD (`superpowers:test-driven-development`). Design doc:
> `docs/plans/2026-09-03-rewrite-permission-and-undo-design.md` — read it first, the decisions there are locked, don't re-litigate them mid-task. Tests use fake brains and monkeypatched Notes — no API key, no network, no real Notes touched. Never run a Notes-touching command while the background listener is mid-request (`CLAUDE.md`).

**Goal:** a note outside `🤖 NOTRON` may only be *added to* by default (invariant
#2, unchanged). A note the user has explicitly said yes to may be rewritten in
place instead. Every write to an existing note — whichever mode — can be
undone once, by tagging her, whether or not that note has rewrite permission.

**Run everything from `apps/juno`:** `.venv/bin/python -m pytest tests -q`
(297 tests green at the start — record the actual count before Task 1).

---

## Task 1: `notron/undo.py` — the one-level save/restore store

**Files:** create `notron/undo.py`, `tests/test_undo.py`

**Step 1 — failing tests:**
```python
from notron import undo

def test_save_then_pop_returns_the_body_once(tmp_path, monkeypatch):
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")
    undo.save("note-1", "<div>old</div>")
    assert undo.pop("note-1") == "<div>old</div>"
    assert undo.pop("note-1") is None          # one level — consumed

def test_save_ignores_an_empty_old_body(tmp_path, monkeypatch):
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")
    undo.save("note-2", "")                     # brand-new note, nothing to undo to
    assert undo.pop("note-2") is None

def test_a_second_save_overwrites_the_first(tmp_path, monkeypatch):
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")
    undo.save("note-3", "<div>v1</div>")
    undo.save("note-3", "<div>v2</div>")
    assert undo.pop("note-3") == "<div>v2</div>"
```

**Step 2 — implement** (mirror `mentions.py`'s `STATE`/load/save shape):
```python
"""One step back, per note. Not a history — a single saved copy of whatever
a note held immediately before Notron's last write to it, consumed the
moment it's used. See docs/plans/2026-09-03-rewrite-permission-and-undo-design.md
decision 4."""
from __future__ import annotations
import json, pathlib

STATE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "undo.json"


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(data))
    except OSError:
        pass


def save(note_id: str, old_body: str) -> None:
    if not old_body:            # nothing existed before this write — nothing to undo to
        return
    data = _load()
    data[note_id] = old_body
    _write(data)


def pop(note_id: str) -> str | None:
    data = _load()
    body = data.pop(note_id, None)
    if body is not None:
        _write(data)
    return body
```

**Step 3 — verify:** `.venv/bin/python -m pytest tests/test_undo.py -q`

## Task 2: `guard.py` — a `restore` mode, and an opt-in past the append rule

**Files:** modify `notron/guard.py`
**Test:** `tests/test_guard.py`

**Step 1 — failing tests:**
```python
def test_restore_is_allowed_outside_the_folder():
    v = guard.check(folder="Notes", title="Parking Garages", old_body="<div>new</div>",
                    new_body="<div>original</div>", mode="restore")
    assert v.allowed

def test_restore_still_refuses_an_empty_body():
    v = guard.check(folder="Notes", title="X", old_body="<div>y</div>",
                    new_body="", mode="restore")
    assert not v.allowed

def test_replace_outside_the_folder_needs_rewrite_allowed():
    blocked = guard.check(folder="Notes", title="X", old_body="<div>a</div>",
                          new_body="<div>b</div>", mode="replace")
    assert not blocked.allowed
    allowed = guard.check(folder="Notes", title="X", old_body="<div>a</div>",
                          new_body="<div>b</div>", mode="replace", rewrite_allowed=True)
    assert allowed.allowed
```

**Step 2 — implement:**
- Add `"restore"` to the accepted-modes tuple on line 55.
- `check()` gains `rewrite_allowed: bool = False`.
- Line 65's block becomes: `if outside and mode == "replace" and not rewrite_allowed:`.
- Skip the append-preserves-old-body check (line 71) and the added-text
  privacy scan (lines 87-94) for `mode == "restore"` — restoring throws away
  the current body on purpose, and the text being restored already lived in
  that note before Notron ever touched it (see design decision 6). Still
  hits the `📌 About Me` read-only check and the empty/size checks — those
  stay unconditional at the top of `check()`.

**Step 3 — verify:** `.venv/bin/python -m pytest tests/test_guard.py -q`

## Task 3: `executor.py` — capture before every write, add `restore()`

**Files:** modify `notron/executor.py`
**Test:** `tests/test_executor.py`

**Step 1 — failing tests:**
```python
def test_a_write_to_an_existing_note_saves_its_old_body(fake_notes, monkeypatch):
    saved = {}
    monkeypatch.setattr(undo, "save", lambda nid, body: saved.setdefault(nid, body))
    ex = Executor()
    ex.append("Some Note", "more text", folder="Notes")   # existing note in fake_notes
    assert saved   # non-empty: the pre-write body was captured

def test_restore_writes_the_body_back_verbatim(fake_notes):
    ex = Executor()
    ex.replace("Some Note", "new content", folder="🤖 NOTRON", rewrite_allowed=True)
    r = ex.restore("Some Note", "<div>original</div>", folder="🤖 NOTRON")
    assert r.ok
    assert fake_notes.body_of("Some Note") == "<div>original</div>"  # no markup.render applied
```
(Reuse whatever fake-Notes fixture `test_executor.py` already has — it exists,
this repo's Executor tests are already monkeypatched against `notes.py`.)

**Step 2 — implement:**
- New method:
  ```python
  def restore(self, title: str, raw_html_body: str, *, folder: str = workspace.FOLDER) -> WriteResult:
      """Put a note back exactly as it was — the undo path. `raw_html_body` is
      already-rendered HTML (what `undo.save` captured), never Markdown — it
      must not go through `markup.render`/`markup.to_html` a second time."""
      return self._apply(folder, title, raw_html_body, mode="restore")
  ```
- `replace()` gains `rewrite_allowed: bool = False`, threaded into `guard.check`.
- In `_apply`, for `mode == "restore"`: `new_body = body_markdown` (the literal
  arg, no rendering — same treatment as the existing `else` branch for
  `replace`, but skip `markup.render` entirely).
- Right before the `notes.write_body(note.id, new_body)` call (today's line
  105): `if note and mode != "restore": undo.save(note.id, old_body)` — every
  mode *except* restore itself (decision 4 lists append/insert/mark/replace,
  not restore — saving on a restore would let a second `@notron undo` pop a
  slot holding Notron's own overwritten body and write it right back, an
  undo/redo loop nothing could break), only when the note already existed —
  a brand-new note has nothing to save.
- `mode == "restore"` on a note that no longer exists is refused, same
  posture as the existing `mark`/`insert` guards on a missing note — "put
  this note back" has no meaning without a note.
- `guard.check(...)` call gains `rewrite_allowed=rewrite_allowed if mode ==
  "replace" else False` (restore doesn't need it — Task 2 already exempts
  `restore` from the block that flag controls).

**Step 3 — verify:** `.venv/bin/python -m pytest tests/test_executor.py -q`

## Task 4: `notron/rewrite.py` — per-note rewrite permission

**Files:** create `notron/rewrite.py`, `tests/test_rewrite.py` (mirror `library.py`'s test shape — same load/save pattern, same test style)

**Step 1 — failing tests:**
```python
def test_a_note_is_not_rewrite_allowed_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    assert rewrite.allowed("note-1") is False

def test_allow_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    rewrite.allow("note-1")
    assert rewrite.allowed("note-1") is True

def test_default_for_new_notes_defaults_to_ask(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    assert rewrite.default_for_new_notes() == "ask"
```

**Step 2 — implement:** `load()`/`allowed(note_id)`/`allow(note_id)`/
`default_for_new_notes()`/`set_default_for_new_notes(value)`, `.notron/rewrite.json`
shape `{"allow": [...], "default_new": "ask"|"always"|"never", "chosen_at": iso}`
— same persistence style as `notron/library.py` (read this file first for the
exact load/save idiom, don't reinvent it).

**Step 3 — verify:** `.venv/bin/python -m pytest tests/test_rewrite.py -q`

## Task 5: `nodes.py` + `graph.py` + `state.py` — wire `undo` and `organize` in

**Files:** modify `notron/state.py`, `notron/nodes.py`, `notron/graph.py`
**Test:** `tests/test_nodes.py`, `tests/test_graph.py`

**Step 1 — failing tests** (shape, not exhaustive — follow existing
`test_nodes.py` patterns for router/filer tests already in the file):
```python
def test_router_matches_undo_in_code_no_model_call(fake_brain_that_errors_if_called):
    state = State(request="@notron undo", reply_to=("Parking Garages", "Notes", 3))
    out = nodes.router(state, brain=fake_brain_that_errors_if_called)
    assert out.intent == "undo"

def test_undoer_restores_when_something_was_saved(monkeypatch):
    monkeypatch.setattr(undo, "pop", lambda nid: "<div>old</div>")
    state = State(intent="undo", reply_to=("Parking Garages", "Notes", 0))
    out = nodes.undoer(state)
    assert out.writes and out.writes[0].mode == "restore"

def test_undoer_says_nothing_to_undo_when_the_slot_is_empty(monkeypatch):
    monkeypatch.setattr(undo, "pop", lambda nid: None)
    state = State(intent="undo", reply_to=("Parking Garages", "Notes", 0))
    out = nodes.undoer(state)
    assert "nothing to undo" in out.answer.lower()

def test_organizer_replaces_when_the_note_has_rewrite_permission(monkeypatch, fake_brain):
    monkeypatch.setattr(rewrite, "allowed", lambda note_id: True)
    state = State(intent="organize", reply_to=("Parking Garages", "Notes", 0), request="organize this")
    out = nodes.organizer(state, brain=fake_brain)
    assert out.writes[0].mode == "replace"

def test_organizer_inserts_and_asks_when_not_allowed(monkeypatch, fake_brain):
    monkeypatch.setattr(rewrite, "allowed", lambda note_id: False)
    state = State(intent="organize", reply_to=("Parking Garages", "Notes", 0), request="organize this")
    out = nodes.organizer(state, brain=fake_brain)
    assert out.writes[0].mode == "insert"
    assert "keep the note clean" in out.answer.lower() or "keep it clean" in out.answer.lower()
```

**Step 2 — implement:**
- `state.py`: `Write` gains `rewrite_allowed: bool = False`; `"restore"`
  documented in the `mode` comment alongside the existing three.
- `nodes.py`:
  ```python
  UNDO_WORDS = re.compile(r"(?i)^\s*(?:please\s+)?undo\b|revert\s+(?:that|this)\b")
  ORGANIZE_WORDS = re.compile(r"(?i)\borganize\s+this\b|clean\s+(?:this|it)\s+up\b|"
                              r"tidy\s+(?:this|it)\s+(?:note\s+)?up?\b")
  ```
  add `"undo"` and `"organize"` to `INTENTS`; in `router()`, check
  `UNDO_WORDS`/`ORGANIZE_WORDS` the same way and in the same place as
  `FILE_WORDS` (code first, model never asked) — `undo` and `organize` only
  make sense with `state.reply_to` set, so when the words match but
  `reply_to` is `None`, fall through to the ordinary model classification
  instead of forcing the intent (a bare "clean this up" in the Ask note
  should still get a normal answer).
  - `undoer(state, brain=None)`: no-op unless `state.intent == "undo"`;
    resolve `title, folder, _ = state.reply_to`; `note =
    notes.find_note(folder, title)`; `old = undo.pop(note.id) if note else
    None`; if `old`: `state.writes.append(Write(title=title, folder=folder,
    mode="restore", markdown=old))`, `state.answer = "Done — put it back the
    way it was."`; else: `state.answer = "Nothing to undo here."`; either way
    `state.writes.append(_reply(state))` so the note shows her turn, same as
    every other reply.
  - `organizer(state, *, brain)`: no-op unless `state.intent == "organize"`;
    ask Super for the full cleaned note (new small system prompt, sibling to
    `PLANNER_SYSTEM` — "you are given one whole note; reply with the entire
    note, cleaned and organized, in Markdown, keeping every fact; output
    nothing else"); resolve the note via `state.reply_to`; branch on
    `rewrite.allowed(note.id)`:
    - allowed → `Write(title=title, folder=folder, mode="replace",
      rewrite_allowed=True, markdown=cleaned)`, `state.answer = "Cleaned it
      up."`
    - not allowed → `Write(..., mode="insert", markdown=cleaned)` via the
      existing `_reply`-style anchoring, plus one appended line: "Want me to
      keep this note clean in place next time, instead of adding below?
      Reply `yes` and I will." A `yes` under that line is picked up next
      pass the same way the Filer's `yes` already is (`conversation.py`
      already has the matching primitive — reuse it, don't add a second one)
      and calls `rewrite.allow(note.id)`; wire that check at the top of
      `organizer` before the model call, mirroring how `filer` checks for a
      pending proposal before doing new work.
- `writer()`'s early-return list (`if state.intent in ("ignore", "plan",
  "file")`) gains `"undo"` and `"organize"` — both nodes already produce
  their own reply.
- `graph.py`: add `"undoer": nodes.undoer` and `"organizer": nodes.organizer`
  to `NODES`; extend `ORDER`/`EDGES` to `... "filer" -> "organizer" ->
  "undoer" -> "writer" -> "executor"`; `run()`'s special no-`brain`-required
  list doesn't need `undoer` added since it takes `brain=None` by default and
  `run()` always passes `brain=brain` positionally-as-kwarg today — check the
  existing call shape in `run()` (line 82-84) and match it, `undoer` behaves
  like `retriever`/`researcher` (accepts but may ignore `brain`).
- `nodes.executor()`: add a branch for `w.mode == "restore"` →
  `ex.restore(w.title, w.markdown, folder=folder)`, and thread
  `rewrite_allowed=w.rewrite_allowed` into the existing `w.mode == "replace"`
  branch's `ex.replace(...)` call.

**Step 3 — verify:** `.venv/bin/python -m pytest tests/test_nodes.py tests/test_graph.py -q`

## Task 6: Full-suite regression + the Invariants walk

**Files:** none (verification only)

- `.venv/bin/python -m pytest tests -q` — compare the pass count against the
  baseline recorded at the top of this plan.
- This diff touches `guard`, `executor`, and adds a new write mode — walk
  CLAUDE.md's **Invariants** list by number and state how each still holds:
  #2 (append-only outside the folder) now has one explicit, per-note,
  user-granted exception — confirm the default (no entry in
  `rewrite.json`) still refuses `replace` exactly as before; #3 (no model in
  the write path) — confirm `organizer`'s model call produces a *proposal*
  (`state.writes`), never calls `notes.write_body` itself; #4 (every write
  logged) — confirm `restore` mode logs through the same `_log` call as
  every other mode (it does, nothing in Task 3 skips it).

## Task 7 (later, not blocking): onboarding screen for the global default

**Files:** `mac/Sources/Notron/...` (new small section — read
`docs/design/04-onboarding-flow.md` and `DesignSystem.swift` tokens first,
per `CLAUDE.md`'s Design rule)

Not test-driven (no Swift test target in this repo, same precedent as
`docs/plans/2026-09-02-onboarding.md`'s Swift task). One screen or one
section on an existing screen: a fabricated before/after example, three-way
choice `ask each time` / `always clean it up` / `never — always add below`,
written to `rewrite.json`'s `default_new` via the existing `Core.run`
subprocess bridge (`notron rewrite --default ask|always|never`, a thin new
CLI flag over Task 4's module — add that flag as a Task 7 sub-step, it needs
no test beyond the module tests Task 4 already wrote).

**Sequenced last on purpose:** everything through Task 6 already fixes the
Parking Garages case end-to-end for a `#notron`/CLI user with zero Mac app
changes. This task is the convenience layer, not the safety layer — fine to
build whenever, including "not yet" for a single-user pre-launch app.

## Effort

| Task | Time |
|---|---|
| 1 — `undo.py` | ~45 min |
| 2 — `guard.py` restore + rewrite_allowed | ~30 min |
| 3 — `executor.py` capture hook + `restore()` | ~1 hour |
| 4 — `rewrite.py` | ~45 min |
| 5 — `nodes.py`/`graph.py`/`state.py` wiring | ~half a day (the biggest single task — two new nodes, one new prompt) |
| 6 — full-suite + invariants walk | ~20 min |
| 7 — onboarding screen (later) | ~half a day |

Core (1–6) ≈ 1.5 days. Task 7 whenever it's wanted.

**Out of scope:** everything Section 3 of the design doc already excludes —
multi-step undo history, guessing the note for a bare "undo" in the Ask note.
