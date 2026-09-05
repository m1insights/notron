"""Per-note permission to rewrite a note in place, instead of only adding to it.

Invariant #2 in CLAUDE.md — outside 🤖 NOTRON, Notron may only add — never
changes for a note nobody has opted in. A note earns rewrite permission one of
two ways: the user types `yes` under the real before/after `organizer` shows
them on that note (decision 2), or the note is brand new and the global
default for new notes is "always" (decision 3). Either way the choice lives
here, in its own file — this is a different question from `library.py`'s
home/read-only/ignore. Only a Home can exercise a rewrite grant; Read only and
Ignore always restrict it. Malformed state never grants permission.

See docs/plans/2026-09-03-rewrite-permission-and-undo-design.md.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime

from . import policy
from .persistence import atomic_write_json, atomic_write_bytes

STATE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "rewrite.json"

DEFAULTS = ("ask", "always", "never")


def _decode(raw) -> dict:
    if (not isinstance(raw, dict)
            or ('version' in raw and (type(raw['version']) is not int or raw['version'] != 1))
            or not isinstance(raw.get('allow', []), list)
            or any(not isinstance(n, str) or not n for n in raw.get('allow', []))
            or raw.get('default_new', 'ask') not in DEFAULTS
            or any(not isinstance(raw.get(k, ''), str)
                   for k in ('chosen_at', 'default_chosen_at'))):
        raise ValueError('invalid rewrite permissions')
    return dict(version=1, allow=raw.get('allow', []),
                default_new=raw.get('default_new', 'ask'),
                chosen_at=raw.get('chosen_at', ''),
                default_chosen_at=raw.get('default_chosen_at', ''))


def _load() -> dict:
    try:
        return _decode(json.loads(STATE.read_text()))
    except (OSError, ValueError):
        return _decode({})


def _write(data: dict) -> None:
    data = _decode(data)
    if STATE.exists():
        try:
            previous = STATE.read_bytes()
            _decode(json.loads(previous))
        except (OSError, ValueError) as exc:
            raise policy.PolicyError('Rewrite policy corrupt; use notron rewrite --recover.') from exc
        atomic_write_bytes(STATE.with_name(STATE.name + '.bak'), previous)
    atomic_write_json(STATE, data)


def restore_permissions() -> None:
    try:
        previous = STATE.with_name(STATE.name + '.bak').read_bytes()
        _decode(json.loads(previous))
    except (OSError, ValueError) as exc:
        raise policy.PolicyError('No validated rewrite backup available.') from exc
    if STATE.exists():
        atomic_write_bytes(STATE.with_name(STATE.name + '.corrupt'), STATE.read_bytes())
    atomic_write_bytes(STATE, previous)


def allowed(note_id: str) -> bool:
    return policy.current().can_file(note_id) and note_id in _load()["allow"]


def allow(note_id: str) -> None:
    data = _load()
    if note_id not in data["allow"]:
        data["allow"].append(note_id)
        data["chosen_at"] = datetime.now().isoformat(timespec="minutes")
        _write(data)


def default_for_new_notes() -> str:
    return _load()['default_new'] if policy.current().status == 'ready' else 'ask'


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
