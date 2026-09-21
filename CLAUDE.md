# NOTRON — working notes for Claude

Notron is a private, always-on personal AI on the Mac:
**Siri speaks. Nemotron decides. Plain code authorizes. A contained sandbox acts.
The result comes back into Notes.**

It is being extended from an Apple Notes personal AI into an orchestration layer,
with Notes as the grant and the durable receipt, and one SwiftUI hero screen (the
task board) to watch, approve and audit. New integration work is planned, not
shipped. Read `docs/production/README.md`,
`docs/production/submission-strategy.md` and
`docs/production/repositioning-design.md` (v2) before implementation; these
supersede older organizer-only scope and roadmap ordering. Preserve existing
Notes guards.

Built for the Nebius × NVIDIA Global AI Hackathon, Personal AI track. Official
deadline: **October 30, 2026, 10am PDT / 1pm EDT**; the internal demo target is
**October 6**. The rules require runtime use of Nebius Token Factory or AI Cloud and
an NVIDIA open-source model, not exclusive inference through Nebius. See
https://nebiusglobalaihackathon.devpost.com/rules (checked September 10).

**NVIDIA Nemotron owns the reasoning and every decision — tier, budget and latency
included.** The judging criteria name Nemotron in three of four axes, so this is a
product constraint, not a preference: do not move the interesting reasoning to
another provider, and do not let an external agent become the source of a decision.

Claude Code, Codex and similar are **optional, disclosed, bring-your-own capability
inside a contained run** — never the decision-maker, never required for the workflow
to succeed, never silently configured. Their credentials and provider usage are
separate opt-in integrations qualified in R00.

The defensible half of this product is the **Apple bridge**. There is no Apple Notes
API, no Siri personal-context API, and no third-party access to another app's
intents; Apple's own Notes does not use the public `.notes` schema path (48 of 48
App Intents declare empty `assistantDefinedSchemas`, measured on macOS 26.2). The
seams Notron uses — index-addressed bulk AppleScript queries, EventKit read
directly, headless `Shortcuts Events`, per-binary TCC identity — are the moat. Treat
the Notes limitations documented below as structural constraints, not as bugs
awaiting more effort.

## Status: pre-launch

No production users. The developer is the only person running this, against
their own Notes/Reminders/Calendar, to find rough edges before anyone else touches
it. Follow the dated roadmap gates: working demo October 6, submission candidate
October 23, deadline October 30. Keep safety and recovery intact; reduce optional
scope when evidence slips. A developer preview is not a public paid release.

## Secure runtime gate (P01 Task 3)

September 9 branch integration: P01/P02 and newer main attachment/calendar/UI
work are reconciled. Historical prototype sections below mentioning plaintext
attachment caches or the ten-minute `booked.py` store are superseded: attachments
use encrypted storage and private temporary native-tool files; retries use durable
operation IDs. `Brain.see` validates live source metadata/policy before every upload.
Photo fallback captures Ask before inference and retains original source checks.
See `docs/production/handoffs/2026-09-09-branch-integration.md` for current evidence.

Protected commands now require injected Keychain credentials and AES-GCM storage.
The signed bridge/startup integration is pending P06, so default startup pauses;
`.env` credentials and plaintext cache fallback are no longer supported. Sensitive
state and mood live in `~/Library/Application Support/com.m1labs.notron`, not the
old repository cache. Background stdout/stderr are discarded; fixed local diagnostic
counters retain seven days. Do not use historical `.notron/listen.log` instructions
below for new builds. Legacy migration is an explicit offline operation; originals
and encrypted backups remain until separate acceptance. See
`docs/production/evidence/P01-secure-storage.md` and the Task 3 shared contract.

## Design

Before building or changing ANY UI in the `mac/` companion app, read `docs/design/DESIGN.md`
and use its tokens (`mac/Sources/Notron/DesignSystem.swift`). Never introduce a new
colour, font size, radius, or spacing value that is not in that file — extend the
token set there first. The Notes-native (light) skin is the default everywhere;
the Operator (dark) skin is reserved for the Skills & Plugins screen only, since
that screen is Advanced-tier only. Key screens and the magic moment are specced
in `docs/design/02-screens.md`; the first-run onboarding sequence (welcome →
permissions → how to talk to her → start listening → pin her notes → handoff
to "Your notes") is specced in `docs/design/04-onboarding-flow.md`. The pin
step exists because **Apple Notes exposes no `pinned` property to any
script** — not AppleScript, not Shortcuts — so Notron can neither pin a note
nor tell whether one is pinned; that screen instructs and opens
(`notron pins`, `notron library open <id>`) and never confirms.

