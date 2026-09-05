# NOTRON — working notes for Claude

A personal AI agent that lives inside the user's Apple Notes. They type in Notes;
she reads, thinks on NVIDIA Nemotron via Nebius, and writes back into Notes.

Built for the Nebius × NVIDIA Global AI Hackathon (Personal AI track, due
**2026-10-30**). Two hackathon rules constrain every choice: **all inference must
run on Nebius Token Factory**, and **at least one NVIDIA open model must be used**.
Do not swap the model provider.

## Status: pre-launch

No production users. The developer is the only person running this, against
their own Notes/Reminders/Calendar, to find rough edges before anyone else touches
it. No deadline pressure beyond the hackathon date above — prefer the correct
long-term design over the fastest thing to ship, and it's fine to land a feature
in stages (e.g. safe-default now, riskier opt-in once its safety net exists).

## Secure runtime gate (P01 Task 3)

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
permissions → how to talk to her → start listening → handoff to "Your notes")
is specced in `docs/design/04-onboarding-flow.md`.

## Commands

```bash
.venv/bin/python -m pytest tests -q      # synthetic fixtures; no live API key/network
.venv/bin/python -m notron setup           # create the 🤖 NOTRON folder in Notes
.venv/bin/python -m notron index           # embed all the user's notes (~2 min)
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
| `markup.py` | Markdown ⇄ the HTML subset Notes actually renders |
| `notedoc.py` | A note as addressable blocks; provably lossless inserts |
| `conversation.py` | Reads a note as turns; finds what she has not answered |
| `mentions.py` | Sweeps every note for `#notron` / `@notron` |
| `filer.py` | The Brain Dump: sorts lines into the user's own notes, ticks them, proposes new notes |
| `layout.py` | How a filed thought is laid out — journal (one bold date a day) or list; pure Markdown |
| `library.py` | Per-note home / read only / ignore choices; the one place "never reads it" lives |
| `rewrite.py` | Per-note permission to rewrite in place instead of only adding — off by default |
| `undo.py` | One saved copy per note, consumed on use — the safety net under rewrite |
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
   asynchronous receipt reconciliation remains Task 3.
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

## Nemotron gotchas

- **Reasoning is billed against `max_tokens` and is not the answer.** Ask for 500
  tokens and it can spend all 500 thinking, returning empty content with
  `finish_reason: "stop"` and no error. `brain.ask` adds `REASONING_HEADROOM` and
  retries once at double budget. Nothing turns reasoning off on this endpoint —
  `reasoning_effort="none"`, `/no_think` and `chat_template_kwargs` were all tried.
- **JSON mode truncates mid-string.** `brain.ask_json` tries the raw text, a fenced
  block, the outermost braces, then rebuilds from complete pairs. The router falls
  back to a safe intent rather than stopping the graph.

## Apple Notes limits

- HTML renders: headings, `<b>`, `<i>`, `<ul>`, `<ol>`, `<table>`.
- `<a href>` loses its href. Emit bare URLs.
- Native tap-to-tick checklists **cannot** be written by script. `☐`/`✅` text is the
  workaround — and the reason Reminders integration is the highest-value next step.
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
  checks policy/revision immediately before mutation and verifies the observed
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
bare "undo" in 📥 Ask Notron asks which note rather than guessing) puts a note
back to what it held before Notron's immediately-prior write, one level,
consumed on use. Every successful write except `restore` itself saves the
prior body (`Executor.apply_write`); `restore` is exempt from the outside-folder
block and the append-preserve/secret-scan checks (`guard.py`) because it puts
back words that were already live in that exact note a moment ago — not new,
AI-authored content. The receipt rides inside the single `restore` write
(a second write would refill the one undo slot with the wrong body) and ticks
every unanswered tagged turn the restored body still holds, or the watcher
would hand the note straight back and redo the very write that was just
undone.

Full design: `docs/plans/2026-09-03-rewrite-permission-and-undo-design.md`.
Onboarding's global default (`rewrite.default_for_new_notes`) has a `mac/`
screen: a sheet on "Your notes," shown once, right after its own Done button,
until the user has picked once (`RewriteDefaultState.needsChoice` in
`RewriteDefault.swift`, reading `.notron/rewrite.json` directly). Sequenced
*after* "Your notes" per decision 3 — that screen prevents real damage
(misfiling), this one is a nice-to-have default. Writes through the same
`Core.run` bridge as everywhere else, via a thin `notron rewrite --default
ask|always|never` CLI flag over `rewrite.set_default_for_new_notes`.

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
- Tests run with no API key, no network and no real Notes app. Fake brains, and a
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
