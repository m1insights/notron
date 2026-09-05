# NOTRON

**A personal AI agent that lives inside the Notes app you already have.**

No new app. No new account. No new habit. You type into Apple Notes on your
phone — the same place you already dump your life — and Notron reads it, thinks
about it on NVIDIA Nemotron, and writes back into your notes. The answer is on
your phone about ten seconds later.

Built for the [Nebius × NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/) — Personal AI track.

**Pre-release security status:** protected startup is paused until the signed
Keychain integration is verified in P06. Examples below describe product behavior,
not an instruction to bypass that gate. No production-readiness or sandbox claim.
See [SECURITY.md](SECURITY.md) for implemented boundaries, limitations and the
owner's unresolved private-reporting decision.

---

## The idea

Every productivity tool asks you to move into it. Notion, Obsidian, Todoist —
they all start with "first, rebuild your life in here." Almost nobody finishes.

Apple Notes already won. It is on a billion devices, it already syncs, and it is
already where people keep the messy truth of their lives. So instead of building
another place to put things, Notron moves *into* the place you already are and
turns it into an agent workspace.

Your notes become three things at once: the interface, the memory, and the
instructions.

## How you talk to her

Two ways, both of them just typing in Notes.

**Ask her directly.** Write in the `📥 Ask Notron` note — anywhere in it, top or
bottom. She waits until you've stopped typing, answers directly underneath what
you wrote, and draws a line so the next thing you type is the next question.

**Or tag her where you're already thinking.** Write `#notron` in any note you own —
the book idea, the meeting note, the half-finished plan — and ask about *that*
thing, in *that* place:

```
Book idea — Lighthouse

A story about a lighthouse keeper who starts receiving letters
from someone who died forty years ago.

Act two is where it falls apart — she just reads letters for sixty pages.

#notron what would give act two some pressure?
```

She answers underneath that line, using the note itself as context. In a note that
isn't hers, a tagged request permits its own reply only when the note is readable.
Automatic filing requires Home permission. Rewrite/undo are separate explicit
paths; Apple Notes full-body writes still have edit-loss risks (see SECURITY.md).

Because it's Apple Notes, this works from your iPhone: type on the sofa, iCloud
carries it to the Mac, she thinks on Nebius, and the answer is on your phone about
ten seconds later. And if you ask something at 2am while the Mac is shut, she picks
it up the moment it wakes.

```bash
notron listen --install     # she listens from now on, through reboots
notron listen --off         # she stops
```

**Or just dump.** `🧠 Brain Dump` is a note in her folder that takes anything, one
thought per line — "took vitamin D today", "act two needs a storm", "that serum
from the pop-up". No deciding where it goes. Once you've left the dump alone for a
quarter of an hour, she files each line into the right one of your own notes and
ticks it where it sits:

```
✓ took vitamin D today → Supplements
✓ act two needs a storm → Book idea — Lighthouse
that serum from the pop-up
```

Nothing is ever deleted — the ticked line stays until you clear the note, and the
receipt says where the copy went. When no note fits, she asks rather than guesses:
*"Want a new note called Skincare Brand? Type yes under this."* Say yes and she
makes it and files the lines; say no and she leaves them alone. `@notron file this:
…` on a line in any other note does the same for that line, in place. And `notron
file` runs it now.

A sentence with lines under it is one thought — "the stack today:" and the list
beneath it arrive together. In a note that logs things over time she writes the
way you would in a journal: today's date once, in bold, then what you said:

    Wed 2 Sep 2026
    The stack did really well today once the trazodone wore off.
    • Concerta 36mg
    • Avmacol
    • PQQ

A note that collects things — recipes, ideas, names — gets plain bullets and no
dates. She decides which a note is the first time she files into it, and sticks
to it. A note she makes for you starts that way from its first line.

**Tell her where things go — once.** Years of notes means three notes called "Supps"
and a password note in the main list. In the app, "Your notes" shows every note
with a three-way switch — *Home* (she may file into it), *Read only* (the
default), *Ignore* (excluded from AI reads, even with a tag; explicit local preview
remains available) — pre-filled with her guesses and a "start from [year]" for the old stuff. Change it any time.

