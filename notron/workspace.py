"""The NOTRON folder in Apple Notes — the agent's whole operating surface."""

from __future__ import annotations

AGENT = "NOTRON"
FOLDER = f"🤖 {AGENT}"

ABOUT = "📌 About Me"
ASK = "📥 Ask Notron"
TODAY = "☀️ Today"
WEEK = "🗓️ This Week"
MEMORY = "🧠 Memory"
LESSONS = "📖 Lessons"
CARE = "🌱 Take Care of Notron"
LOG = "📊 Log"
DUMP = "🧠 Brain Dump"

#: Notes the agent must never write to. The user owns these outright.
READ_ONLY = frozenset({ABOUT})

#: Notes the agent fully owns and may rewrite.
AGENT_OWNED = frozenset({TODAY, WEEK, MEMORY, LESSONS, CARE, LOG})

#: Notes both sides write: the user asks, the agent appends its answer — or,
#: in the dump, the user throws lines in and the agent ticks them as filed.
SHARED = frozenset({ASK, DUMP})

SYSTEM_NOTES = (ABOUT, ASK, DUMP, TODAY, WEEK, MEMORY, LESSONS, CARE, LOG)

#: The three she is used through, every day — the ones worth pinning first.
PIN_SUGGESTED = (ABOUT, ASK, DUMP)

#: Every system note, suggested first, then in the order the user meets them.
PIN_ORDER = (ABOUT, ASK, DUMP, TODAY, WEEK, CARE, MEMORY, LESSONS, LOG)

#: One plain line per note, for the screen that asks the user to pin it.
PIN_WHY: dict[str, str] = {
    ABOUT: "Your instruction note. She reads it before everything — you'll edit it often.",
    ASK: "Where you ask her things. The note you'll open most.",
    DUMP: "Throw a thought in and she files it. Only works if it's one click away.",
    TODAY: "What she's lined up for today. Rebuilt every morning.",
    WEEK: "The week ahead. Rebuilt Sunday night.",
    CARE: "What she needs from you to keep working well.",
    MEMORY: "What she's learned about you.",
    LESSONS: "Rules she's taught herself. Delete any you disagree with.",
    LOG: "Everything she's done, newest first.",
}

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
    ASK: """Type anything below this line and Notron will answer underneath it. Follow up with “make that simpler” or “expand on that.” Put New topic on a line of its own to start fresh. She uses up to three earlier exchanges; deleting an exchange removes it from future conversation context.

———
""",
    DUMP: """Throw anything in here, one thought per line — a list under a thought stays with it. When you've stopped for a while, Notron files each thought into the right note and ticks it — nothing is ever deleted.

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
    LESSONS: """Rules Notron has taught herself from answers that missed. Delete any you disagree with — 📌 About Me always outranks these.

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
    from . import markup, notes, library, policy

    lib = library.load()
    if lib.status == "corrupt":
        raise policy.PolicyError("Recover or reset note policy before setup.")

    notes.ensure_folder(FOLDER)
    listed = notes.list_notes(FOLDER)
    existing = {n.title: n for n in listed}
    if any(sum(n.title == title for n in listed) > 1 for title in SYSTEM_NOTES):
        raise policy.PolicyError('Duplicate system notes; resolve their identity before setup.')
    result: dict[str, str] = {}
    for title in SYSTEM_NOTES:
        if title in existing:
            lib.system_notes[title] = existing[title].id
            result[title] = "kept"
            continue
        lib.system_notes[title] = notes.create_note(FOLDER, markup.render(title, SEEDS[title]))
        result[title] = "created"
    policy.save_policy(library.STATE, lib.payload())
    return result


def readable_system_note(title: str):
    """Only setup-registered IDs can supply standing/system note content."""
    from . import notes, policy
    snap = policy.current()
    if snap.status != 'ready':
        return None
    note = notes.find_note(FOLDER, title)
    return note if note and snap.system_notes.get(title) == note.id and snap.readable(note) else None
def pin_guide() -> list[dict]:
    """Her system notes with their live ids, for the screen that asks the user
    to pin them.

    Nothing here pins anything: Apple's Notes scripting has no `pinned`
    property — not in AppleScript, not in Shortcuts — and the only writable pin
    state is `ZISPINNED` in the TCC-protected iCloud SQLite store, which this
    project will not touch. All she can do is name the note and open it.

    A note `bootstrap()` has not created yet is left out rather than offered
    with no id: a row whose one button does nothing is worse than no row.

    Notes allows two notes with the same name (see CLAUDE.md's Performance
    section); a duplicate system-note title keeps the newest, matching
    `library.suggest()`'s same tie-break rather than whichever the
    AppleScript happened to list last.
    """
    from datetime import datetime

    from . import notes

    live: dict[str, notes.Note] = {}
    for n in notes.list_notes(FOLDER):
        twin = live.get(n.title)
        if twin is None or (n.modified_at or datetime.min) >= (twin.modified_at or datetime.min):
            live[n.title] = n
    return [{"title": t, "id": live[t].id, "why": PIN_WHY[t], "suggested": t in PIN_SUGGESTED}
            for t in PIN_ORDER if t in live]
