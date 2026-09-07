"""What she has just booked, so a retry does not book it again.

`doer` runs before `writer` on purpose — she must not claim in a note that a
reminder is set before it is. The cost of that ordering is that the note write
can still fail afterwards, and "the note changed while she was writing"
(`executor.py`) is a normal outcome, not a rare one. The watcher then retries
the whole graph (`watch.MAX_TRIES`): router, scheduler, doer, all again, from
the same question text. Nothing compared the second reminder to the first, so
one question made two.

The window is short and deliberate. This guards a retry loop and a slip of the
hand — the user pasting the same line twice, or tagging her in two notes about
the same thing. Asking for the same reminder again next week is a person
meaning it, and she should do it.

Everything here refuses or does nothing. There is no path in this file that
edits or removes a reminder or an event that already exists — invariants #6 and
#7 hold because the only outcome of a match is that Notron does *less*.
"""

from __future__ import annotations

import json
import pathlib
import re
import time

STATE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "actions.json"

#: How long two identical requests are treated as one. Long enough to cover a
#: retry (`watch.MAX_TRIES` passes, seconds apart) and a double paste; short
#: enough that a real second thought half an hour later still books.
WINDOW_SECONDS = 600

#: A ceiling, so a file nobody prunes cannot grow all year.
MAX_KEPT = 200

_SPACE = re.compile(r"\s+")


def fingerprint(action) -> str:
    """What makes two requests the same request.

    Title is squashed and lowercased: the model rephrases spacing and case
    between passes on the same question, and "Call the pharmacy" twice is one
    thing however it was typed. Kind, op and time are exact — "remind me at 2"
    and "remind me at 3" are two things, and always were.
    """
    title = _SPACE.sub(" ", (action.title or "").strip()).lower()
    return "|".join((action.kind or "", action.op or "", title, action.when or ""))


def _load() -> dict:
    try:
        data = json.loads(STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # A corrupt cache must never be the reason a real reminder is refused.
        return {}


def _save(data: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(data))
    except OSError:
        pass


def already(action, *, now=None) -> str | None:
    """The reference of the thing she booked a moment ago, if this is it again."""
    clock = now or time.time
    row = _load().get(fingerprint(action))
    if not row:
        return None
    when, ref = row.get("at", 0), row.get("ref") or ""
    return ref if clock() - when < WINDOW_SECONDS else None


def remember(action, ref: str, *, now=None) -> None:
    """Record one thing that really was created. Called only after it worked."""
    clock = now or time.time
    data = _load()
    data[fingerprint(action)] = {"at": clock(), "ref": ref}
    if len(data) > MAX_KEPT:
        keep = sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-MAX_KEPT:]
        data = dict(keep)
    _save(data)
