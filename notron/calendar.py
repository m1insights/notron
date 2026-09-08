"""The Calendar app — so the day Notron plans is the day you actually have.

Reads go through `eventkit`. Driving the Calendar app instead was measured at 26
seconds for a single seven-day window, because a `whose start date …` filter walks
every event the calendar has ever held — on this Mac, 1,757 of them across seven
calendars, one of which alone holds 1,256. EventKit answers the same question in
0.025 seconds. See `eventkit.py`.

Reads are still expressed as bounded windows even though EventKit is fast. A window
is what a plan actually needs, and it is the habit that keeps this file honest.

Recurring events are handled for free by asking for a date range: EventKit expands
occurrences inside the window, where the app's scripting interface would hand back
the original rule and leave you to work out this week's instance yourself.

Note for anyone editing this file: never write a bare `import calendar` anywhere in
the `notron` package. Relative imports (`from . import calendar`) are unambiguous; a
bare one gets the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from . import eventkit
from .when import human, parse

MAX_IN_PROMPT = 30

#: A stop against one absurd multi-year event becoming a thousand headings.
MAX_DAYS = 14
DEFAULT_MINUTES = 60

# The bounds are computed in Python and handed over as text, not as an offset
# from `now`. `dateWithTimeIntervalSinceNow(0)` is what made `brief()` under-
# report the day: run at 10:00 it started the window at 10:00, and the 09:00
# meeting the user had just come out of was simply not there.
_WINDOW = """
var store = $.EKEventStore.alloc.init;
var cals = store.calendarsForEntityType($.EKEntityTypeEvent);
var f = pinned("yyyy-MM-dd'T'HH:mm");
var start = f.dateFromString(%(start)s);
var end = f.dateFromString(%(end)s);
var pred = store.predicateForEventsWithStartDateEndDateCalendars(start, end, cals);
var events = store.eventsMatchingPredicate(pred);
var rows = [];
for (var i = 0; i < events.count; i++) {
  var e = events.objectAtIndex(i);
  rows.push({
    calendar: ObjC.unwrap(e.calendar.title) || '',
    title: ObjC.unwrap(e.title) || '',
    start: ObjC.unwrap(f.stringFromDate(e.startDate)),
    end: e.endDate.isNil() ? '' : ObjC.unwrap(f.stringFromDate(e.endDate)),
    location: e.location.isNil() ? '' : ObjC.unwrap(e.location),
    all_day: e.isAllDay ? true : false
  });
}
JSON.stringify(rows);
"""

_NAMES = """
var store = $.EKEventStore.alloc.init;
var cals = store.calendarsForEntityType($.EKEntityTypeEvent);
var names = [];
for (var i = 0; i < cals.count; i++) names.push(ObjC.unwrap(cals.objectAtIndex(i).title));
JSON.stringify(names);
"""

_CREATE = """
var store = $.EKEventStore.alloc.init;
var e = $.EKEvent.eventWithEventStore(store);
e.title = %(title)s;
e.notes = %(notes)s;
var f = pinned("yyyy-MM-dd'T'HH:mm");
e.startDate = f.dateFromString(%(start)s);
e.endDate = f.dateFromString(%(end)s);
var wanted = %(calendar)s;
var target = $();
var cals = store.calendarsForEntityType($.EKEntityTypeEvent);
for (var i = 0; i < cals.count; i++) {
  var c = cals.objectAtIndex(i);
  if (wanted && ObjC.unwrap(c.title) === wanted) { target = c; break; }
}
if (target.isNil()) target = store.defaultCalendarForNewEvents;
e.calendar = target;
var err = Ref();
var ok = store.saveEventSpanCommitError(e, $.EKSpanThisEvent, true, err);
JSON.stringify(ok ? {id: ObjC.unwrap(e.eventIdentifier),
                     calendar: ObjC.unwrap(target.title)}
                  : {error: 'save failed', why: reason(err)});