## Commands

```bash
.venv/bin/python -m pytest tests -q      # synthetic fixtures; no live API key/network
.venv/bin/python -m notron setup           # create the 🤖 NOTRON folder in Notes
.venv/bin/python -m notron index           # embed all the user's notes (~2 min)
.venv/bin/python -m notron index --attachments  # …also look at pictures, listen to recordings
.venv/bin/python -m notron ask "..."       # one-shot, for testing
.venv/bin/python -m notron listen          # foreground listener
.venv/bin/python -m notron listen --install  # background listener via launchd
.venv/bin/python -m notron morning         # the daily routine
.venv/bin/python -m notron graph           # print the node graph
.venv/bin/python -m notron permissions     # can she reach Notes, Reminders, Calendar?
.venv/bin/python -m notron agenda          # today, this week, and what's outstanding
.venv/bin/python -m notron reflect         # learn from her own answers that missed
.venv/bin/python -m notron file            # sort 🧠 Brain Dump into the right notes now
.venv/bin/python -m notron library         # which notes she may file into / never reads
```

`--dry-run` on `ask`, `plan`, `care` and `morning` walks the graph and writes nothing.

## Running the listener while developing

**A listener started from a tool-run shell dies the moment that shell returns.**
Hours were lost to this: the code looked broken because every test listener was
being killed after printing its startup lines. Use `notron listen --install`
(launchd) only after the P06 signed startup gate passes. Current background
stdout/stderr are discarded; `.notron/listen.log` is a historical plaintext path,
not a current diagnostic source.

**Do not run other Notes commands while the listener is working.** Notes serves one
script request at a time; a slow query from a second process wedges the app for
both. Requests serialise behind a file lock, so they queue rather than fail — but
a long query still delays the listener.

## Architecture

A declared graph of specialised nodes, not one agent in a loop:

```
watcher ─► router ─► retriever ─► researcher ─► agenda ─► planner ─► scheduler ─► doer ─► filer ─► organizer ─► undoer ─► writer ─► executor
  │          │          │            │           │          │           │          │        │        │           │        │          │
no LLM     Nano      no LLM       Tavily      no LLM       Super       Nano     no LLM   Super     Super     no LLM   Super   no LLM + Guard
```

Nodes decline work they do not own. Model tiers live in `brain.DEFAULT_MODELS`
(`fast` = Nemotron Nano 30B, `smart` = Super 120B, `deep` = Ultra 550B); embeddings
are Qwen3-Embedding-8B because Nebius serves no NVIDIA embedding model.

| Module | Responsibility |
|---|---|
| `applescript.py` | The only place that shells out to `osascript`. Holds the lock. |
| `eventkit.py` | Apple's calendar/reminder store, read directly. 700× faster than the apps. |
| `notes.py` | Apple Notes read/write. Bulk queries only — see Performance. |
| `reminders.py` | Create and complete reminders; never delete. |
| `calendar.py` | Bounded date-range reads; create-only. |
| `when.py` | Dates, moved between model, Python and AppleScript without drift. |
| `permissions.py` | Which apps she is actually allowed to read — including write-only. |
| `attachments.py` | Pictures, recordings and files — the part of a note that is not text |
| `markup.py` | Markdown ⇄ the HTML subset Notes actually renders |
| `notedoc.py` | A note as addressable blocks; provably lossless inserts |
| `conversation.py` | Reads a note as turns; finds what she has not answered |
| `mentions.py` | Sweeps every note for `#notron` / `@notron` |
| `filer.py` | The Brain Dump: sorts lines into the user's own notes, ticks them, proposes new notes |
| `layout.py` | How a filed thought is laid out — journal (one bold date a day) or list; pure Markdown |
| `library.py` | Per-note home / read only / ignore choices; the one place "never reads it" lives |
| `rewrite.py` | Per-note permission to rewrite in place instead of only adding — off by default |
| `undo.py` | One saved copy per note, consumed on use — the safety net under rewrite |
| `booked.py` | What she just created outside Notes, so a retry cannot book it twice |
| `guard.py` | The single choke point for every write |
| `executor.py` | Applies writes. No model runs here, ever. |
| `graph.py` / `nodes.py` / `state.py` | The graph and what flows along it |
| `privacy.py` | Keeps credentials and private notes out of answers |
| `index.py` / `retrieval.py` | Semantic search over the user's notes |
| `care.py` / `daily.py` | "Take Care of Notron" and the morning routine |
| `reflect.py` | The self-improvement loop — lessons from answers that missed |

## Invariants — do not break these

