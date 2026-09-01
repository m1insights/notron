# Brain Dump → Filer — Feature Spec (approved; BUILT 2026-09-01)

**Status:** built as specced — `notron/filer.py`, graph node `filer`, write mode
`mark` (Guard-proven add-only tick + receipt), `notron file`, listener auto-run
after 15 quiet minutes, `tests/test_filer.py`. One deliberate deviation: a tag
with words after it (`@notron file this: X`) files only that line; a bare
`@notron file these` files the lines typed under it. New notes go in
`NOTRON_FILING_FOLDER` (default Apple's "Notes"). Classification runs on **Super,
not Nano**: measured live, Nano took 43–235 s per call and copied glimpses into
titles; Super answered in 1.3 s, titles exact (`filer.TIER`).

**Goal:** One note called something like `🧠 Brain Dump` where the user throws
anything — supplement thoughts, app ideas, skincare brand notes — and Notron
sorts each line into the right master note. Capture takes zero decisions;
Notron does the organizing. This is the concrete form of the "Filer" skill from
the Mac GUI design (docs/design/DESIGN.md era, 2026-08-31 hackathon strategy).

**Persona check (Becky, 27, non-technical):** she will never pre-build a note
system. She *will* dump. The magic moment is opening `Supplements` and finding
last week's scattered thoughts already there.

---

## Approved decisions (locked 2026-09-01)

1. **Never delete silently.** Filing = copy the line into the master note,
   then mark the dump line as filed with a `✓ ` text prefix. Native Apple
   Notes checkboxes are OFF the table — scripting strips them (see
   `reference: apple-notes-scripting-limits`). The dump note is never
   truncated or cleared by Notron; the user clears it when they feel like it.
2. **No matching master note → propose, don't guess.** Notron suggests
   creating a new master note ("Want a `Skincare Brand` note for these 3
   items?") instead of shoving items into the nearest wrong bucket. Becky
   won't pre-create masters, so creation flows *from* the dump.
3. **Trigger = on-demand + after a dump session ends.** "Notron, file my
   brain dump" always works; automatic filing runs only after the dump note
   has been quiet for a while (session over), never on a raw timer — a timer
   files half-finished thoughts.
4. **Receipts.** Every filed item gets a one-line audit trail:
   `filed → Supplements`. Visible wins, and undo is possible because the
   original line is still in the dump note (just ✓-prefixed).

## Tagging

- `@notron` is the **primary, user-facing** tag (culturally familiar from
  social/Slack mentions). `#notron` keeps working — `mentions.py` already
  watches for it; `@notron` becomes an alias in the same scan.
- The tag is **not limited to the dump note**: `@notron file this` on a line
  in *any* note flags that line for the Filer. The dump note is just the
  place where the tag is implicit on every line.

## Shape of the build (sketch, ~1 day when green-lit)

- New graph node `filer`: reads unfiled dump lines, classifies each against
  the list of master notes (Nemotron Nano — title + first lines of each
  master as context), emits structured Actions. Model proposes; plain code
  (Guard) applies — same contract as `scheduler`/`doer`.
- Reuses `mentions.py` watch loop (crash-safe seen/pending state) for the
  dump note and for stray `@notron` tags.
- Writes via existing `notes.py` append path; master note title = first line
  (Apple Notes rule).

**Out of scope for v1:** filing images/attachments, multi-device conflict
handling, filing into folders (notes only).