"""


@dataclass(frozen=True)
class Event:
    calendar: str
    title: str
    start: str        # ISO
    end: str          # ISO
    location: str = ""
    all_day: bool = False

    @property
    def starts_at(self) -> datetime | None:
        return parse(self.start)

    @property
    def ends_at(self) -> datetime | None:
        return parse(self.end)

    @property
    def last_day(self) -> date | None:
        """The last day this event actually occupies.

        EventKit ends a one-day all-day event at the *following* midnight, so
        taking `end.date()` literally puts a phantom offsite on tomorrow. A
        timed event finishing exactly at midnight belongs to the day before it
        for the same reason.
        """
        start, end = self.starts_at, self.ends_at
        if start is None:
            return None
        if end is None or end <= start:
            return start.date()
        if end.time() == time(0, 0):
            end -= timedelta(days=1)
        return max(start.date(), end.date())

    def covers(self, day: date) -> bool:
        """True if any part of this event falls on `day`. A three-day trip is
        three days — the middle of it must not read as free."""
        start = self.starts_at
        last = self.last_day
        return start is not None and last is not None and start.date() <= day <= last

    def line(self) -> str:
        """One event, as she says it.

        An all-day event has no time worth printing: `isAllDay` was never read,
        so a blocked-out day came through as "- 00:00 Team offsite" and read as
        a midnight meeting.
        """
        when = "All day —" if self.all_day else f"{self.starts_at:%H:%M}"
        return f"- {when} {self.title}"


def _js(value: str) -> str:
    import json

    return json.dumps(value or "")


def names(*, caller=None) -> list[str]:
    return (caller or eventkit.run)(_NAMES)


def window(*, back: int = 0, days: int = 7, on: datetime | None = None,
           from_midnight: bool = False, caller=None) -> list[Event]:
    """Events between `back` days ago and `days` days ahead.

    `from_midnight` anchors the near end at the start of that day instead of at
    this moment — what "today" means to a person, and not what it meant to the
    predicate before.
    """
    now = on or datetime.now()
    start = now - timedelta(days=back)
    if from_midnight:
        start = datetime.combine(start.date(), time(0, 0))
    body = _WINDOW % {"start": _js(start.strftime("%Y-%m-%dT%H:%M")),
                      "end": _js((now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M"))}
    rows = (caller or eventkit.run)(body)
    events = [Event(calendar=r.get("calendar", ""), title=r.get("title", ""),
                    start=r.get("start", ""), end=r.get("end", ""),
                    location=r.get("location", ""), all_day=bool(r.get("all_day")))
              for r in rows if r.get("title", "").strip()]
    return sorted(events, key=lambda e: e.start)


def brief(*, on: datetime | None = None, caller=None) -> str:
    """Today, as a few lines a planner can actually use.

    From midnight, not from now: the window used to start at the moment it was
    asked, so a 10am `notron agenda` had already lost the 9am meeting and the
    morning routine under-reported the day it was there to describe. And a day
    is every event that *touches* it, not only the ones that begin on it —
    otherwise the middle of a week-long trip is a free Wednesday.
    """
    day = (on or datetime.now()).date()
    todays = [e for e in window(days=2, on=on, from_midnight=True, caller=caller)
              if e.covers(day)]
    if not todays:
        return "Nothing in the calendar today."
    return "\n".join(e.line() + (f" — {e.location}" if e.location else "")
                     for e in todays)


def week(*, on: datetime | None = None, caller=None, limit: int = MAX_IN_PROMPT) -> str:
    """The next seven days, grouped by day.

    Grouped by the days each event *occupies*, so a three-day trip appears on
    all three. It used to be listed once, on the day it began, which made the
    two days in the middle look free.
    """
    events = window(days=7, on=on, from_midnight=True, caller=caller)[:limit]
    dated = [e for e in events if e.starts_at and e.last_day]
    if not dated:
        return "Nothing in the calendar this week."

    # Walk the days the events actually span rather than counting forward from
    # today: what is grouped is whatever the read returned, so this cannot
    # quietly drop an event by disagreeing with the clock. MAX_DAYS is only a
    # stop against one absurd multi-year event turning into a thousand headings.
    first = min(e.starts_at.date() for e in dated)
    last = min(max(e.last_day for e in dated), first + timedelta(days=MAX_DAYS))
    lines, day = [], first
    while day <= last:
        on_day = [e for e in dated if e.covers(day)]
        if on_day:
            lines.append(f"**{human(datetime.combine(day, time(0, 0)), with_time=False)}**")
            lines.extend(e.line() for e in on_day)
        day += timedelta(days=1)
    return "\n".join(lines)


def create(title: str, *, start_iso: str, end_iso: str | None = None,
           calendar_name: str = "", notes: str = "", caller=None) -> str:
    """Add an event. Returns its identifier.

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

    body = _CREATE % {
        "title": _js(title), "notes": _js(notes), "calendar": _js(calendar_name),
        "start": _js(start.strftime("%Y-%m-%dT%H:%M")),
        "end": _js(end.strftime("%Y-%m-%dT%H:%M")),
    }
    out = (caller or eventkit.run)(body)
    if "error" in out:
        raise eventkit.EventKitError(eventkit.failure(out, "could not create the event"))
    return out["id"]