1. **`📌 About Me` is never written by Notron.** It is the user's instruction note.
2. **Outside `🤖 NOTRON`, ordinary filing/replies preserve existing text.**
   Explicit rewrite/restore paths are described below; none is an atomic Apple
   write. `notedoc.preserves` checks the candidate body, proving
   character by character that every original character survives, in order, and
   that new text landed between elements rather than inside a sentence.
3. **No model runs in the write path.** The model proposes, the Guard judges in
   plain code, a dumb executor applies. Nothing calls `notes.write_body` except
   `Executor`.
4. **Write receipts are best effort** in the registered, readable `📊 Log`.
   Successful writes log afterward; inaccessible logs and crashes can leave gaps.
   P02 Task 2 records Notes operations and observed revisions in the encrypted-payload ledger;
   Schema 3 binds complete known source/context/destination provenance to each
   payload; deletion or denial of any contributor purges it. Legacy payloads with
   unknown complete provenance are conservatively purged during migration, while
   operation identities survive. Asynchronous receipt reconciliation remains Task 3.
5. **Policy exclusions precede outbound redaction.** Every model/search/embedding
   input uses `outbound.py`; supported secret patterns are filtered by `privacy.py`.
   Redaction cannot recognize every secret. See `SECURITY.md` for limits.
6. **A calendar event may only ever be created.** No move, no delete, no update —
   there is no code in `calendar.py` that could do it.
7. **A reminder may be created or completed, never deleted.** Done is not gone.
8. **The Filer never deletes a line.** Filing copies the line into the master note
   and ticks the original — `✓ magnesium → Supplements` — via the `mark` write
   mode, which `notedoc.marks_between` proves added only a `✓ ` after a line's
   opening tag and a receipt before its closing tag. No fitting master note means
   a proposal in the dump and a wait for `yes`; it never guesses a bucket, never
   creates a note unasked. Lines that look like credentials are never filed and
   never shown to the model.
9. **An ignored note is excluded from AI reads.** Explicit user-requested local
   preview remains separately authorized. `library.user_notes()` is the way the core
   lists the user's notes; `index`, `retrieval`, `mentions`, `care` and `filer` all
   go through it, and `index.search` re-checks at query time because the index may
   be older than the choice.
10. **An attachment is only ever read.** Nothing in `attachments.py` creates,
   renames, moves or deletes one. Notes offers `delete`; there is no code here
   that calls it.
11. **An ignored note's attachments are never touched** — not listed, not
   extracted, not described. Invariant 9 covers the note; this covers what hangs
   off it. `attachments.on_note` refuses *before* asking Notes, and `fetch`
   asks the library again at the moment of use, because a list of attachments
   held in a variable is older than a choice the user made since.
12. **She never implies she has seen something she has not.** A file she cannot
   read is named in the prompt with "you have NOT seen these", and anything that
   fails to open — Notes busy, a bad decode, past the `nodes.MAX_LOOKS` cap —
   falls back into that list rather than disappearing from both. Silently losing
   a file is the outcome that reads as having looked.
13. **No write ever lands on a note holding a picture.** Apple Notes hands an
   embedded image back as inline base64 and discards it when the body is
   written again, so a write there deletes the photo — silently, and after
   `notedoc.preserves` has already passed. Leaving the `<img>` markup out of
   the write does not help: tested 2026-09-06, the attachment behind it is
   deleted too, so there is no scripted write that keeps a picture.
   `markup.holds_media` is the check,
   `guard.check` the only place it is enforced, for every mode including
   `restore`; she answers in `📥 Ask Notron` instead of going quiet, and
   `mentions.answered_away` then retires that question — keyed by the question,
   not the note, and persisted in `seen.json`. Without that the tag owes an
   answer for ever, `pending` never clears, and `scan` re-reads the note (1.8MB
   of base64) on every twenty-second sweep.

## Performance — measured on 358 notes, 1,263 reminders and 1,757 events

These are not micro-optimisations; they decide whether a background agent is
possible at all.

**Notes: bulk AppleScript queries, addressed by index.**

| Doing it the obvious way | Doing it right |
|---|---|
| `repeat with n in notes of f` — **106s** | `name of every note of f` — **0.2s** |
| Finding a folder by walking `every folder` — **20s** | `folder 3` by index — **0.2s** |
| First call after Notes goes idle — **~40s** | Absorb once at startup (`notes.warm_up`) |

