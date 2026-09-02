# Journal Filing — parked follow-ups

**Status:** items 1-3 (the three bugs) fixed 2026-09-02 on
`feature/journal-filing-followups`. Item 4's CLAUDE.md one-liner fixed
alongside them; its other two bullets and item 5 remain parked, as written
below. Tests: 298.

Found during the Fable review of `feature/journal-filing` (merged to `main` at
`cc2a691`, 2026-09-02). All rated minor or cosmetic — none blocked the ship.
Fixed at review time instead: the important finding (a two-level part chain
dropping its bottom line) and a malformed-`shapes`-reply crash. See
`docs/plans/2026-09-02-journal-filing.md` for the feature itself.

None of these lose or duplicate a line today — each is a stuck-until-something-
changes edge case, not active data loss. Pick them up opportunistically; no
deadline.

---

## 1. A stale `judged` entry can hide a line from `_approve`'s landed set

**File:** `notron/filer.py`, `_approve`, the `landed` computation near the end
(~line 669).

**What happens:** `landed` is built by scanning the *whole* `judged` cache for
`kind == "note"` entries whose title is in `out.created` — not from the
`waiting` items that actually just got filed this call. If some other line's
digest carries a stale `{"kind": "note", "title": T}` from a note that no
longer exists (deleted, dropped past the 200-master cutoff, or excluded from
homes since), and a *new* proposal this pass happens to create a note also
named `T`, that stale line is swept into `landed` and silently dropped from
`rest` — unticked, unreported, zero model calls, `worth_a_pass` says nothing
to do. It self-heals the next time something else touches the dump (judged
still points at `note/T`, `T` is real now), so it is a stuck line, not a lost
one.

**Fix:** compute `landed` from `waiting` directly instead of `judged`:
`{w.digest() for w in waiting} | {p.digest() for w in waiting for p in w.parts}`,
accumulated per created title.

---

## 2. A shape can cache wrong, and wrong is forever

**File:** `notron/filer.py`, `_shape_for` (~line 458) and `classify`'s
shape-collection loop (~line 378).

**What happens:** `classify` writes the model's `"shapes"` reply into the
`shapes` dict keyed by whatever string the model used, verbatim. `_shape_for`
looks that dict up by the *canonical* title (the one `_match` already resolved
`note`/`new` verdicts against). If the model's `shapes` key differs by case,
quoting, or padding from the canonical title — exactly the kind of thing
`_match` exists to paper over for note titles — the lookup misses, `_shape_for`
falls back to `layout.LOG`, and **caches that guess forever** (first decision
wins is deliberate; a wrong first decision is not). A list-shaped note like
Recipes would get a bold date stamped on every filing from then on, with no
code path to notice or correct it short of hand-editing `filer.json["shapes"]`.

**Fix:** only cache in `_shape_for` when `title in said` — return
`said.get(title, shapes.get(title, layout.LOG))` uncached otherwise, so a miss
this pass can still resolve correctly next pass. Better: resolve `shapes` keys
in `classify` through the same `_match(title, by_key)` used for `note`
verdicts before writing them, so the key always matches canonical.

---

## 3. A proposal record can dedup a lead but still double-file its parts

**File:** `notron/filer.py`, `_file` section 3, the
`if it.as_dict() not in record["items"]:` guard (~line 582).

**What happens:** dedup is by full `as_dict()` equality, which since this
branch now includes `parts`. If a part line is edited or deleted from the dump
between two passes that both see the same lead as `"new"`, the lead's
`as_dict()` differs (different `parts`) and the guard doesn't catch it —
`record["items"]` gets two entries for what is conceptually the same lead, and
a later `yes` files it twice. (A related case — `near` shifting because a line
was inserted above it — predates this branch entirely.)

**Fix:** dedup by `digest()` and replace the earlier entry in place, rather
than comparing the full dict.

---

## 4. Cosmetic / already-true, worth a look sometime

- **A lead line that starts with Markdown syntax renders as structure, not
  text.** `layout.py` emits the lead at the start of a Markdown line, so a
  dump line beginning `# `, `- `, `1. `, `|`, or `---` becomes a heading,
  bullet, list item, table row, or rule in the destination note instead of
  the sentence it was. Pre-existing in how `Executor.append` has always
  worked — not new to this feature, just newly more visible since a lead can
  now carry more of the user's own punctuation.
- **Parts never show up in `out.left` or the "Left N lines" summary** —
  a declined lead or a refused append reports the lead but the trace never
  says anything about the parts riding along with it.
- **`CLAUDE.md`'s Commands section still says "278 tests"** in the
  `pytest tests -q` comment — stale since before this feature (was already
  wrong at 279); now 295. One-line fix whenever someone's in that file.

---

## 5. Performance: `_existing_text` reads a note the Executor is about to read anyway

**File:** `notron/filer.py`, `_existing_text` (~line 467), called from
`_entry_markdown`.

**What happens:** for a **log**-shaped note, `_entry_markdown` calls
`_existing_text` — `notes.find_note` + `notes.read_body`, roughly three
AppleScript round-trips — just to check whether the note already ends under
today's date heading. `Executor._apply` reads the same note's body again a
moment later to build the actual write. Both reads happen on the path the
listener blocks on. (List-shaped notes already skip this, from the
code-simplifier pass in the same branch.)

**Fix:** thread the body `Executor._apply` is about to read down into
`_entry_markdown` instead of re-reading, or have `Executor.append` accept a
transform function that sees `existing_text` and returns the markdown to
append — bigger change, worth its own small design pass rather than a quick
patch.
