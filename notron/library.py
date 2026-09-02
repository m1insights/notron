"""Which of the user's notes Notron may read, and which she may file into.

A notes library is years deep: three notes called "Supps", a password note in
the main list, experiments from 2019. Two things go wrong without a say-so —
she files a line into the wrong-but-similar note (a THC log landed in an App
Store listing whose title said "supplements"), and she reads something that was
never meant for a model. So the user tells her, once, per note:

  home     a note she may file lines into — the Filer's only destinations
  read     the default: she may read it to answer questions, never file into it
  ignore   she never reads it — not for filing, not for questions, not even a
           tag inside it

Choices live in `.notron/library.json`, keyed by note id, so a rename changes
nothing. The Mac app writes the file; every reader in the core lists notes
through `user_notes()` here, so "never reads it" is one rule in one place.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime

from . import notes, workspace

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

    @property
    def configured(self) -> bool:
        return bool(self.homes or self.ignore or self.decided or self.start_from)

    def hides(self, note_id: str, modified: str) -> bool:
        """The one rule. Explicit choices first, the year only for notes the
        user never looked at, and an unreadable date is read rather than hidden."""
        if note_id in self.ignore:
            return True
        if note_id in self.homes or note_id in self.decided or self.start_from is None:
            return False
        at = notes.Note(note_id, "", "", modified).modified_at
        return at is not None and at < self.start_from

    def is_ignored(self, note: notes.Note) -> bool:
        return self.hides(note.id, note.modified)

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
    try:
        raw = json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return Library()
    start = raw.get("start_from")
    try:
        start_at = parse_start(start) if start else None
    except ValueError:
        start_at = None
    return Library(
        homes=set(raw.get("homes") or []),
        ignore=set(raw.get("ignore") or []),
        decided=set(raw.get("decided") or []),
        start_from=start_at,
        chosen_at=raw.get("chosen_at") or "",
    )


def save(lib: Library) -> None:
    lib.chosen_at = lib.chosen_at or datetime.now().isoformat(timespec="minutes")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "homes": sorted(lib.homes),
        "ignore": sorted(lib.ignore),
        "decided": sorted(lib.decided),
        "start_from": lib.start_from.strftime("%Y-%m-%d") if lib.start_from else None,
        "chosen_at": lib.chosen_at,
    }, indent=1))


def user_notes(lib: Library | None = None) -> list[notes.Note]:
    """Every note of theirs she is allowed to read. The only way the core
    should ever list the user's notes."""
    lib = lib or load()
    return [n for n in notes.list_all_notes()
            if n.folder != workspace.FOLDER and not lib.is_ignored(n)]