Notes also allows two folders with the same name, so looking one up by name can
silently return the first one twice. Address folders by index — **and check the
name that comes back.** Notes keeps folders in alphabetical order, so a folder
created anywhere above hers shifts her index down one, and a cached index then
addresses the folder *next to* the one asked for and returns its notes as if
they were hers. No error, no slow read, just the wrong folder. On 2026-09-03 an
empty second "Notes" folder pushed `🤖 NOTRON` from position 5 to 6 and she read
**Recently Deleted** as her own folder for two minutes — long enough to lose
sight of `📊 Log` and recreate it fourteen times. `notes.resolve` is now the
only way to reach a folder: it verifies the name `folder_at` hands back (free —
it arrives in the same request) and re-asks once if it does not match.
`create_note` goes through it too, so a write can never land in a different
folder than reads come from, and it no longer falls back to `make new folder`
when a name matches nothing — that fallback is what created the duplicate.
`ensure_folder` is the one place a folder is ever created, and it refreshes the
index the moment it does.

**Attachments: the same two rules, measured again.** An attachment leaves no
trace in the note body — a note holding a voice memo reads back as
`<div><br><br></div>`, 39 characters, and `markup.to_text` returns `''`. So
every part of Notron that works from a body is blind to it by construction, and
`attachments.py` is the only place that asks Notes the second question.

| Doing it the obvious way | Doing it right |
|---|---|
| `attachments of nt` then `name of atts` — **`-1728`** | `name of every attachment of nt` — **0.30s** |
| Per-note query over a folder — **87s** for 222 notes | `attachments of every note of f` — **0.18s** |

The bulk form does **not** flatten, contrary to a first reading: it returns one
sub-list per note, in the same order as `id of every note of f`, so zipping the
three lists names the note each file hangs off. (What does come back `missing
value` is `id of container of …`.) Two traps beyond the ones above: a bare
`nm as text` raises `-1700` the moment an inline table is in the list — Notes
models a table as an attachment whose `name` is `missing value`, and most
attachments in a real library are tables, not files — and a folder addressed by
a stale index returns another folder's files with no error, so `in_folder`
checks the name that comes back exactly as `notes.resolve` does.

**Reminders and Calendar: never AppleScript. EventKit.**

| Doing it the obvious way | Doing it right |
|---|---|
| 23 open reminders via the Reminders app — **65.7s** | via EventKit — **0.093s** |
| 7-day calendar window via the Calendar app — **26.0s** | via EventKit — **0.025s** |

`whose` filters in those two apps walk every object that has ever existed in them,
so cost scales with the user's history, not the answer. Reminders cannot even return
properties from a filtered set: `name of rs` raises
`Can't get name of {reminder id "x-apple-reminder://…"}`. There is no tuning that
closes a 700× gap — use `notron/eventkit.py`.

**And EventKit's speed is worthless if the process was never granted access.**
Measured 2026-09-06/07 (`docs/spikes/2026-09-06-eventkit-request-under-osascript.md`):
from the terminal the status was `3` (full) and reads worked; from the **launchd
listener** it was `0` (not determined), `calendarsForEntityType` returned an empty
array, a seven-day window returned zero events, and **nothing raised**. Every
`agenda:` line in `.notron/listen.log` read `125 chars of real commitments` —
the exact length of "Nothing in the calendar today" plus "Nothing outstanding
in Reminders". She had never once seen a real event from the background
listener. **That grant has since appeared** — re-measured 2026-09-08 from a
job shaped like `watch.plist`, the listener reads 5 calendars and 23 reminders
— but not because of anything in this repo, and every fresh install starts at
`notDetermined`. The honesty layer below is insurance, not a workaround. When
re-measuring, go through `eventkit.run`: a bare `osascript` from the same job
still reads zero, because TCC answers per responsible process.

Asking does not fix it. `requestFullAccessToEvents…`,
`requestFullAccessToReminders…` and the legacy
`requestAccessToEntityTypeCompletion` all resolve as functions under JXA and
**none of them calls back** from launchd — the same failure mode as Speech, and
for the same reason: `osascript` carries no usage string. (The legacy one
appears to work from the terminal only because access is already granted there
and it has nothing to ask.) So do **not** build an `ensure_access()`; it is a
ten-second stall that buys nothing. The grant has to come from a real bundle —
`mac/Info.plist` now carries the three usage strings — and until the listener
has a signed identity of its own (`docs/production/plans/06-mac-distribution.md`)
it stays blind unless run in the foreground from an approved terminal.

What the code does about it: `permissions.cached()` (a failing check re-asked
every `RECHECK_SECONDS`, a passing one held for the process), `nodes.agenda`
saying *"I cannot read your Calendar"* in the words the model gets rather than
letting an empty read read as a free day, and the listener naming the gap in
`.notron/listen.log` at startup.

