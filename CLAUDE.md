# JUNO — working notes for Claude

A personal AI agent that lives inside the user's Apple Notes. They type in Notes;
she reads, thinks on NVIDIA Nemotron via Nebius, and writes back into Notes.

Built for the Nebius × NVIDIA Global AI Hackathon (Personal AI track, due
**2026-10-30**). Two hackathon rules constrain every choice: **all inference must
run on Nebius Token Factory**, and **at least one NVIDIA open model must be used**.
Do not swap the model provider.

## Commands

```bash
.venv/bin/python -m pytest tests -q      # 77 tests, no API key or network needed
.venv/bin/python -m juno setup           # create the 🤖 JUNO folder in Notes
.venv/bin/python -m juno index           # embed all the user's notes (~2 min)
.venv/bin/python -m juno ask "..."       # one-shot, for testing
.venv/bin/python -m juno listen          # foreground listener
.venv/bin/python -m juno listen --install  # background listener via launchd
.venv/bin/python -m juno morning         # the daily routine
.venv/bin/python -m juno graph           # print the node graph
```

`--dry-run` on `ask`, `plan`, `care` and `morning` walks the graph and writes nothing.

## Running the listener while developing

**A listener started from a tool-run shell dies the moment that shell returns.**
Hours were lost to this: the code looked broken because every test listener was
being killed after printing its startup lines. Use `juno listen --install`
(launchd) for anything that must outlive the command, and read `.juno/listen.log`.

**Do not run other Notes commands while the listener is working.** Notes serves one
script request at a time; a slow query from a second process wedges the app for
both. Requests serialise behind a file lock, so they queue rather than fail — but
a long query still delays the listener.

## Architecture

A declared graph of specialised nodes, not one agent in a loop:

```
watcher ─► router ─► retriever ─► researcher ─► planner ─► writer ─► executor
  │          │          │            │            │          │          │
no LLM     Nano      no LLM       Tavily       Super      Super    no LLM + Guard
```

Nodes decline work they do not own. Model tiers live in `brain.DEFAULT_MODELS`
(`fast` = Nemotron Nano 30B, `smart` = Super 120B, `deep` = Ultra 550B); embeddings
are Qwen3-Embedding-8B because Nebius serves no NVIDIA embedding model.

| Module | Responsibility |
|---|---|
| `applescript.py` | The only place that shells out to `osascript`. Holds the lock. |
| `notes.py` | Apple Notes read/write. Bulk queries only — see Performance. |
| `markup.py` | Markdown ⇄ the HTML subset Notes actually renders |
| `notedoc.py` | A note as addressable blocks; provably lossless inserts |
| `conversation.py` | Reads a note as turns; finds what she has not answered |
| `mentions.py` | Sweeps every note for `#juno` |
| `guard.py` | The single choke point for every write |
| `executor.py` | Applies writes. No model runs here, ever. |
| `graph.py` / `nodes.py` / `state.py` | The graph and what flows along it |
| `privacy.py` | Keeps credentials and private notes out of answers |
| `index.py` / `retrieval.py` | Semantic search over the user's notes |
| `care.py` / `daily.py` | "Take Care of Juno" and the morning routine |

## Invariants — do not break these

1. **`📌 About Me` is never written by Juno.** It is the user's instruction note.
2. **Outside `🤖 JUNO` she may only add, never rewrite.** `notedoc.preserves` proves
   character by character that every original character survives, in order, and
   that new text landed between elements rather than inside a sentence.
3. **No model runs in the write path.** The model proposes, the Guard judges in
   plain code, a dumb executor applies. Nothing calls `notes.write_body` except
   `Executor`.
4. **Every write, allowed or blocked, is logged** to `📊 Log`.
5. **Credentials and private notes never reach the model.** See `privacy.py`.

## Performance — measured on 358 real notes

These are not micro-optimisations; they decide whether a background agent is
possible at all.

| Doing it the obvious way | Doing it right |
|---|---|
| `repeat with n in notes of f` — **106s** | `name of every note of f` — **0.2s** |
| Finding a folder by walking `every folder` — **20s** | `folder 3` by index — **0.2s** |
| First call after Notes goes idle — **~40s** | Absorb once at startup (`notes.warm_up`) |

Notes also allows two folders with the same name, so looking one up by name can
silently return the first one twice. Address folders by index.

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

## Conventions

- Plain functions and dataclasses. No agent framework — the graph is the point.
- Comments explain **why**, especially where a fix encodes a bug that actually
  happened. Several tests are named after real failures; keep them that way.
- Tests run with no API key and no network. Fake brains, monkeypatched Notes.
- User-facing strings are plain and warm. She is an assistant, not a pet.
