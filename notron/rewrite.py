"""Per-note permission to rewrite a note in place, instead of only adding to it.

Invariant #2 in CLAUDE.md — outside 🤖 NOTRON, Notron may only add — never
changes for a note nobody has opted in. A note earns rewrite permission one of
two ways: the user types `yes` under the real before/after `organizer` shows
them on that note (decision 2), or the note is brand new and the global
default for new notes is "always" (decision 3). Either way the choice lives
here, in its own file — this is a different question from `library.py`'s
home/read-only/ignore, and a Home, a Read-only or an Ignore-adjacent note can
each independently answer it.

See docs/plans/2026-09-03-rewrite-permission-and-undo-design.md.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime

STATE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "rewrite.json"

DEFAULTS = ("ask", "always", "never")


def _load() -> dict:
    try:
        raw = json.loads(STATE.read_text())
    except (OSError, ValueError):
        raw = {}
    return {
        "allow": list(raw.get("allow") or []),
        "default_new": raw.get("default_new") or "ask",
        "chosen_at": raw.get("chosen_at") or "",
        # Its own field, deliberately not "chosen_at" — that one is stamped by
        # allow() for a per-note yes, a different question from "has the user
        # ever picked the global default." Sharing one field meant the Mac
        # app's onboarding sheet (gated on this) would never show for a user
        # who'd already said `@notron yes` on a single note.
        "default_chosen_at": raw.get("default_chosen_at") or "",
    }


def _write(data: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(data, indent=1))
    except OSError:
        pass


def allowed(note_id: str) -> bool:
    return note_id in _load()["allow"]


def allow(note_id: str) -> None:
    data = _load()
    if note_id not in data["allow"]:
        data["allow"].append(note_id)
        data["chosen_at"] = datetime.now().isoformat(timespec="minutes")
        _write(data)


def default_for_new_notes() -> str:
    return _load()["default_new"]


def default_chosen() -> bool:
    """Has the user ever picked the global default, as opposed to just
    saying yes to one note's organizer offer? What the onboarding sheet
    checks before showing itself."""
    return bool(_load()["default_chosen_at"])


def set_default_for_new_notes(value: str) -> None:
    if value not in DEFAULTS:
        raise ValueError(f"{value!r} isn't a default — use one of {DEFAULTS}")
    data = _load()
    data["default_new"] = value
    data["default_chosen_at"] = datetime.now().isoformat(timespec="minutes")
    _write(data)
