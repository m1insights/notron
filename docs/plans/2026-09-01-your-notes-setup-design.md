# "Your notes" — onboarding step 2 + Settings screen (design, approved 2026-09-01)

**Goal:** a messy, years-old notes library must not make Notron file things into
the wrong place or read what it should not. Setup lets the user say, in one
screen, where they keep things, what Notron must never read, and how far back
she looks — and change it any time later.

**Why now:** first live day of the Filer. A THC log line was filed into an
App Store listing note because its title contained "supplements"; the user has
three notes called "Supps" and a decade of supplement experiments. The mess only
hurts *filing destinations* — search still finds the right note for questions —
so setup picks destinations; it never deletes history.

**Persona check (Becky):** she cannot audit 300 notes. Notron pre-fills every
choice; she unticks. Positive selection ("these ~10 are where I keep things") is
a smaller decision than negative ("which of 300 should be ignored").

## Decisions (locked)

1. **Not an Apple Notes folder.** People keep a password note in their main list
   and want Notron to skip *that note*. Ignore is per note, chosen in the GUI.
2. **Three states per note:** **Home** (a filing target), **Read only** (the
   default — searchable for questions, never filed into), **Ignore** (Notron never
   reads it: not for filing, not for questions, not even a `@notron` tag inside it).
3. **Date cutoff is a convenience, not a rule.** "Start from [year]" sets every
   note last edited before it to Ignore in one move; per-row choices override.
4. **Tidy scan (near-duplicate merge) is parked.** Embeddings exist to do it
   later; it would never delete, only propose a merged note.
5. **Editable forever.** Same screen under Settings → "Your notes". Changes apply
   on the next pass; nothing already filed is touched.

## Section 1 — the screen

- One list of every note, search box on top, pre-sorted by Notron's guess.
  Row = title · folder · last edited · a three-way chip **Home / Read only / Ignore**.
- Pre-fill (plain code, no model): ~10 **Home** = edited recently and often,
  list-shaped body, unique title, not private. **Ignore** pre-set on anything
  `privacy.py` already recognises (passwords, journal, medical…) with a line
  "we spotted 4 notes that look private — set to Ignore". Everything else Read only.
- "Start from [2026 ▾]" at the top.
- Duplicate titles ("Supps" ×3): inline "3 notes share this name — which is the
  one?" picker; the others drop to Read only.
- One **Done** button. Skippable. New notes created later default to Read only;
  a note Notron creates after a `yes` in the Brain Dump becomes a Home automatically.
- Menu-bar quick view gains one line: "12 homes · 9 ignored".
- Fits `docs/design/02-screens.md` as **1b. First-run — your notes (onboarding
  step 2)**; standard list + chip components from DESIGN.md, Notes-native skin.

## Section 2 — storage and core behaviour

- **`.notron/library.json`**, written by the Mac app, read by the Python core:
  `{"homes": [note ids], "ignore": [note ids], "start_from": "2026-01-01" | null,
  "chosen_at": iso}`. Keyed by note id — stable across renames. Not a Notes note:
  a 300-row note is easy to corrupt from a phone.
- New module `notron/library.py`: `load()`, `is_home(note)`, `is_ignored(note)`
  (id in ignore, or modified before `start_from`), `suggest()` (the pre-fill
  ranker) and `scan()` (every note + suggested state, as JSON for the GUI).
- **Homes → `filer.masters()`** offers only homes when any are set; with none set
  (CLI users) today's heuristic stays. A proposal accepted with `yes` appends the
  new note's id to `homes`.
- **Ignore + start_from → dropped everywhere:** `index.build`, `retrieval.search`,
  `mentions.Scanner.changed`, `care`, and the Filer's candidate list. "Never reads
  it" is literal — one helper, called in each of those five places.
- **How the GUI lists notes:** it shells out to the bundled core —
  `notron library scan` prints JSON — rather than talking to Notes from Swift.
  One Notes automation approval (onboarding step 1) then covers both.
- CLI parity: `notron library` prints the current state; `notron library --home
  "Supps" --ignore "Old passwords" --start-from 2026` for terminal users.

## Section 3 — effort

| Piece | Time |
|---|---|
| `library.py` + the five ignore hooks + Filer homes + tests | half a day |
| "Your notes" screen in `mac/` (list, search, chip, year picker, duplicate picker, writes the JSON) | a day |
| Menu-bar count line + Settings entry | 30 min |

Total ≈ 1.5 days. Core first (it is testable without the GUI and fixes today's
misfile for the CLI user immediately); screen second.

**Out of scope:** Tidy scan; per-folder rules; syncing choices between Macs.
