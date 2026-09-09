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
    # A clear timing clause before the task takes precedence over dates *in*
    # the task: "remind me tonight to book a table for Saturday" fires tonight.
    # This is independent of the scheduler's model-supplied fields.
    prefix = re.match(r'\s*remind\s+me\s+(.+?)\s+to\s+\S', text)
    if prefix and re.search(r'\b(today|tonight|tomorrow|yesterday)\b', prefix[1]):
        text = prefix[1]
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
    # Detection does not resolve these phrases: word/article quantities still
    # depend on the unknown capture reference and must ask for an exact date.
    relative = bool(re.search(
        r"\b(today|tomorrow|tonight|yesterday|next|this|in\s+\d+|from now|later|soon|hence|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
        r"\b(?:in|after|within)\s+(?:\w+[\s-]+){0,5}"
        r"(?:seconds?|minutes?|hours?|days?|weeks?|fortnights?|months?|years?)\b",
        envelope.text, re.I))
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
    # Plural weekdays request recurrence even without "every" or "weekly".
    if re.search(r"\b(?:" + "|".join(WEEKDAYS) + r")s\b", text, re.I):
        return True
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


_DURATION = re.compile(
    r"\b(\d+(?:\.\d+)?|an?|one|two|three|half)\s*(hours?|hrs?|minutes?|mins?)\b", re.I)
# A destination number is not an end time. Supported clock forms require a
# colon or am/pm; a complete ISO timestamp and noon/midnight are also explicit.
_END_TIME = re.compile(
    r"\b(?:to|until|ends?(?:\s+at)?)\s+(?P<end>"
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?|"
    r"(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*[ap]m)?|"
    r"(?:1[0-2]|[1-9])\s*[ap]m|noon|midnight)(?![\w:])", re.I)


def duration_explicit(text: str) -> bool:
    """Conservative evidence of a requested duration/end; never model permission."""
    return bool(_DURATION.search(text) or _END_TIME.search(text))


def duration_error(text: str, start: str, end: str, timezone_name: str) -> str:
    durations, ends = list(_DURATION.finditer(text)), list(_END_TIME.finditer(text))
    if len(durations) > 1 or len(ends) > 1:
        return "I found multiple duration or end-time references. Confirm one exact start and end."
    if durations:
        match = durations[0]
        words = {'a':1, 'an':1, 'one':1, 'two':2, 'three':3, 'half':0.5}
        number = words.get(match[1].lower())
        amount = number if number is not None else float(match[1])
        expected = amount * (3600 if match[2].lower().startswith('h') else 60)
        actual = resolve_local(end, timezone_name).timestamp() - resolve_local(start, timezone_name).timestamp()
        if actual != expected:
            return "The proposed event duration does not match your requested duration. Confirm exact start and end times."
    if ends:
        value = ends[0]['end']
        if parse(value) is not None:
            expected_end = resolve_local(value, timezone_name)
        else:
            clock = value.lower().replace(' ', '')
            if clock in ('noon', 'midnight'):
                hour, minute = (12 if clock == 'noon' else 0), 0
            else:
                period = clock[-2:] if clock.endswith(('am', 'pm')) else ''
                digits = clock[:-2] if period else clock
                hour, _, minute = digits.partition(':')
                hour, minute = int(hour), int(minute or 0)
                if period:
                    if not 1 <= hour <= 12:
                        return "Confirm an unambiguous end time."
                    hour = hour % 12 + (12 if period == 'pm' else 0)
            local_start = resolve_local(start, timezone_name).astimezone(ZoneInfo(timezone_name))
            wall_end = datetime(local_start.year, local_start.month, local_start.day, hour, minute)
            if wall_end <= local_start.replace(tzinfo=None):
                wall_end += timedelta(days=1)
            expected_end = resolve_local(wall_end.isoformat(), timezone_name)
        if expected_end.timestamp() != resolve_local(end, timezone_name).timestamp():
            return "The proposed event end does not match your requested end time. Confirm exact start and end times."
    return ""