**Every date crossing into EventKit is pinned.** An `NSDateFormatter` with a
fixed `dateFormat` and no locale reads the Mac's region, so `yyyy` under a
non-Gregorian region is not the year we mean — the wrong date is written, or
`dateFromString` returns nil and the save fails silently. There is exactly one
formatter constructor, `pinned()` in `eventkit.DATES` (`en_US_POSIX` + explicit
Gregorian calendar), no script builds its own, and a test enforces both. The
time zone is deliberately *not* pinned: 2pm means 2pm where the user is.

## Nemotron gotchas

- **Reasoning is billed against `max_tokens` and is not the answer.** Ask for 500
  tokens and it can spend all 500 thinking, returning empty content with
  `finish_reason: "stop"` and no error. `brain.ask` adds `REASONING_HEADROOM` and
  retries once at double budget. Nothing turns reasoning off on this endpoint —
  `reasoning_effort="none"`, `/no_think` and `chat_template_kwargs` were all tried.
- **The vision model reasons *inside* `content`.** `openbmb/MiniCPM-V-4_5` (used
  by `brain.see`, still on Nebius) emits `<think>…</think>` ahead of its answer
  rather than in the separate `reasoning` field Nemotron uses — so the headroom
  fix alone is not enough, and the first real call had three paragraphs of the
  model talking itself through a picture on their way into a note. `see` strips
  it, and treats an *unterminated* block as having spent the whole budget
  thinking: dropped, and asked again at double rather than printed.
- **JSON mode truncates mid-string.** `brain.ask_json` tries the raw text, a fenced
  block, the outermost braces, then rebuilds from complete pairs. The router falls
  back to a safe intent rather than stopping the graph.

## Apple Notes limits

- HTML renders: headings, `<b>`, `<i>`, `<ul>`, `<ol>`, `<table>`.
- `<a href>` loses its href. Emit bare URLs.
- Native tap-to-tick checklists **cannot** be written by script. `☐`/`✅` text is the
  workaround — and the reason Reminders integration is the highest-value next step.
- **A picture survives being read and does not survive being written.** The
  `body` getter serialises an embedded photo as inline
  `<img src="data:image/heic;base64,…">` — 1,867,394 characters for one
  camera-roll image against 33 characters of real text — and the `body` setter
  silently discards it, leaving `<div><br><br></div>`: no image, no attachment,
  no error. Both directions measured 2026-09-06. So **any** scripted write to a
  note holding a picture deletes the picture, at any size, and
  `notedoc.preserves` cannot see it happen: every character NOTRON sends really
  is preserved, and Notes throws the image away after the proof passes. The
  Guard refuses every mode on such a note (`markup.holds_media`) and she
  answers in `📥 Ask Notron` instead. Do not "fix" this by raising
  `MAX_BODY_CHARS`: that was the only thing standing in the way, an image under
  ~146KB clears it, and `ARG_MAX` is 1,048,576 so a 1.87MB body cannot reach
  `osascript` at all. **And do not try stripping the `<img>` markup out first
  — that was tested on 2026-09-06 and it destroys the photo too.** Writing a
  body with the image markup removed took the note from 1,867,394 characters
  to 198 and left `attachments of note` empty: Notes treats the body it is
  handed as the whole truth and deletes the attachment object behind it. There
  is no scripted write that keeps a picture. Refusing is not a conservative
  choice here, it is the only correct one.
- A note's title is always the first line of its body. Every writer leads with it.
- A launchd agent needs its own macOS Automation approval for Notes; until the user
  grants it, its first request hangs rather than failing.
- Three separate permissions, each with its own silent failure. **Notes** uses
  AppleScript Automation, and an unapproved app *hangs* rather than failing.
  **Calendar and Reminders** use EventKit, whose `write only` state is the nasty
  one: it raises nothing and reports one calendar and zero events, so a blocked
  calendar is indistinguishable from a free week. `notron permissions` reads the
  numeric status instead of trusting a query.
- EventKit reads are asynchronous and JXA has no `await`. A script that does not
  pump `NSRunLoop.runModeBeforeDate` exits before the callback fires and returns
  nothing, every time, with no error.
- The historical prototype used JXA and Terminal permission grants instead of a
  compiled Swift helper. Signed identity and EventKit/Automation prompt behavior
  must be verified in P06; Python tests do not establish native permission safety.
- A dated reminder needs an explicit `EKAlarm`. A due date alone shows in the app
  but does not notify, and a reminder that does not buzz is a note with a circle.
