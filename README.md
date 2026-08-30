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

## How you talk to her

Two ways, both of them just typing in Notes.

**Ask her directly.** Write in the `📥 Ask Juno` note — anywhere in it, top or
bottom. She waits until you've stopped typing, answers directly underneath what
you wrote, and draws a line so the next thing you type is the next question.

**Or tag her where you're already thinking.** Write `#juno` in any note you own —
the book idea, the meeting note, the half-finished plan — and ask about *that*
thing, in *that* place:

```
Book idea — Lighthouse

A story about a lighthouse keeper who starts receiving letters
from someone who died forty years ago.

Act two is where it falls apart — she just reads letters for sixty pages.

#juno what would give act two some pressure?
```

She answers underneath that line, using the note itself as context. In a note that
isn't hers she never speaks unless spoken to, and never changes a word you wrote —
the Guard proves that character by character before every write.

Because it's Apple Notes, this works from your iPhone: type on the sofa, iCloud
carries it to the Mac, she thinks on Nebius, and the answer is on your phone about
ten seconds later. And if you ask something at 2am while the Mac is shut, she picks
it up the moment it wakes.

```bash
juno listen --install     # she listens from now on, through reboots
juno listen --off         # she stops
```

There is a terminal route too — `juno ask "..."` — but it exists for testing. The
note is the product.

## How it works for you

Juno creates one folder, `🤖 JUNO`, with six notes:

| Note | Who writes it | What it is |
|---|---|---|
| `📌 About Me` | **You only** | Your standing instructions. Juno reads it before every action and can never write to it. |
| `📥 Ask Juno` | Both | Type a request; Juno answers underneath. |
| `☀️ Today` | Juno | Your to-do list, rebuilt each morning. |
| `🗓️ This Week` | Juno | Your weekly plan. |
| `🧠 Memory` | Juno | What she has learned about you. You can correct any of it. |
| `🌱 Take Care of Juno` | Juno | What *she* needs from *you* to keep working well. |
| `📊 Log` | Juno | Every single thing she did. Nothing happens off the record. |

Every other note you own is her knowledge base.

`📌 About Me` is the important one. It works like a config file written in plain
English — "never schedule me before 9am", "keep it short", "I'm a nurse on
nights" — and it outranks everything Juno would otherwise decide. You are not
prompting a chatbot. You are editing the constitution of your assistant.

## Take Care of Juno

An assistant that reads your whole life has upkeep, and normally that upkeep is
invisible until something breaks: the instruction note quietly bloats until it
crowds out your actual question, hundreds of new notes never get learned, the
bill drifts. Every morning Juno measures her own state and writes you a note
about it, in her own voice:

> I'm in good shape, but I'm carrying a lot.
>
> **What I need from you**
> - [ ] `📌 About Me` is 6,100 characters and I read all of it before every single
>       thing I do. Trim it to the rules that still matter.
> - [ ] 42 notes have appeared since I last studied. Run `juno index`.
>
> **How I'm doing**
> - I've read all 358 of your notes.
> - This week: 61 thoughts, 240,000 tokens on Nebius.

It reframes context hygiene — a thing normal people will never do — as looking
after something. Every number in it is measured; the model only writes the words.

```bash
juno care
```

## Safety: the Guard

An agent with write access to your entire personal history is only useful if it
cannot wreck it. Juno has one choke point — the Guard — and it is not a prompt,
it is code. Every proposed write passes through it:

1. **`📌 About Me` can never be written by Juno.** Your instructions are yours.
2. **Outside her own folder Juno may only append.** She cannot overwrite or
   delete a note you wrote. The Guard verifies the new body still contains the
   old one, byte for byte.
3. **No write may carry a credential.** On Juno's very first live morning run she
   pulled two passwords out of the user's own notes and wrote them into the day's
   plan — which then synced to their phone. Now credential notes are excluded from
   retrieval entirely, anything that still looks like a secret is masked before the
   model sees it, and the Guard blocks any write containing one on the way out.
   `tests/test_privacy.py` pins that exact leak.
4. **Every write, allowed or blocked, is logged** to `📊 Log` before it lands.

No language model runs in the write path. The model proposes; the Guard judges;
a dumb executor applies. That separation is the whole security model.

## Architecture: graph engineering

Juno is not one agent spinning in a while-loop. It is a declared graph of
specialised nodes with explicit edges, so you can see exactly what runs, in what
order, and on which model.

```
  watcher ─► router ─► retriever ─► researcher ─► planner ─► writer ─► executor
     │          │          │            │            │          │          │
   no LLM     Nano      no LLM       Tavily       Super      Super      no LLM
                                                                           │
                                                                        [Guard]
```

- **watcher** — loads `📌 About Me` and `🧠 Memory`. No model, runs every time.
- **router** — Nemotron **Nano 30B**. Classifies intent in ~200 tokens and
  decides whether the expensive nodes need to run at all. Most wake-ups stop here.
- **retriever** — semantic search over every note, embedded once and cached.
  Credential notes and private ones never reach the model. No model here.
- **researcher** — Tavily web search, only when the answer cannot be in her head
  or in your notes. Fails soft: no key or a timeout costs the web, not the reply.
- **planner** — Nemotron **Super 120B**. Only fires on planning intent.
- **writer** — Nemotron **Super 120B**. Composes the answer as Markdown.
- **executor** — no model. Runs the Guard, applies the write, records the log.

Nodes decline work they do not own, so a "note to self" costs one Nano call and
a plan costs one Nano plus one Super. Cost scales with what you actually asked for.

## Powered by

- **[Nebius Token Factory](https://tokenfactory.nebius.com)** — all inference.
- **[Tavily](https://tavily.com)** — web search, when the answer is not in her
  head or your notes. Optional; leave the key out and she works without it.
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
.venv/bin/python -m juno index               # teach her your notes (once)
.venv/bin/python -m juno ask "what did I decide about pricing?"
.venv/bin/python -m juno plan --week
.venv/bin/python -m juno care                # what she needs from you today
.venv/bin/python -m juno morning             # her full daily routine
.venv/bin/python -m juno schedule --hour 6   # let macOS run it every morning
.venv/bin/python -m juno graph               # print the node graph
.venv/bin/python -m juno models              # what your Nebius key can run
```

Add `--dry-run` to any command to walk the graph and write nothing.

## Two things Nemotron does that will catch you out

**Reasoning is billed against `max_tokens`, and it is not the answer.** Ask Nano for
a 500-token reply and it can spend all 500 thinking, then return an empty string
with `finish_reason: "stop"` — no error, no warning. `Brain.ask` adds headroom for
the thinking and retries once with double the budget if the content still comes
back empty. None of `reasoning_effort="none"`, `/no_think`, or
`chat_template_kwargs={"thinking": false}` turned reasoning off on this endpoint.

**JSON replies truncate mid-string.** The router's classification failed the first
time it met a long question, because the budget ran out halfway through the last
field. `Brain.ask_json` now tries the raw text, then the fenced block, then the
outermost braces, then reconstructs a valid object from the complete pairs — and
the router falls back to a safe default rather than stopping the graph.

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
