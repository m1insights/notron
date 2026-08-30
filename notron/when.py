"""Dates, moved between a language model, Python and AppleScript without drift.

Three separate things go wrong here, and each has bitten someone before:

  * A model asked for "Thursday" happily returns a Friday. So every resolved date
    is checked against any weekday the user actually named, and every confirmation
    says the weekday out loud.
  * `date "3/9/2026"` in AppleScript is read using the Mac's region — the third of
    September in London, the ninth of March in New York. Never build a date from a
    string. Pass the numbers.
  * A model can return anything at all. Parsing must return None, never raise;
    a bad date is something the Guard refuses, not something that stops the graph.
"""

from __future__ import annotations

import re
from datetime import datetime

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse(value) -> datetime | None:
    """A model's date string as a datetime, or None. Never raises."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "").split("+")[0]
    for fmt in FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def has_time(value: str | None) -> bool:
    """Whether the model gave a time of day or only a date."""
    return bool(value) and "T" in value


def components(dt: datetime) -> tuple[str, ...]:
    """Year, month, day, hour, minute as argv strings for AppleScript."""
    return (str(dt.year), str(dt.month), str(dt.day), str(dt.hour), str(dt.minute))


def human(dt: datetime, *, with_time: bool = True) -> str:
    """'Thursday 3 September at 09:00' — the weekday is the point."""
    stamp = f"{dt:%A} {dt.day} {dt:%B}"
    if with_time and (dt.hour or dt.minute):
        stamp += f" at {dt:%H:%M}"
    return stamp


def weekday_named(request: str) -> int | None:
    """The weekday the user actually said, as Monday=0, or None."""
    text = request.lower()
    for i, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", text):
            return i
    return None
