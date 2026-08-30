"""The NOTRON folder in Apple Notes — the agent's whole operating surface."""

from __future__ import annotations

AGENT = "NOTRON"
FOLDER = f"🤖 {AGENT}"

ABOUT = "📌 About Me"
ASK = "📥 Ask Notron"
TODAY = "☀️ Today"
WEEK = "🗓️ This Week"
MEMORY = "🧠 Memory"
CARE = "🌱 Take Care of Notron"
LOG = "📊 Log"

#: Notes the agent must never write to. The user owns these outright.
READ_ONLY = frozenset({ABOUT})

#: Notes the agent fully owns and may rewrite.
AGENT_OWNED = frozenset({TODAY, WEEK, MEMORY, CARE, LOG})

#: Notes both sides write: the user asks, the agent appends its answer.
SHARED = frozenset({ASK})

SYSTEM_NOTES = (ABOUT, ASK, TODAY, WEEK, MEMORY, CARE, LOG)

SEEDS: dict[str, str] = {
    ABOUT: """This note is **yours**. Notron reads it before every single thing she does, and she can never write to it. Edit it whenever you like.

## Who I am
Name:
What I do:
Where I live:

## How to talk to me
- Keep it short.
- Tell me the answer first.

## My rules
- Never schedule me before 9am.
- Never move anything already in my calendar without asking.

## What matters right now
-
""",
    ASK: """Type anything below this line and Notron will answer underneath it.

———
""",
    TODAY: """Notron rebuilds this every morning. Tell her "done with X" and she ticks it off.

- [ ] Nothing yet — Notron hasn't run.
""",
    WEEK: """Notron rebuilds this every Sunday night.

Nothing planned yet.
""",
    MEMORY: """What Notron has learned about you. She writes here; you can correct anything.

Nothing learned yet.
""",
    CARE: """Notron rewrites this every morning. It's what she needs from you to keep working well.

She hasn't checked herself over yet.
""",
    LOG: """Everything Notron did, newest first. Nothing happens that isn't written here.

———
""",
}


def bootstrap() -> dict[str, str]:
    """Create the NOTRON folder and any missing system notes. Never overwrites."""
    from . import markup, notes

    notes.ensure_folder(FOLDER)
    existing = {n.title: n for n in notes.list_notes(FOLDER)}
    result: dict[str, str] = {}
    for title in SYSTEM_NOTES:
        if title in existing:
            result[title] = "kept"
            continue
        notes.create_note(FOLDER, markup.render(title, SEEDS[title]))
        result[title] = "created"
    return result
