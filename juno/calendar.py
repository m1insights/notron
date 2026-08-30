"""The Calendar app — so the day Juno plans is built around the day you have.

Reading a real calendar is the one place where the wrong query does not merely
run slowly, it never finishes. `every event of calendar` walks years of history;
on a calendar with a decade in it that is minutes, not seconds. Every read here is
a bounded date range, expressed as a `whose start date is greater than …` filter
so the filtering happens inside Calendar rather than across Apple events.

Recurring events also read strangely over AppleScript — a repeating event has one
underlying record, and asking about "the rule" gets you the original, not this
week's instance. Asking for a date range gets you the instances, which is what a
plan actually needs.

Note for anyone editing this file: never write `import calendar` anywhere inside
the `juno` package. Relative imports (`from . import calendar`) are unambiguous;
a bare one gets the standard library.

Calendar is its own single-threaded script target and needs its own macOS
Automation approval — see `juno permissions`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .applescript import run
from .when import human, parse

COLD_START_TIMEOUT = 120
WINDOW_TIMEOUT = 90
MAX_IN_PROMPT = 30

RS = "\x1e"
US = "\x1f"

_ISO = f"""
on iso(d)
  if d is missing value then return ""
  set y to text -4 thru -1 of ("0000" & (year of d))
  set m to text -2 thru -1 of ("00" & ((month of d) as integer))
  set dy to text -2 thru -1 of ("00" & (day of d))
  set h to text -2 thru -1 of ("00" & (hours of d))
  set mi to text -2 thru -1 of ("00" & (minutes of d))
  return y & "-" & m & "-" & dy & "T" & h & ":" & mi
end iso
"""

_WINDOW = _ISO + f"""
on run argv
  set backDays to (item 1 of argv) as integer
  set fwdDays to (item 2 of argv) as integer
  set d1 to (current date) - (backDays * days)
  set d2 to (current date) + (fwdDays * days)
  set rows to {{}}
  tell application "Calendar"
    repeat with c in calendars
      set cn to name of c
      set es to (every event of c whose start date is greater than d1 and start date is less than d2)
      if (count of es) > 0 then
        set su to summary of es
        set sd to start date of es
        set ed to end date of es
        set lo to location of es
        repeat with k from 1 to count of su
          set theLoc to item k of lo
          if theLoc is missing value then set theLoc to ""
          set end of rows to cn & "{RS}" & (item k of su) & "{RS}" & (my iso(item k of sd)) & "{RS}" & (my iso(item k of ed)) & "{RS}" & theLoc
        end repeat
      end if
    end repeat
  end tell
  set text item delimiters to "{US}"
  return rows as text
end run
"""

_NAMES = f"""
on run argv
  tell application "Calendar" to set n to name of every calendar
  set text item delimiters to "{US}"
  return n as text
end run
"""

_CREATE = """
on mkdate(y, m, d, h, mi)
  set dt to current date
  set day of dt to 1
  set year of dt to y
  set month of dt to m
  set day of dt to d
  set hours of dt to h
  set minutes of dt to mi
  set seconds of dt to 0
  return dt
end mkdate

on run argv
  set theTitle to item 1 of argv
  set calName to item 2 of argv
  set theNotes to item 3 of argv
  set sd to my mkdate((item 4 of argv) as integer, (item 5 of argv) as integer, ¬
                      (item 6 of argv) as integer, (item 7 of argv) as integer, ¬
                      (item 8 of argv) as integer)
  set ed to my mkdate((item 9 of argv) as integer, (item 10 of argv) as integer, ¬
                      (item 11 of argv) as integer, (item 12 of argv) as integer, ¬
                      (item 13 of argv) as integer)
  tell application "Calendar"
    if calName is "" then
      set target to missing value
      repeat with c in calendars
        if writable of c then
          set target to c
          exit repeat
        end if
      end repeat
      if target is missing value then error "no writable calendar"
    else
      set target to calendar calName
    end if
    tell target
      set e to make new event with properties {summary:theTitle, start date:sd, end date:ed, description:theNotes}
    end tell
    return uid of e
  end tell
end run
"""


@dataclass(frozen=True)
class Event:
    calendar: str
    title: str
    start: str        # ISO
    end: str          # ISO
    location: str = ""

    @property
    def starts_at(self) -> datetime | None:
        return parse(self.start)


def warm_up() -> float:
    import time

    started = time.time()
    run(_NAMES, timeout=COLD_START_TIMEOUT, retries=0)
    return time.time() - started


def names(*, runner=None) -> list[str]:
    return [n for n in (runner or run)(_NAMES).split(US) if n.strip()]


def window(*, back: int = 0, days: int = 7, runner=None) -> list[Event]:
    """Every event starting inside a bounded range. The only safe way to read."""
    caller = runner or (lambda s, *a: run(s, *a, timeout=WINDOW_TIMEOUT))
    raw = caller(_WINDOW, str(back), str(days))
    if not raw.strip():
        return []
    out: list[Event] = []
    for row in raw.split(US):
        parts = row.split(RS)
        if len(parts) != 5 or not parts[1].strip():
            continue
        out.append(Event(calendar=parts[0], title=parts[1], start=parts[2],
                         end=parts[3], location=parts[4]))
    return sorted(out, key=lambda e: e.start)


def brief(*, on: datetime | None = None, runner=None) -> str:
    """Today, as a few lines a planner can actually use."""
    day = (on or datetime.now()).date()
    todays = [e for e in window(days=2, runner=runner)
              if e.starts_at and e.starts_at.date() == day]
    if not todays:
        return "Nothing in the calendar today."
    return "\n".join(
        f"- {e.starts_at:%H:%M} {e.title}" + (f" — {e.location}" if e.location else "")
        for e in todays
    )


def week(*, runner=None, limit: int = MAX_IN_PROMPT) -> str:
    """The next seven days, grouped by day."""
    events = window(days=7, runner=runner)[:limit]
    if not events:
        return "Nothing in the calendar this week."
    lines, seen = [], None
    for e in events:
        at = e.starts_at
        if at is None:
            continue
        if at.date() != seen:
            seen = at.date()
            lines.append(f"**{human(at, with_time=False)}**")
        lines.append(f"- {at:%H:%M} {e.title}")
    return "\n".join(lines)


DEFAULT_MINUTES = 60


def create(title: str, *, start_iso: str, end_iso: str | None = None,
           calendar_name: str = "", notes: str = "", runner=None) -> str:
    """Add an event. Returns its uid.

    There is no `update`, `move` or `delete` in this module, and there never will
    be. The user's standing instructions say nothing in their calendar gets moved
    without asking, and the strongest way to keep that promise is not to write the
    code that could break it.
    """
    from datetime import timedelta

    from . import when as when_mod

    start = when_mod.parse(start_iso)
    if start is None:
        raise ValueError(f"unusable start date {start_iso!r}")
    end = when_mod.parse(end_iso) or (start + timedelta(minutes=DEFAULT_MINUTES))
    if end <= start:
        end = start + timedelta(minutes=DEFAULT_MINUTES)
    args = (title, calendar_name, notes) + when_mod.components(start) + when_mod.components(end)
    return (runner or run)(_CREATE, *args)
