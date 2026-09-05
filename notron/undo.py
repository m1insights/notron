"""One step back, per note. Not a history — a single saved copy of whatever
a note held immediately before Notron's last write to it, consumed the
moment it's used. See docs/plans/2026-09-03-rewrite-permission-and-undo-design.md
decision 4."""

from __future__ import annotations

import json
import pathlib

from .paths import DATA_DIR
STATE = DATA_DIR / "undo.json"


def _load() -> dict:
    from .securestore import read_json, write_json
    from . import policy
    data = read_json(STATE)
    if not all(isinstance(nid, str) and nid and isinstance(body, str) for nid, body in data.items()):
        from .securestore import IntegrityError
        raise IntegrityError("Encrypted undo schema invalid; processing paused.")
    safe = {nid: body for nid, body in data.items() if policy.current().can_read(nid)}
    if safe != data:
        write_json(STATE, safe)
    return safe


def _write(data: dict) -> None:
    from .securestore import write_json
    write_json(STATE, data)


def save(note_id: str, old_body: str) -> None:
    from . import policy
    if not policy.current().can_read(note_id):
        return
    if not old_body:            # nothing existed before this write — nothing to undo to
        return
    data = _load()
    data[note_id] = old_body
    _write(data)


def pop(note_id: str) -> str | None:
    data = _load()
    body = data.pop(note_id, None)
    if body is not None:
        _write(data)
    return body
