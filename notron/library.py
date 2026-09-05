"""Which of the user's notes Notron may read, and which she may file into.

A notes library is years deep: three notes called "Supps", a password note in
the main list, experiments from 2019. Two things go wrong without a say-so —
she files a line into the wrong-but-similar note (a THC log landed in an App
Store listing whose title said "supplements"), and she reads something that was
never meant for a model. So the user tells her, once, per note:

  home     a note she may file lines into — the Filer's only destinations
  read     explicit permission to retrieve and answer tags, never auto-file
  ignore   she never reads it — not for filing, not for questions, not even a
           tag inside it

Choices live in `.notron/library.json`, keyed by note id, so a rename changes
nothing. The Mac app saves through the validated Python writer; every reader in the core lists notes
through `user_notes()` here, so "never reads it" is one rule in one place.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from datetime import datetime

from . import notes, workspace, policy

STATE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "library.json"

HOME, READ, IGNORE = "home", "read", "ignore"


@dataclass
class Library:
    homes: set[str] = field(default_factory=set)
    ignore: set[str] = field(default_factory=set)
    #: Every note the user had in front of them when they pressed Done. A note
    #: they decided on is governed by that decision, never by the year cutoff.
    decided: set[str] = field(default_factory=set)
    start_from: datetime | None = None
    chosen_at: str = ""
    allow_new_notes: bool = False
    system_notes: dict[str, str] = field(default_factory=dict)
    status: str = "unconfigured"

    @property
    def configured(self) -> bool:
        return self.snapshot().status == 'ready'

    def snapshot(self) -> policy.PolicySnapshot:
        if self.status == 'corrupt':
            return policy.PolicySnapshot('corrupt')
        return policy.decode_policy(self.payload())

    def payload(self) -> dict:
        return dict(version=1, homes=sorted(self.homes), ignore=sorted(self.ignore),
                    decided=sorted(self.decided), chosen_at=self.chosen_at,
                    start_from=self.start_from.strftime('%Y-%m-%d') if self.start_from else None,
                    allow_new_notes=self.allow_new_notes, system_notes=dict(self.system_notes))

    def hides(self, note_id: str, modified: str) -> bool:
        return not self.snapshot().readable(notes.Note(note_id, '', '', modified))

    def is_ignored(self, note: notes.Note) -> bool:
        return not self.snapshot().readable(note)

    def state_of(self, note: notes.Note) -> str:
        if self.is_ignored(note):
            return IGNORE
        return HOME if note.id in self.homes else READ


def parse_start(text: str) -> datetime:
    """'2026' or '2026-06-15'. Anything else is a ValueError, said plainly."""
    text = text.strip()
    for fmt in ("%Y", "%Y-%m", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"couldn't read {text!r} as a year or a date — try 2026 or 2026-06-15")


def load() -> Library:
    snap = policy.load_policy(STATE)
    return Library(homes=set(snap.homes), ignore=set(snap.ignore), decided=set(snap.decided),
                   start_from=snap.start_from, chosen_at=snap.chosen_at,
                   allow_new_notes=snap.allow_new_notes, system_notes=dict(snap.system_notes),
                   status=snap.status)


def save(lib: Library, *, reset: bool = False) -> None:
    lib.chosen_at = lib.chosen_at or datetime.now().isoformat(timespec='minutes')
    policy.save_policy(STATE, lib.payload(), reset=reset)
    lib.status = 'ready'
    from . import retention
    retention.apply_policy()


def user_notes(lib: Library | None = None) -> list[notes.Note]:
    """Every note of theirs she is allowed to read. The only way the core
    should ever list the user's notes."""
    lib = lib or load()
    if not lib.configured:
        return []
    return [n for n in notes.list_all_notes()
            if n.folder != workspace.FOLDER and not lib.is_ignored(n)]


# ------------------------------------------------------------- the pre-fill

import re as _re

from . import privacy

#: How many homes the screen opens with. A short list gets unticked; a long
#: one gets ignored.
MAX_HOMES = 10
HOME_SCORE = 4  # recency(2) + title(1) alone must never clear this — a note
                # only becomes a suggested home when its body is list-shaped too


@dataclass(frozen=True)
class Suggestion:
    note: notes.Note
    state: str
    reason: str          # shown under the row — why she guessed this


def _recency(note: notes.Note, now: datetime) -> int:
    at = note.modified_at
    if at is None:
        return 0
    days = (now - at).days
    return 2 if days <= 60 else 1 if days <= 365 else 0


def _list_shaped(text: str) -> int:
    """A note people keep things in is many short lines, not paragraphs."""
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < 6:
        return 0
    short = sum(len(l) <= 60 for l in lines) / len(lines)
    return 2 if short >= 0.6 else 0


def _title_like_a_place(title: str) -> int:
    return 1 if len(title) <= 40 and not _re.search(r"[.!?]", title) else 0


