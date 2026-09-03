"""One step back, per note. Not a history — a single saved copy of whatever
a note held immediately before Notron's last write to it, consumed the
moment it's used. See docs/plans/2026-09-03-rewrite-permission-and-undo-design.md
decision 4."""

from __future__ import annotations

import json
import pathlib

STATE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "undo.json"


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(data))
    except OSError:
        pass


def save(note_id: str, old_body: str) -> None:
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
