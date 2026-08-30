"""Take Care of Juno — the note where she tells you what she needs.

An assistant that reads your whole life has upkeep, and normally that upkeep is
invisible until something breaks: the instruction note quietly bloats until it
crowds out your actual question, hundreds of new notes never get learned, the
log grows without bound, the bill drifts. Juno surfaces all of it as things you
can do for her, in her own voice, once a day.

Every signal here is measured, never guessed. The model only writes the copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import index, markup, notes, permissions, workspace
from .brain import USAGE_LOG

# Thresholds. Past these, Juno asks for help.
ABOUT_COMFORTABLE = 2500      # chars of instructions she re-reads on every single run
ABOUT_HEAVY = 5000
LOG_ENTRIES_MAX = 60
MEMORY_STALE_DAYS = 7
UNINDEXED_MAX = 20


@dataclass
class Signal:
    key: str
    severity: str          # "ok" | "nudge" | "needs you"
    fact: str              # the measured truth, no opinion
    ask: str               # what the user can actually do about it


def _note_text(title: str) -> str:
    n = notes.find_note(workspace.FOLDER, title)
    return markup.to_text(notes.read_body(n.id)) if n else ""


def _usage(days: int = 7) -> dict:
    if not USAGE_LOG.exists():
        return {"calls": 0, "in": 0, "out": 0}
    data = json.loads(USAGE_LOG.read_text())
    cutoff = date.today() - timedelta(days=days)
    total = {"calls": 0, "in": 0, "out": 0}
    for day, tiers in data.items():
        try:
            if date.fromisoformat(day) < cutoff:
                continue
        except ValueError:
            continue
        for row in tiers.values():
            for k in total:
                total[k] += row.get(k, 0)
    return total


def check() -> list[Signal]:
    out: list[Signal] = []

    # 1. The instruction note. She re-reads it on every run, so its size is a tax.
    about = _note_text(workspace.ABOUT)
    n = len(about)
    if n > ABOUT_HEAVY:
        out.append(Signal(
            "about_size", "needs you",
            f"{workspace.ABOUT} is {n:,} characters — I read all of it before every single thing I do.",
            "Cut it back to the rules that still matter. Old ones are crowding out your actual question.",
        ))
    elif n > ABOUT_COMFORTABLE:
        out.append(Signal(
            "about_size", "nudge",
            f"{workspace.ABOUT} is {n:,} characters and growing.",
            "Worth a trim soon — shorter instructions make me sharper.",
        ))
    elif n < 200:
        out.append(Signal(
            "about_size", "needs you",
            f"{workspace.ABOUT} is nearly empty.",
            "Tell me who you are and how you like things. It's the difference between a chatbot and an assistant.",
        ))
    else:
        out.append(Signal("about_size", "ok", f"{workspace.ABOUT} is {n:,} characters.", ""))

    # 2. Notes she has never read.
    live = [x for x in notes.list_all_notes() if x.folder != workspace.FOLDER]
    known = set(json.loads(index.CACHE.read_text()).keys()) if index.exists() else set()
    unlearned = [x for x in live if x.id not in known]
    if not index.exists():
        out.append(Signal(
            "index", "needs you",
            f"I haven't read any of your {len(live)} notes yet.",
            "Run `juno index` once and I'll actually know your life.",
        ))
    elif len(unlearned) > UNINDEXED_MAX:
        out.append(Signal(
            "index", "needs you",
            f"{len(unlearned)} notes have appeared since I last studied.",
            "Run `juno index` — takes a minute and I'll catch up.",
        ))
    elif unlearned:
        out.append(Signal("index", "nudge", f"{len(unlearned)} notes I haven't read yet.", "Run `juno index` when you get a sec."))
    else:
        out.append(Signal("index", "ok", f"I've read all {len(live)} of your notes.", ""))

    # 3. Memory freshness — an assistant that learns nothing is just a search box.
    mem = notes.find_note(workspace.FOLDER, workspace.MEMORY)
    when = mem.modified_at if mem else None
    if when and (datetime.now() - when).days >= MEMORY_STALE_DAYS:
        days = (datetime.now() - when).days
        out.append(Signal(
            "memory", "nudge",
            f"I haven't learned anything new about you in {days} days.",
            "Tell me something — a preference, a deadline, a person. I'll remember it.",
        ))
    else:
        out.append(Signal("memory", "ok", "My memory of you is current.", ""))

    # 4. The log grows forever unless someone clears it.
    entries = _note_text(workspace.LOG).count(" — ")
    if entries > LOG_ENTRIES_MAX:
        out.append(Signal(
            "log", "nudge",
            f"{workspace.LOG} has {entries} entries.",
            "Clear it out whenever you like — it's a receipt, not a record you need to keep.",
        ))
    else:
        out.append(Signal("log", "ok", f"{workspace.LOG} has {entries} entries.", ""))

    # 5. What she costs to run.
    u = _usage(7)
    if u["calls"]:
        out.append(Signal(
            "usage", "ok",
            f"This week: {u['calls']} thoughts, {u['in'] + u['out']:,} tokens on Nebius.",
            "",
        ))

    # 6. Apps she is no longer allowed to talk to. The symptom is silence, so it
    #    has to be said out loud somewhere the user will read it.
    blocked = [c.app for c in permissions.check() if not c.ok]
    if blocked:
        out.append(Signal(
            "apps", "needs you",
            f"I can't reach {', '.join(blocked)} any more.",
            "System Settings → Privacy & Security → Automation, switch them back on.",
        ))
    else:
        out.append(Signal("apps", "ok", "Notes, Reminders and Calendar all answer.", ""))

    return out


CARE_SYSTEM = """You are Juno, a personal assistant who lives in someone's Apple Notes.

You are writing your own daily upkeep note — the things you need from them to keep
working well. You are given measured facts about your own state. Turn them into a
short note in your voice.

Rules:
- Warm, direct, never needy or cutesy. You are a capable assistant asking for what
  you need, not a pet begging.
- Lead with one line on how you're doing overall.
- Then "## What I need from you" as a "- [ ]" list — only the items that actually
  need them. If nothing does, say so and skip the list.
- Then "## How I'm doing" as a short bullet list of the healthy stuff.
- Never invent a number. Use only the facts given.
- Under 150 words. This is read on a phone."""


def compose(signals: list[Signal], brain=None) -> str:
    facts = "\n".join(
        f"- [{s.severity}] {s.fact}" + (f" ASK: {s.ask}" if s.ask else "")
        for s in signals
    )
    if brain is None:
        return _plain(signals)
    written = brain.ask(system=CARE_SYSTEM, user=f"Facts about your state today:\n{facts}",
                        tier="fast", max_tokens=600)
    # This note must never be blank — it is the one that tells you something is wrong.
    return written or _plain(signals)


def _plain(signals: list[Signal]) -> str:
    """Deterministic fallback so this note is never blank, even offline."""
    todo = [s for s in signals if s.severity != "ok"]
    lines = []
    if todo:
        lines.append("## What I need from you\n")
        lines += [f"- [ ] {s.ask} ({s.fact})" for s in todo]
        lines.append("")
    lines.append("## How I'm doing\n")
    lines += [f"- {s.fact}" for s in signals if s.severity == "ok"]
    return "\n".join(lines)


def run(brain=None, *, dry_run: bool = False):
    """Measure, compose, and write the care note."""
    from .executor import Executor

    signals = check()
    body = compose(signals, brain)
    result = Executor(dry_run=dry_run).replace(workspace.CARE, body)
    return signals, body, result