def suggest(all_notes: list[notes.Note], texts: dict[str, str], *,
            now: datetime | None = None) -> list[Suggestion]:
    """Notron's guess for every note. Plain code — no model reads anything here.

    `texts` is the opening of each note by id (from the index; empty when the
    user has not run `notron index` yet, in which case shape is unknown and
    only recency and the title count)."""
    now = now or datetime.now()
    by_title: dict[str, list[notes.Note]] = {}
    for n in all_notes:
        by_title.setdefault(n.title, []).append(n)

    scored: list[tuple[int, notes.Note, str]] = []
    out: dict[str, Suggestion] = {}
    for n in all_notes:
        if privacy.is_vault(n.title):
            out[n.id] = Suggestion(n, IGNORE, "looks like passwords")
            continue
        if privacy.is_private(n.title):
            out[n.id] = Suggestion(n, IGNORE, "looks private")
            continue
        twins = by_title[n.title]
        newest = max(twins, key=lambda t: t.modified_at or datetime.min)
        if len(twins) > 1 and n is not newest:
            out[n.id] = Suggestion(n, READ, f"{len(twins)} notes share this name")
            continue
        score = _recency(n, now) + _list_shaped(texts.get(n.id, "")) + _title_like_a_place(n.title)
        why = ", ".join(w for w, on in (("edited recently", _recency(n, now) == 2),
                                        ("list-shaped", _list_shaped(texts.get(n.id, "")) > 0)) if on)
        scored.append((score, n, why))

    scored.sort(key=lambda s: (-s[0], -(s[1].modified_at.timestamp() if s[1].modified_at else 0)))
    homes = 0
    for score, n, why in scored:
        if score >= HOME_SCORE and homes < MAX_HOMES:
            out[n.id] = Suggestion(n, HOME, why)
            homes += 1
        else:
            out[n.id] = Suggestion(n, READ, "")
    return [out[n.id] for n in all_notes]


# ------------------------------------------------------------- for the GUI

_ORDER = {HOME: 0, READ: 1, IGNORE: 2}


def scan(lib: Library | None = None) -> dict:
    """Every note with its state, for the "Your notes" screen and `notron library`.

    Before the user has chosen, `state` is the guess; after, it is their choice,
    with the guess still alongside so the screen can say why."""
    from . import index

    lib = lib or load()
    live = [n for n in notes.list_all_notes() if n.folder != workspace.FOLDER]
    guesses = {s.note.id: s for s in suggest(live, index.glimpses(1400, keep_lines=True))}
    rows = []
    for n in live:
        g = guesses[n.id]
        rows.append({
            "id": n.id, "title": n.title, "folder": n.folder, "modified": n.modified,
            "state": lib.state_of(n) if lib.configured else g.state,
            "suggested": g.state, "reason": g.reason,
            "sensitive": privacy.is_vault(n.title) or privacy.is_private(n.title),
        })
    rows.sort(key=lambda r: (_ORDER[r["state"]], -(_stamp(r["modified"]))))
    by_title: dict[str, list[str]] = {}
    for r in rows:
        by_title.setdefault(r["title"], []).append(r["id"])
    counts = {s: sum(r["state"] == s for r in rows) for s in (HOME, READ, IGNORE)}
    return {
        "notes": rows,
        "duplicates": {t: ids for t, ids in by_title.items() if len(ids) > 1},
        "start_from": lib.start_from.strftime("%Y-%m-%d") if lib.start_from else None,
        "configured": lib.configured,
        "status": lib.snapshot().status,
        "counts": counts,
    }


#: A note longer than this is cut for the preview panel; nobody triages by
#: reading twenty thousand characters, and the whole thing crosses a pipe.
PEEK_CHARS = 20_000


def peek(note_id: str, *, reveal: bool = False) -> dict:
    """One note's body as plain text, for the person deciding what to do with it.

    Deliberately *not* filtered through `user_notes()`: an ignored note is
    exactly the one they need to look at before agreeing it should stay ignored.
    Nothing here goes near a model — the text is read from Notes and handed
    straight to the Mac app's preview panel, on a click the user made.

    But a title is not enough of a guard. The user's real library has a note
    called "CRITICAL" whose body is nothing but Obsidian recovery codes;
    `privacy.py` does not flag that title, and it sorted first in the list. So
    the body is checked too, and a note holding anything credential-shaped comes
    back empty with `held` set until the user asks for it by name. The point is
    not the model here — it is the screen, and whoever else can see it.
    """
    from . import markup

    text = markup.to_text(notes.read_body(note_id))
    looks_secret = privacy.contains_secret(text) or privacy.is_key_dump(text)
    held = "" if reveal else ("secret" if looks_secret else "")
    return {
        "id": note_id,
        "text": "" if held else text[:PEEK_CHARS],
        "chars": len(text),
        "truncated": not held and len(text) > PEEK_CHARS,
        "held": held,
    }


def _stamp(modified: str) -> float:
    at = notes.Note("", "", "", modified).modified_at
    return at.timestamp() if at else 0.0


def add_home(note_id: str) -> None:
    """A newly created note explicitly approved with yes becomes a filing home."""
    lib = load()
    if not lib.configured:
        raise policy.PolicyError('Configure note permissions before adding a home.')
    lib.homes.add(note_id)
    lib.decided.add(note_id)
    lib.ignore.discard(note_id)
    save(lib)


def save_selection(raw: dict) -> None:
    """Mac selection transport. Preserve setup IDs and settings absent in its UI."""
    selected = policy.decode_policy(raw)
    lib = load()
    if lib.status == 'corrupt':
        raise policy.PolicyError('Policy corrupt; use notron library recover or --reset.')
    lib.homes = set(selected.homes)
    lib.ignore = set(selected.ignore)
    lib.decided = set(selected.decided)
    lib.start_from = selected.start_from
    lib.chosen_at = selected.chosen_at
    save(lib)
