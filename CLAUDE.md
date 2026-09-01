# NOTRON — working notes for Claude

A personal AI agent that lives inside the user's Apple Notes. They type in Notes;
she reads, thinks on NVIDIA Nemotron via Nebius, and writes back into Notes.

Built for the Nebius × NVIDIA Global AI Hackathon (Personal AI track, due
**2026-10-30**). Two hackathon rules constrain every choice: **all inference must
run on Nebius Token Factory**, and **at least one NVIDIA open model must be used**.
Do not swap the model provider.

## Commands

```bash
.venv/bin/python -m pytest tests -q      # 164 tests, no API key or network needed
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
watcher ─► router ─► retriever ─► researcher ─► agenda ─► planner ─► scheduler ─► doer ─► writer ─► executor
  │          │          │            │           │          │           │          │       │          │
no LLM     Nano      no LLM       Tavily      no LLM       Super       Nano     no LLM   Super   no LLM + Guard
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
| `mentions.py` | Sweeps every note for `#notron` |
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
