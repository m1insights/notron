# NOTRON — working notes for Claude

A personal AI agent that lives inside the user's Apple Notes. They type in Notes;
she reads, thinks on NVIDIA Nemotron via Nebius, and writes back into Notes.

Built for the Nebius × NVIDIA Global AI Hackathon (Personal AI track, due
**2026-10-30**). Two hackathon rules constrain every choice: **all inference must
run on Nebius Token Factory**, and **at least one NVIDIA open model must be used**.
Do not swap the model provider.

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
.venv/bin/python -m pytest tests -q      # 278 tests, no API key or network needed
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
(launchd) for anything that must outlive the command, and read `.notron/listen.log`.

**Do not run other Notes commands while the listener is working.** Notes serves one
script request at a time; a slow query from a second process wedges the app for
both. Requests serialise behind a file lock, so they queue rather than fail — but
a long query still delays the listener.

## Architecture

A declared graph of specialised nodes, not one agent in a loop:

```
watcher ─► router ─► retriever ─► researcher ─► agenda ─► planner ─► scheduler ─► doer ─► filer ─► writer ─► executor
  │          │          │            │           │          │           │          │        │       │          │
no LLM     Nano      no LLM       Tavily      no LLM       Super       Nano     no LLM   Super   Super   no LLM + Guard
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
| `guard.py` | The single choke point for every write |
| `executor.py` | Applies writes. No model runs here, ever. |
| `graph.py` / `nodes.py` / `state.py` | The graph and what flows along it |
| `privacy.py` | Keeps credentials and private notes out of answers |
| `index.py` / `retrieval.py` | Semantic search over the user's notes |
| `care.py` / `daily.py` | "Take Care of Notron" and the morning routine |
| `reflect.py` | The self-improvement loop — lessons from answers that missed |

## Invariants — do not break these

1. **`📌 About Me` is never written by Notron.** It is the user's instruction note.
2. **Outside `🤖 NOTRON` she may only add, never rewrite.** `notedoc.preserves` proves
   character by character that every original character survives, in order, and
   that new text landed between elements rather than inside a sentence.
3. **No model runs in the write path.** The model proposes, the Guard judges in
   plain code, a dumb executor applies. Nothing calls `notes.write_body` except
   `Executor`.
4. **Every write, allowed or blocked, is logged** to `📊 Log`.
5. **Credentials and private notes never reach the model.** See `privacy.py`.
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
9. **An ignored note is never read.** `library.user_notes()` is the only way the core
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
silently return the first one twice. Address folders by index.

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
- Do not replace the JXA scripts with a compiled Swift helper. EventKit access is
  granted per binary, and an unsigned binary's identity changes on every rebuild —
  so every edit to Notron would re-prompt, and a background listener can never answer
  a prompt. `osascript` inherits the terminal's stable identity.
- A dated reminder needs an explicit `EKAlarm`. A due date alone shows in the app
  but does not notify, and a reminder that does not buzz is a note with a circle.
- **A write is a full-body overwrite, and nothing locks the note while she
  thinks.** `append`/`insert` build `new_body` from a body read at the start of
  `_apply`; the model's thinking time sits in the gap after that read, unlocked,
  and the user can keep typing in the very note being answered. Writing the
  stale body back silently eats or mangles whatever they typed in that window —
  seen live as a sentence cut off mid-word and Notron answering the garble next
  pass. `_apply` now re-reads immediately before writing and skips the write if
  the note moved; the watcher retries next poll. `replace` is exempt — its
  `new_body` comes from the model's output, not from `old_body`.

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
(default) or **ignore** (never read — not for filing, not for questions, not even a
tag). Choices live in `.notron/library.json` keyed by note id; the Mac app's "Your
notes" window writes it (it lists notes via `notron library scan`), `notron library`
edits it from the terminal. A "start from [year]" cutoff applies only to notes the
user never looked at (`decided`); a row they flipped wins. With no homes chosen the
Filer keeps every readable note as a candidate; a note Notron creates after a `yes`
joins the homes only when homes exist. The pre-fill (`library.suggest`) is plain
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

## The self-improvement loop (`reflect.py`)

Runs inside `notron morning` and on demand via `notron reflect`. Plain code finds
the evidence (corrections and re-asked questions in the Ask note — no model, so a
quiet day costs zero calls); Super proposes ≤3 lessons, each forced to quote the
transcript verbatim (string-checked in code); a **separate** Nano call verifies
them against 📌 About Me and the existing lessons; the Guard writes the survivors
to 📖 Lessons (capped at 12). Every prompt then carries the lessons *below* the
standing instructions — About Me always wins, and the user can delete any lesson
by editing the note. Run record: `.notron/reflect.json`, append-only.

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
- Tests run with no API key and no network. Fake brains, monkeypatched Notes.
- User-facing strings are plain and warm. She is an assistant, not a pet.