- **Apple Notes writes replace a whole body and are non-atomic.** P02 Task 2
  binds each proposed write to a note ID and SHA-256 revision captured before
  inference. The executor serializes local transactions, saves encrypted undo,
  rechecks source readability after admission reads before retaining payloads,
  checks policy/revision and all contributing source permissions after final reads,
  requires the operation still APPLYING, and verifies the observed
  body afterward. Stale replace/restore refuses; stale append/insert/mark needs
  unambiguous captured anchors, and journal appends refuse stale layout. Rich
  replacement input is conservatively refused; organizer delivers separate text
  or refusal to the existing registered plain Ask note without touching the rich
  original. Unsafe/unavailable Ask delivery leaves the request needing review. Notes mutations have no automatic timeout
  retry. Remote iCloud/editor writers do not honor the local lock: the final
  read/write gap remains a race, including undetectable overwritten remote text.
  Reconciliation, undo snapshot lifecycle and worker ownership remain Tasks 3/4/6.

## The Brain Dump (`filer.py`)

`🧠 Brain Dump` is a shared note in her folder: the user throws in one thought per
line, and Notron files each into the right one of their own notes. Trigger is
on-demand (`notron file`, or "file my brain dump" / "file this: …" anywhere she
listens — routed in code by `nodes.FILE_WORDS`, no model asked) or automatic once
the set of unfiled lines has sat unchanged for `watch.DUMP_SETTLE` (15 min): a
timer would file half a thought. **Super** picks a title from the list of master
notes (titles + a glimpse from the index, vault/private notes excluded) — not Nano:
measured 2026-09-01, Nano took 43 s on a two-line prompt (235 s on five) and padded
titles with the glimpse; Super answered in 1.3 s with the title exact. Plain code
copies (`append`), ticks (`mark`), and — only after a `yes` typed under the
proposal — creates a note in `filer.FILING_FOLDER` (`NOTRON_FILING_FOLDER`,
default "Notes"). `.notron/filer.json` remembers verdicts and pending proposals so
a dump that is only waiting on the user costs zero model calls per poll
(`filer.worth_a_pass`). `@notron file this: …` on a line in any note goes through
the same path and ticks that line where it sits; `conversation.unanswered` treats
a ticked turn as answered, or the listener would re-ask it forever. A blank line
is a run boundary set in code; within a run the model may hang lines under a lead
(`part_of`), and `classify` validates it. Shape per note is cached in
`filer.json["shapes"]`, first decision wins.

## Your notes (`library.py`)

Once per note the user says **home** (the Filer may file into it), **read only**
(default) or **ignore** (excluded from AI reads, including tags; a user may still
explicitly preview it locally). Choices live in `.notron/library.json` keyed by note id; the Mac app's "Your
notes" window writes it (it lists notes via `notron library scan`), `notron library`
edits it from the terminal. A "start from [year]" cutoff applies only to notes the
user never looked at (`decided`); a row they flipped wins. Zero homes means zero
automatic filing destinations. Unknown notes are denied unless validated policy
explicitly enables new-note reads (off by default). Missing/corrupt policy pauses
AI processing; `notron library recover` explicitly restores a validated backup.
A note created after an explicit `yes` becomes a home, including from zero homes.
Read only permits a one-request tagged reply in that readable note; Ignore still
excludes tagged notes. Setup registers system note IDs; names alone grant nothing. The pre-fill (`library.suggest`) is plain
code: recency + list-shaped body + short title, `privacy.py` seeds the ignores,
duplicate titles keep only the newest as a home.

The window is a split: the list on the left, and on the right **what is actually
inside the selected note** (`notron library peek <id>`), because a decade-old note
titled "CRITICAL" tells you nothing and nobody triages 219 of those with the Notes
app open alongside. Arrow keys move, 1/2/3 set home/read only/ignore, "Open in
Notes" (`notron library open <id>`) is the escape hatch. Ignored notes preview on
purpose — the user is looking, not the model, and that is the note they most need
to see before agreeing it stays ignored. **A preview is held back** when the title
trips `privacy.py` or the body holds anything credential-shaped, until the user
presses "Show it anyway" (`--reveal`); the risk being managed there is the room the
user is sitting in, not the model. `privacy.is_key_dump` catches the unlabelled
case (that "CRITICAL" note is six bare Obsidian recovery codes) and is deliberately
**not** wired into `contains_secret` — a run of order numbers must cost one click in
a panel, never a write the Guard refuses.

## Rewrite permission and undo (`rewrite.py`, `undo.py`, `organizer`, `undoer`)

