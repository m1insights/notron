# JUNO

**A personal AI agent that lives inside the Notes app you already have.**

No new app. No new account. No new habit. You type into Apple Notes on your
phone — the same place you already dump your life — and Juno reads it, thinks
about it on NVIDIA Nemotron, and writes back into your notes. The answer is on
your phone about ten seconds later.

Built for the [Nebius × NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/) — Personal AI track.

---

## The idea

Every productivity tool asks you to move into it. Notion, Obsidian, Todoist —
they all start with "first, rebuild your life in here." Almost nobody finishes.

Apple Notes already won. It is on a billion devices, it already syncs, and it is
already where people keep the messy truth of their lives. So instead of building
another place to put things, Juno moves *into* the place you already are and
turns it into an agent workspace.

Your notes become three things at once: the interface, the memory, and the
instructions.

## How it works for you

Juno creates one folder, `🤖 JUNO`, with six notes:

| Note | Who writes it | What it is |
|---|---|---|
| `📌 About Me` | **You only** | Your standing instructions. Juno reads it before every action and can never write to it. |
| `📥 Ask Juno` | Both | Type a request; Juno answers underneath. |
| `☀️ Today` | Juno | Your to-do list, rebuilt each morning. |
| `🗓️ This Week` | Juno | Your weekly plan. |
| `🧠 Memory` | Juno | What she has learned about you. You can correct any of it. |
| `📊 Log` | Juno | Every single thing she did. Nothing happens off the record. |

Every other note you own is her knowledge base.

`📌 About Me` is the important one. It works like a config file written in plain
English — "never schedule me before 9am", "keep it short", "I'm a nurse on
nights" — and it outranks everything Juno would otherwise decide. You are not
prompting a chatbot. You are editing the constitution of your assistant.

## Safety: the Guard

An agent with write access to your entire personal history is only useful if it
cannot wreck it. Juno has one choke point — the Guard — and it is not a prompt,
it is code. Every proposed write passes through it:

1. **`📌 About Me` can never be written by Juno.** Your instructions are yours.
2. **Outside her own folder Juno may only append.** She cannot overwrite or
   delete a note you wrote. The Guard verifies the new body still contains the
   old one, byte for byte.
3. **Every write, allowed or blocked, is logged** to `📊 Log` before it lands.

No language model runs in the write path. The model proposes; the Guard judges;
a dumb executor applies. That separation is the whole security model.

## Architecture: graph engineering

Juno is not one agent spinning in a while-loop. It is a declared graph of
specialised nodes with explicit edges, so you can see exactly what runs, in what
order, and on which model.

```
  watcher ─► router ─► retriever ─► planner ─► writer ─► executor
     │          │          │           │          │          │
   no LLM     Nano      no LLM       Super      Super      no LLM
                                                             │
                                                          [Guard]
```

- **watcher** — loads `📌 About Me` and `🧠 Memory`. No model, runs every time.
- **router** — Nemotron **Nano 30B**. Classifies intent in ~200 tokens and
  decides whether the expensive nodes need to run at all. Most wake-ups stop here.
- **retriever** — searches your notes. No model.
- **planner** — Nemotron **Super 120B**. Only fires on planning intent.
- **writer** — Nemotron **Super 120B**. Composes the answer as Markdown.
- **executor** — no model. Runs the Guard, applies the write, records the log.

Nodes decline work they do not own, so a "note to self" costs one Nano call and
a plan costs one Nano plus one Super. Cost scales with what you actually asked for.

## Powered by

- **[Nebius Token Factory](https://tokenfactory.nebius.com)** — all inference.
- **NVIDIA Nemotron 3** (Nano 30B / Super 120B / Ultra 550B) — open-source models.
- **Apple Notes + iCloud** — the interface and the sync layer, free.

## Install

Requires macOS and Python 3.11+.

```bash
git clone <this repo> && cd juno
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

cp .env.example .env          # then paste your Nebius key into it
.venv/bin/python -m juno setup
```

The first run will ask macOS for permission to control Notes. Allow it.

Then open Notes → `🤖 JUNO` → `📌 About Me` and write a few lines about yourself.

## Use

```bash
.venv/bin/python -m juno ask "what did I decide about pricing?"
.venv/bin/python -m juno plan --week
.venv/bin/python -m juno graph      # print the node graph
.venv/bin/python -m juno models     # what your Nebius key can run
```

Add `--dry-run` to any command to walk the graph and write nothing.

## Notes app quirks we found

Tested on macOS 26.2, 2026-08-29. Apple documents almost none of this.

| Thing | Result |
|---|---|
| Read/write notes via AppleScript | Works |
| `<h1>` `<b>` `<i>` `<ul>` `<ol>` `<table>` | All render |
| `<a href>` | **href is stripped.** Emit bare URLs instead. |
| `class="checklist"` (tap-to-tick boxes) | **Stripped.** Juno uses ☐ / ✅ text and you tell her when something's done. |
| Note title | Taken from the first line of the body, always. |
| iCloud | Every Mac-side write appears on your iPhone automatically. |

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

The graph is tested against a stand-in brain, so the full suite runs with no API
key and no network.

## Licence

MIT.