There is a terminal route too — `notron ask "..."` — but it exists for testing. The
note is the product.

## She can set a reminder

Type this into `📥 Ask Notron`:

```
remind me to call the pharmacy thursday at 10am
```

About fifteen seconds later, underneath it:

```
Notron: Reminder set: Call the pharmacy — Thursday 3 September at 10:00
```

And it is a real reminder — in the Reminders app, buzzing on your phone at
10am Thursday, exactly the way one you set by hand would. Same thing for your
calendar: "put a dentist appointment in my calendar for 2pm Friday" creates a
real event.

She can also tell you no. `📌 About Me` says "never schedule me before 9am"; ask
her for something at 7am and she answers `I didn't set that. That's 07:00, and
you asked me never to schedule anything before 09:00` and creates nothing. That
rule — like "never move anything already in my calendar" and "never delete a
reminder" — is enforced in plain code by the Guard, not by asking the model
nicely. See [Safety: the Guard](#safety-the-guard).

## How it works for you

Notron creates one folder, `🤖 NOTRON`, with six notes:

| Note | Who writes it | What it is |
|---|---|---|
| `📌 About Me` | **You only** | Your standing instructions. Notron reads it before every action and can never write to it. |
| `📥 Ask Notron` | Both | Type a request; Notron answers underneath. |
| `☀️ Today` | Notron | Your to-do list, rebuilt each morning. |
| `🗓️ This Week` | Notron | Your weekly plan. |
| `🧠 Memory` | Notron | What she has learned about you. You can correct any of it. |
| `🌱 Take Care of Notron` | Notron | What *she* needs from *you* to keep working well. |
| `📊 Log` | Notron | Best-effort local write receipts when its registered note is accessible. |

Only policy-approved notes form her knowledge base. Zero homes permits no automatic
filing; unknown notes are denied unless the validated policy explicitly permits reads.

`📌 About Me` is the important one. It works like a config file written in plain
English — "never schedule me before 9am", "keep it short", "I'm a nurse on
nights" — and it supplies standing context. Only rules implemented in code are enforced
independently of the model; natural-language instructions can be misunderstood. You are not
prompting a chatbot. You are editing the constitution of your assistant.

## Take Care of Notron

An assistant that reads your whole life has upkeep, and normally that upkeep is
invisible until something breaks: the instruction note quietly bloats until it
crowds out your actual question, hundreds of new notes never get learned, the
bill drifts. Every morning Notron measures her own state and writes you a note
about it, in her own voice:

> I'm in good shape, but I'm carrying a lot.
>
> **What I need from you**
> - [ ] `📌 About Me` is 6,100 characters and I read all of it before every single
>       thing I do. Trim it to the rules that still matter.
> - [ ] 42 notes have appeared since I last studied. Run `notron index`.
>
> **How I'm doing**
> - I've read all 358 of your notes.
> - This week: 61 thoughts, 240,000 tokens on Nebius.

It reframes context hygiene — a thing normal people will never do — as looking
after something. Every number in it is measured; the model only writes the words.

```bash
notron care
```

## Safety: the Guard

Notron uses local policy, outbound preparation, the Guard and a fixed executor
as separate checks. Current protections are exercised with synthetic adapters:

1. **About Me is protected by its registered note ID.** Names alone grant nothing.
2. **Filing requires Home permission.** A tagged request in a readable note permits
   its own reply. Append/insert preservation checks do not prove atomicity or
   attachment preservation; explicitly enabled rewrite and undo are separate paths.
3. **Exclusion precedes redaction.** Ignored notes and sensitive-title matches are
   excluded from AI inputs. Supported secret patterns are redacted before inference,
   embedding and search, and checked on ordinary writes. Detection is imperfect;
   restoring an existing undo snapshot is a separate, existing-content path.
4. **Write logging is best effort.** Receipts go to the registered, readable Log
   note; successful writes are logged afterward. There is no durable audit ledger yet.
5. **Calendar events are create-only; reminders may be created or completed.**
   Model output cannot introduce new operation types or directly grant permissions.
6. **Model output is data.** Fixed scripts receive values through arguments;
   there is no model-to-shell/script execution path.

Sensitive caches use authenticated encryption with no plaintext fallback. All
provider requests use constrained transports; citations are checked locally without
fetching their URLs. These controls do not protect a compromised local account,
guarantee provider retention, or eliminate Apple Notes write races. See
[SECURITY.md](SECURITY.md) for the full limits and pending release gates.

## Architecture: graph engineering

Notron is not one agent spinning in a while-loop. It is a declared graph of
specialised nodes with explicit edges, so you can see exactly what runs, in what
order, and on which model.

```
  watcher ─► router ─► retriever ─► researcher ─► agenda ─► planner ─► scheduler ─► doer ─► writer ─► executor
     │          │          │            │           │          │           │          │       │          │
   no LLM     Nano      no LLM       Tavily      no LLM      Super        Nano     no LLM   Super  no LLM + Guard
```

- **watcher** — loads `📌 About Me` and `🧠 Memory`. No model, runs every time.
- **router** — Nemotron **Nano 30B**. Classifies intent in ~200 tokens and
  decides whether the expensive nodes need to run at all. Most wake-ups stop here.
- **retriever** — semantic search over approved notes, with redaction before embeddings
  and encrypted caching. Ignored notes are excluded; secret detection is imperfect.
- **researcher** — Tavily web search, only when the answer cannot be in her head
  or in your notes. Fails soft: no key or a timeout costs the web, not the reply.
- **agenda** — reads today's real calendar and open reminders via EventKit. No
  model, and only when the request is about the day.
- **planner** — Nemotron **Super 120B**. Only fires on planning intent, plans
  around what `agenda` actually found.
- **scheduler** — Nemotron **Nano 30B**. Turns "remind me to..." into a
  structured reminder or calendar action.
- **doer** — no model. Applies the action through the Guard. Scheduling replies
  are composed from executor outcomes without a further model call.
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
git clone <this repo> && cd notron
uv sync --locked --extra dev
.venv/bin/python -m pytest tests -q
```

This prepares the locked development/test dependencies. The historical
`requirements.txt` is incomplete; use `pyproject.toml` and `uv.lock`. Do not copy
credentials into `.env`: environment credentials are unsupported. Signed Keychain
setup, native permission validation and enabling real processing remain P06 work.

## Use

```bash
.venv/bin/python -m notron index               # teach her your notes (once)
.venv/bin/python -m notron ask "what did I decide about pricing?"
.venv/bin/python -m notron plan --week
.venv/bin/python -m notron file                # sort the Brain Dump into your notes now
.venv/bin/python -m notron library             # which notes she may file into, which she never reads
.venv/bin/python -m notron care                # what she needs from you today
.venv/bin/python -m notron morning             # her full daily routine
.venv/bin/python -m notron schedule --hour 6   # let macOS run it every morning
.venv/bin/python -m notron graph               # print the node graph
.venv/bin/python -m notron models              # what your Nebius key can run
.venv/bin/python -m notron permissions         # can she reach Notes, Reminders, Calendar?
.venv/bin/python -m notron agenda              # today, this week, and what's outstanding
```

Supported processing commands expose `--dry-run` to skip application writes; it
is not a privacy or network bypass and still requires secure startup.

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
| `class="checklist"` (tap-to-tick boxes) | **Stripped.** Notron uses ☐ / ✅ text and you tell her when something's done — or now, asks Reminders to make a real one. |
| Note title | Taken from the first line of the body, always. |
| iCloud | Every Mac-side write appears on your iPhone automatically. |
| Reminders/Calendar via AppleScript | **Unusable at real-library scale** — 65.7s for 23 open reminders, 26s for a 7-day window. Read them through EventKit instead (0.093s / 0.025s) — see `notron/eventkit.py`. |

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

The graph is tested against a stand-in brain, so the full suite runs with no API
key and no network.

## Licence

MIT.
