# Rewrite permission + undo — ship report

Merged to `main` at `9842baf`. Branch `feature/rewrite-undo`, worktree removed.
Listener reinstalled (`notron listen --install`) and confirmed live, watching 302 notes.

Tests: 300 → 361 passed (61 new), 0 failed, in `main`.

## What shipped

- `notron/undo.py` — one-level save/restore per note, consumed on use.
- `notron/rewrite.py` — per-note opt-in to rewrite-in-place, default off.
- `notron/guard.py` — new `restore` mode (exempt from the outside-folder block
  and the secret scan — the body was already the user's own); `rewrite_allowed`
  opt-in on `replace`.
- `notron/executor.py` — every successful write except `restore` itself now
  saves an undo slot; new `Executor.restore()`; refuses restoring a note that
  no longer exists.
- `notron/nodes.py` + `graph.py` — two new nodes, `organizer` ("organize this"
  / "clean this up") and `undoer` ("undo" / "revert this"), wired into the
  graph between `filer` and `writer`.

## Invariants walk (CLAUDE.md)

- **#1** (About Me never written) — holds unconditionally; the read-only
  check sits above every mode, restore included.
- **#2** (outside-folder append-only) — holds, with exactly two narrow,
  single-chokepoint carve-outs: `mode="restore"` (only ever produced by
  `undoer`, only ever fed a body `undo.pop` actually returned) and
  `rewrite_allowed=True` (only ever set by `organizer`, only when
  `rewrite.allowed(note.id)`, which only `rewrite.allow()` sets, which only
  fires behind a tagged confirmation matched to the specific offer it answers).
- **#3** (no model in the write path) — holds; `organizer`'s model call only
  ever produces a `Write`, never calls `Executor` directly.
- **#4** (every write logged) — holds; `restore` reaches the same `_log` call
  as every other mode, success or blocked.
- **#5–#9** — untouched; no other module in this diff's path.

## Caught in code review, fixed before merge

1. **Critical** — an empty or truncated model answer on `organizer`'s cleanup
   path could have replaced an opted-in note with just its title. Now refuses
   to write below half the source length; answers "I couldn't tidy that
   safely" instead.
2. Two routing regexes (`UNDO_WORDS`, `ORGANIZE_WORDS`) were unanchored due to
   a `|` splitting the whole pattern — "we should revert that decision,
   @notron what do you think" would have fired a real, unchecked restore.
   Anchored to match `FILE_WORDS`' existing precedent.
3. The undo receipt only closed the *last* unanswered tagged turn in a
   restored note — a second tagged ask further up would have kept re-firing
   the write that was just undone. Now ticks every open tagged turn first.
4. A bare "yes" was matched against the *last* Notron turn anywhere in the
   note, not the one it actually answers — an old, superseded offer could be
   granted by a `yes` meant for something else. Now checks the turn directly
   above the confirmation.
5. `undo` typed inside 📥 Ask Notron would restore the Ask note itself and
   report a false "done". Now asks which note, per the design doc's own
   stated intent.
6. Chasing (3) surfaced a genuine pre-existing bug in `conversation.py`
   (shared by the Filer, not introduced by this branch): a ticked *list item*
   reads `"• ✓ …"`, not `"✓ …"`, so the "already answered" check never
   recognised it. Fixed with a regression test. One narrower, deeper gap
   remains and is documented, not fixed: a tagged line sharing a bulleted
   list with untagged siblings still reads as unanswered (a block-vs-line
   granularity mismatch between `notedoc` and `conversation.py`).

## One incident during verification, self-resolved

Running the full suite in the main checkout hit a **pre-existing** test bug
(`test_a_tagged_note_keeps_being_reported_until_it_is_answered`, on `main`
since before this branch) that didn't isolate `mentions.STATE`, and it
overwrote the real `.notron/seen.json` (listener scan state — not Notes
content) with two lines of fake test data. The live listener process held
correct state in memory and rewrote the real file on its next poll a few
minutes later — confirmed recovered, all 34KB of real state intact, no data
lost. Fixed the test and added a repo-wide autouse fixture isolating
`mentions.STATE`, matching the ones already added for `undo.STATE` and
`rewrite.STATE`, so no future test can repeat this.

## Also fixed along the way

- `pyproject.toml` — scoped setuptools package discovery to `notron/`; `mac/`
  (a `Package.swift` + `Sources/` dir) was tripping a flat-layout guard and
  blocking a fresh `pip install -e .[dev]` for anyone starting a new worktree.

## Not built

**Task 7** — a `mac/` onboarding screen for the global rewrite default
(ask/always/never for new notes). Explicitly scoped in the plan as optional
and sequenced last; core functionality (undo + rewrite permission +
organizer) already works end-to-end for a `#notron`/CLI user without it.
~Half a day whenever it's wanted.

## Try it

Tag `@notron organize this` on a messy note. She proposes a cleaned version
and offers to keep it clean in place — reply `@notron yes` once, and future
organize requests on that note rewrite it directly. `@notron undo` on any
note she just touched puts it back.
