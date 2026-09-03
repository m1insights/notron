# Rewrite permission + undo — Design

**Goal:** today, outside `🤖 NOTRON`, she may only *add* — invariant #2. That's
why the Parking Garages note now holds the user's original pasted mess **and**
her clean table: she inserted her answer below the tag, exactly as designed.
Good invariant, annoying result. This gives willing users a real "clean this
note up in place" option without weakening the default for anyone who hasn't
asked for it, and gives everyone a one-step way back if a rewrite is wrong.

**Status:** pre-launch, one user (the developer). No rush — get the shape
right for real users later rather than the fastest thing to ship now.

## Decisions (locked)

1. **Opt-in, per note, default off.** The safe default (append/insert,
   today's behavior) never changes for a note nobody has opted in. Rewrite is
   something a note earns, not a global switch.
2. **Ask with the real note, not a canned example, and ask in the moment —
   not buried in onboarding.** The user's own instinct was right that a
   real before/after builds more trust than a description — but the *most*
   real before/after is the note itself, the first time she'd actually
   rewrite it. Onboarding runs before any note has been touched, so it can
   only ever show a fabricated sample; that's a weaker version of the same
   idea. Mirrors a pattern this repo already ships and has already tested:
   the Filer's proposal — she shows the work, the user types `yes`, nothing
   is guessed. Concretely: the first time a tagged request on a note outside
   her folder would read better as a full rewrite than an appended reply,
   she inserts *both* — her cleaned version, and one line asking "want me to
   keep the note clean like this from now on, instead of adding below? Reply
   `yes` and I will." `yes` sets that one note to rewrite-allowed; anything
   else, or no reply, leaves it as read-only-append forever (asked once, not
   nagged).
3. **Onboarding still gets a settings-level choice, but as a global default
   for new notes going forward** ("when I tidy up a note for you, should I
   ask each time, or just do it?") **using a fabricated sample note**,
   because a global default needs to exist before the first real note is
   touched. The per-note real-example ask (#2) always wins on a given note —
   the global default only decides what a *fresh* note starts at and whether
   the per-note ask happens at all.
4. **Undo is per note, one level, restores exactly what she overwrote.**
   Every successful write to an existing note — append, insert, mark or
   replace — saves that note's immediately-prior body before writing. A
   second write to the same note overwrites the saved copy; there is only
   ever one step back, per note, matching what the user asked for. Restoring
   consumes the saved copy — undo twice in a row on the same note does
   nothing the second time, with a plain "nothing to undo" rather than an
   error. This also quietly fixes the open question from the "before/after
   page" conversation: rewrite ships *with* its safety net from day one,
   never as a separate, riskier follow-up.
5. **Undo triggers the same way everything else does: tag her.**
   `@notron undo` (or `#notron undo`) on the note she just touched. No new
   surface, no Mac app screen required for this part. Already safe against
   "undo" appearing in ordinary text — every trigger in this app requires
   the `#notron`/`@notron` tag outside the Ask note (`conversation.py`'s
   `TAG` regex, enforced in `mentions.py`), so a meeting note that happens to
   contain the word never reaches the router at all.
6. **Restoring is not "rewriting."** Guard's outside-folder block exists to
   stop *new*, AI-authored content from replacing the user's words without
   permission. Undo puts back words that were already live in that exact
   note a moment ago — it needs no rewrite permission, and it's exempt from
   the append-preserves-old-body check that a normal `append`/`insert` needs,
   because by definition it's throwing away the current body on purpose.

## Section 1 — core (Python, `notron/`)

- **`notron/rewrite.py`** — the fourth per-note preference module, alongside
  `library.py`. `.notron/rewrite.json`: `{"allow": [note_id, ...],
  "default_new": "ask" | "always" | "never", "chosen_at": iso}`. `allowed(note)
  -> bool`, `allow(note_id)`, `default_for_new_notes()`. Deliberately its own
  file, not a fourth `library.json` list — home/read-only/ignore is about
  *where the Filer may put things*; rewrite-allowed is a different question
  a Home, a Read-only, or an Ignore-adjacent note can each independently answer.
- **`notron/undo.py`** — `.notron/undo.json`: `{note_id: {"body": "<html>",
  "at": iso}}`. `save(note_id, old_body)` (no-op if `old_body` is empty — a
  brand-new note has nothing to undo *to*, undo doesn't mean "delete the note
  you just created"), `pop(note_id) -> str | None` (read-and-clear — the
  "one level" behavior lives entirely in this function).
- **`guard.py`** — `check()` gains `mode="restore"`: exempt from the
  outside-folder replace block and the append-preserves-old-body check (see
  decision 6); still refuses an empty body, over-length body, and a write to
  `📌 About Me` (belt and suspenders — she never captures an undo entry for
  a note she never writes, so this never actually fires, but it costs
  nothing to keep the same floor under every mode). `check()` also gains
  `rewrite_allowed: bool = False`; the existing outside-folder `replace`
  block only fires when `not rewrite_allowed`.
- **`executor.py`** — `_apply` calls `undo.save(note.id, old_body)` right
  before `notes.write_body(...)`, for every mode, whenever `note` already
  existed. New public method `Executor.restore(title, raw_html_body, folder)`
  → `_apply(..., mode="restore")`, where `new_body = raw_html_body` directly
  (no `markup.render`/`markup.to_html` — the saved copy is already the exact
  HTML Notes held, round-tripping it through the Markdown renderer would
  mangle it). `Executor.replace` gains `rewrite_allowed` and threads it into
  `guard.check`.
- **`nodes.py`** — two additions:
  - `UNDO_WORDS` regex (same style as `FILE_WORDS`), matched in `router()`
    before the model is asked — this is a fact ("put it back"), never a
    judgment call, so it stays out of the classifier same as filing.
    New intent `"undo"`, added to `INTENTS`. Only meaningful when
    `state.reply_to` names a specific note (i.e. said via a tag inside that
    note, not in `📥 Ask Notron`); bare "undo" in the Ask note replies asking
    which note, rather than guessing.
  - New node `undoer`: `state.intent != "undo"` → no-op; else resolve the
    note from `state.reply_to`, `undo.pop(note.id)`, and either
    `state.writes.append(Write(title=title, folder=folder, mode="restore",
    markdown=old_body))` or set `state.answer` to the "nothing to undo" line.
    Slots into `graph.py` right after `filer`, before `planner` — same tier
    as every other no-model, code-only node.
  - `organizer` node (the "clean this note up" path): recognizes
    `ORGANIZE_WORDS` ("organize this", "clean this up", "tidy this note")
    the same code-routed way. Asks Super for the *whole note, cleaned* (not
    a reply — closer to `planner`'s prompt shape than `writer`'s). Then:
    `rewrite.allowed(note)` → `Write(mode="replace", rewrite_allowed=True)`;
    not allowed → today's behavior unchanged (`Write(mode="insert")`, the
    cleaned text appended below, via the existing `_reply` path) **plus**
    the one-line ask from decision 2, appended to the same write.

## Section 2 — surfaces

- **In-Notes proposal (decision 2):** no new UI. Uses the exact mechanics
  the Filer's `yes`-confirmation already exercises in production —
  `conversation.unanswered` sees the reply, a plain `yes` line under it is
  the only thing `organizer` (next pass) needs to check before calling
  `rewrite.allow(note.id)`.
- **Onboarding global default (decision 3):** one new screen or one section
  added to an existing one in `mac/` — a fabricated before/after ("Here's a
  note the way you typed it → here's how she'd tidy it, in place, if you let
  her"), radio choice `ask each time` / `always` / `never`, written straight
  to `.notron/rewrite.json`'s `default_new`. `docs/design/04-onboarding-flow.md`
  and `DesignSystem.swift` tokens apply, same as every other onboarding
  screen — read them before building this. Sequenced *after* "Your notes" in
  the flow, not before: it's a nice-to-have default, "Your notes" is the
  screen that prevents real damage (misfiling).

## Section 3 — effort

| Piece | Time |
|---|---|
| `undo.py` + `restore` mode in guard/executor + tests | ~2 hours |
| `rewrite.py` + guard's `rewrite_allowed` param + tests | ~1 hour |
| `undoer` node + router wiring + tests | ~1–2 hours |
| `organizer` node + in-Notes proposal flow + tests | ~half a day |
| Onboarding screen in `mac/` (global default only) | ~half a day |

Total ≈ 2 days. Core (undo + rewrite permission + organizer, all testable
with fake brains and monkeypatched Notes, no GUI needed) first — it already
fixes the Parking Garages case for a `#notron` / CLI user. Onboarding screen
last, and genuinely optional for the developer's own single-user testing
right now; sequence it whenever, not before the core.

**Out of scope for this round:** undoing *which* of several recent writes
when more than one happened close together (single slot, most-recent-only,
per decision 4); a "history" of every past version (that is what a real
backup system would be — this is a deliberately small safety net, not one);
saying "undo" bare in `📥 Ask Notron` and having her guess the note.
