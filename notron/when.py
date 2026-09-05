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
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?$")


def parse(value) -> datetime | None:
    """A model's date string as a datetime, or None. Never raises."""
    if not value or not isinstance(value, str) or not ISO.fullmatch(value.strip()):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def has_time(value: str | None) -> bool:
    """Whether the model gave a time of day or only a date."""
    return isinstance(value, str) and bool(re.search(r"[T ]\d{2}:\d{2}", value))


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


@dataclass(frozen=True)
class TimeContext:
    reference: datetime
    now: datetime
    timezone: str
    needs_confirmation: bool = False
    message: str = ""
    capture_known: bool = True


def resolve_time_context(envelope, now: datetime, resumed: bool = False) -> TimeContext:
    """Original capture is evidence; observation is never a recovered capture."""
    if now.tzinfo is None:
        raise ValueError("Current time must be aware")
    zone = ZoneInfo(envelope.timezone)
    reference = (envelope.captured_at or envelope.observed_at).astimezone(zone)
    current = now.astimezone(zone)
    relative = bool(re.search(r"\b(today|tomorrow|tonight|yesterday|next|this|in \d+|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", envelope.text, re.I))
    uncertain = envelope.captured_at is None and relative
    return TimeContext(reference, current, envelope.timezone, uncertain,
                       "The original capture date is unknown. What exact date (YYYY-MM-DD) and time should I use?" if uncertain else "",
                       envelope.captured_at is not None)


def resolve_local(value: str, timezone_name: str) -> datetime:
    """Resolve a wall time only if zoneinfo round trips to one unique instant."""
    dt = parse(value)
    if dt is None:
        raise ValueError("Give an exact date and time")
    if dt.tzinfo is not None:
        return dt
    zone = ZoneInfo(timezone_name)
    candidates = set()
    for fold in (0, 1):
        candidate = dt.replace(tzinfo=zone, fold=fold)
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == dt:
            candidates.add(candidate.astimezone(timezone.utc))
    if len(candidates) != 1:
        raise ValueError("That local time is ambiguous or nonexistent because of daylight saving. Give an explicit UTC offset.")
    return next(iter(candidates)).astimezone(zone)


def unsupported_request(text: str) -> bool:
    # Conservative compound requests wait for separate requests. No partial success.
    return bool(re.search(r"\b(every|each|weekdays|weekends|annually|biweekly|fortnightly|daily|weekly|monthly|yearly|recurr(?:ing|ence)?|and|then|also)\b|[;\n]\s*(?:remind|schedule|create|add|set|complete|tick)\b", text, re.I))


def relative_date_error(value: str | None, envelope) -> str:
    match = re.search(r"\b(today|tonight|tomorrow|yesterday)\b", envelope.text, re.I)
    if not match:
        return ""
    context = resolve_time_context(envelope, datetime.now(timezone.utc))
    expected = context.reference.date() + timedelta(days={'today':0, 'tonight':0, 'tomorrow':1, 'yesterday':-1}[match[0].lower()])
    if not value or resolve_local(value, envelope.timezone).astimezone(ZoneInfo(envelope.timezone)).date() != expected:
        return f"The captured request means {expected.isoformat()}. Confirm that exact date before creating."
    return ""


def duration_explicit(text: str) -> bool:
    """Conservative evidence of a requested duration/end; never model permission."""
    return bool(re.search(r"\b(?:for\s+)?(?:\d+(?:\.\d+)?|an?|one|two|three|half)\s*(?:hours?|hrs?|minutes?|mins?)\b|\b(?:to|until|ends?(?:\s+at)?)\s+(?:\d|noon|midnight)", text, re.I))


def duration_error(text: str, start: str, end: str, timezone_name: str) -> str:
    match = re.search(r"\b(\d+(?:\.\d+)?|an?|one|two|three|half)\s*(hours?|hrs?|minutes?|mins?)\b", text, re.I)
    if not match:
        return ""
    words = {'a':1, 'an':1, 'one':1, 'two':2, 'three':3, 'half':0.5}
    number = words.get(match[1].lower())
    amount = number if number is not None else float(match[1])
    expected = amount * (3600 if match[2].lower().startswith(('h',)) else 60)
    actual = resolve_local(end, timezone_name).timestamp() - resolve_local(start, timezone_name).timestamp()
    return "The proposed event duration does not match your requested duration. Confirm exact start and end times." if actual != expected else ""