Invariant #2 — outside `🤖 NOTRON` she may only add — has exactly two narrow,
deliberate carve-outs, both gated to a single chokepoint. **`organizer`**
("organize this" / "clean this up" / "tidy this note up") asks Super for the
whole note, cleaned; by default the result lands *underneath* what the user
wrote, exactly like any other reply, plus one line offering to keep that note
clean in place next time. That offer must be answered with a tagged
`@notron yes` — outside her folder a reply requires an explicit tag, while
policy-approved retrieval can read untagged content — and only
then does `rewrite.allow(note.id)` fire, which is the only thing that ever
sets `Write.rewrite_allowed = True`, which is the only thing that lets
`guard.check`'s outside-folder block pass a `replace`. A `yes` only confirms
the offer directly above it (`organizer._offer_precedes`), never an older,
already-superseded one lower in the same note. An empty or implausibly short
model answer never becomes the note — the one path here that can overwrite a
user's own words outright refuses to write rather than risk it.

**`undoer`** (`@notron undo` / `revert this`, said inside the note itself —
bare "undo" in 📥 Ask Notron asks which note rather than guessing) now uses
revision-bound encrypted snapshots. `undo.peek` does not consume; a restore must
match the exact saved snapshot and its successful post-write revision, then
verify its resulting body before `undo.consume(note_id, snapshot_id)` removes it.
A staged backup is durable before every ordinary mutation, promoted on verified
success, retained on unknown outcomes, and discarded on known pre-write refusal.
The previous committed slot survives refused writes. The restore receipt rides
inside the one proven restore and cannot refill the slot.

Any intervening edit, including typing an undo command, requires a separate
plain-text recovery copy. The exact tagged `undo recovery copy <snapshot-id>`
command confirms that snapshot in that source note. Copies go through the Guard
and Executor, preserve the original snapshot and later user words, and grant no
automatic filing-home permission. Recovered nonblank lines carry the answered
marker so old tags cannot become new actions, even if the copy is later selected.
Legacy encrypted body-only snapshots remain recovery-only; no revision is guessed.

P02 recovery persists exact copy/write identities and content before effects.
Graph checkpoints reuse successful inference for receipt repair. A saved action
is separate from its Notes receipt (`State.receipt_complete`); exact operation
references reconcile EventKit saves and exact revision evidence reconciles Notes.
Uncertain results require review. No same-title scan may read unselected note
bodies to recover a lost creation ID. Audit retries use bounded metadata only.
See `docs/production/handoffs/2026-09-05-P02-tasks-3-4.md` for current evidence and
remaining native/worker limitations.

Full design: `docs/plans/2026-09-03-rewrite-permission-and-undo-design.md`.
Onboarding's global default (`rewrite.default_for_new_notes`) has a `mac/`
screen: a sheet on "Your notes," shown once, right after its own Done button,
until the user has picked once (`RewriteDefaultState.needsChoice` in
`RewriteDefault.swift`, reading `.notron/rewrite.json` directly). Sequenced
*after* "Your notes" per decision 3 — that screen prevents real damage
(misfiling), this one is a nice-to-have default. Writes through the same
`Core.run` bridge as everywhere else, via a thin `notron rewrite --default
ask|always|never` CLI flag over `rewrite.set_default_for_new_notes`.

## Attachments (`attachments.py`)

Apple Notes keeps a picture, a recording or a dropped-in file completely out of
the note's HTML body, so until this module existed she answered questions about
a photo as though the photo were not there — fluent, confident, and
indistinguishable from having looked. Three things follow from that:

*She says what she cannot read.* Every answering surface asks what the note
carries on the way to an answer (never on an idle poll — the Ask note is read
every five seconds and this costs 0.4s). What she cannot put into words is
listed in the prompt under "you have NOT seen these", and anything that fails
for any reason falls back into that list.

*Everything becomes text, and text is untrusted.* A `.txt` is read directly; a
picture goes to `brain.see` (Nebius, MiniCPM-V, downscaled with `sips` — a
macOS built-in, no new dependency); a voice memo is transcribed by macOS itself.
All three go through `privacy.py` on the way in — **a photo of a password is the
one secret `privacy.py` cannot catch**, because it only ever sees text, and the
moment the model reads it out loud it *is* text. Results are cached beside the
file, so a picture asked about twice costs one look, and the cached words are
what `notron index` makes searchable.

*Speech is on-device, and its permission is backwards.* `SFSpeechRecognizer`
through JXA, the same route EventKit takes and for the same reason (a compiled
helper's identity changes on every rebuild; a background listener can never
answer a prompt). Never call `requestAuthorization` — under `osascript` the
callback never fires, because there is no usage string in its bundle, so a
design that waits for a grant hangs forever. Recognising a *local file*
on-device needs no grant: it works with `authorizationStatus` sitting at `0`.
`requiresOnDeviceRecognition = true` is a Notron privacy constraint, not an
exclusive-provider hackathon rule — without it Apple may send audio to its servers. This is the one
permission `notron permissions` reports by capability rather than by its numeric
status, because here the number says "not determined" forever while
transcription works perfectly.

Cost is bounded in two places. `nodes.MAX_LOOKS` caps how many pictures one
answer will look at — a note of fifteen screenshots is otherwise fifteen vision
calls at ~3.5s each inside a listener poll — and `notron index` describes or
transcribes nothing without `--attachments`, indexing only what she has already
read. Full design and the measurements behind it:
`docs/plans/2026-09-05-attachments.md`.

## The self-improvement loop (`reflect.py`)

Runs inside `notron morning` and on demand via `notron reflect`. Plain code finds
the evidence (corrections and re-asked questions in the Ask note — no model, so a
quiet day costs zero calls); Super proposes ≤3 lessons, each forced to quote the
transcript verbatim (string-checked in code); a **separate** Nano call verifies
them against 📌 About Me and the existing lessons; the Guard writes the survivors
to 📖 Lessons (capped at 12). Every prompt then carries the lessons *below* the
standing instructions — the model is instructed to prioritize About Me, without a
guarantee, and the user can delete lessons by editing the note. Current reflection
state uses encrypted Application Support storage; `.notron/reflect.json` is legacy.

## The Ask-note chat contract

- Her turn: opens with `conversation.QA_RULE` (a light en-dash break — never
  `RULE`'s em dashes, which mean something different: see below), then
  `**Notron:**` on its own line, prose italicised by `markup.voice`
  (structure — headings, lists, tables — stays upright), closed with `———`.
  `conversation.SIGNATURE` matches that bold signature; change both or neither.
- **She answers to "Norton".** macOS autocorrects "Notron" the first time you
  type it, and she is addressed by name in every note she is tagged in — an
  unrecognised tag is silence, indistinguishable from her being asleep.
  `conversation.TAG` accepts `notron|nortron|norton|notrn` and is the only place
  the name is ever matched; `\b` still holds, so "@nortonantivirus" is not her.
  **Her own writing never changes** — she signs `**Notron:**`, `SIGNATURE` is
  untouched. Onboarding also teaches the Mac's speller the word
  (`teachTheSpellerHerName`), which fixes the Mac but not the phone.
- The router may never return `ignore` for notes/manual triggers — everything on
  those surfaces is addressed to her, and silence makes the listener re-ask the
  model forever. A question that still produces no write gets `Watcher.MAX_TRIES`
  attempts, then rests for `COOLDOWN` seconds.
- An insert re-finds its question by text at write time (`notedoc.locate`) —
  the block index is only a hint, because the user keeps typing while she thinks.

## Conventions

- Plain functions and dataclasses. No agent framework — the graph is the point.
- Comments explain **why**, especially where a fix encodes a bug that actually
  happened. Several tests are named after real failures; keep them that way.
- Tests run with no API key, no network and no real Notes app — and, since
  2026-09-07, no real Calendar, Reminders or speech recogniser either. Those
  three do not go through `applescript.run`, so the fake Notes app never covered
  them: `eventkit._osascript` and `attachments.speech_available` are refused or
  faked autouse, and `booked.STATE` is pointed at a `tmp_path` because it is live
  state in the developer's own checkout that decides whether a reminder is booked
  at all. Fake brains, and a
  fake Notes app (`conftest.FakeNotesApp`) under **every** test, autouse — it
  answers the same AppleScript the real one does, so index addressing and name
  verification are genuinely exercised, and anything else (a write, a `show
  note`, an EventKit script) fails loudly naming the script. Until 2026-09-03
  seventeen tests read the developer's own 358 notes because they never patched
  `applescript.run`; live work that day also left fourteen junk `📊 Log` notes
  in that library. A test must never be able to touch the real one.
- User-facing strings are plain and warm. She is an assistant, not a pet.

## P01 security contract

Read `SECURITY.md` and `docs/production/evidence/P01-security-boundaries.md` before
changing trust boundaries. Model output remains data: scheduler fields are validated,
operation types are fixed, dynamic JXA uses JSON argv, and AppleScript uses argv.
No arbitrary tool, policy grant, or executable source may come from model output.
No sandbox or production-readiness claim; P06 signed startup still pauses real
processing. Private reporting route and provider retention remain release decisions.
Tests globally block unmocked subprocesses, DNS and socket connections.
