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
from datetime import datetime

from . import eventkit
from .when import human, parse

MAX_IN_PROMPT = 30
DEFAULT_MINUTES = 60

_WINDOW = """
var store = $.EKEventStore.alloc.init;
var cals = store.calendarsForEntityType($.EKEntityTypeEvent);
var start = $.NSDate.dateWithTimeIntervalSinceNow(-86400 * input.back);
var end = $.NSDate.dateWithTimeIntervalSinceNow(86400 * input.days);
var pred = store.predicateForEventsWithStartDateEndDateCalendars(start, end, cals);
var events = store.eventsMatchingPredicate(pred);
var f = $.NSDateFormatter.alloc.init;
f.dateFormat = 'yyyy-MM-dd\\'T\\'HH:mm';
var rows = [];
for (var i = 0; i < events.count; i++) {
  var e = events.objectAtIndex(i);
  rows.push({
    calendar: ObjC.unwrap(e.calendar.title) || '',
    title: ObjC.unwrap(e.title) || '',
    start: ObjC.unwrap(f.stringFromDate(e.startDate)),
    end: e.endDate.isNil() ? '' : ObjC.unwrap(f.stringFromDate(e.endDate)),
    location: e.location.isNil() ? '' : ObjC.unwrap(e.location)
  });
}
return JSON.stringify(rows);
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
e.title = input.title;
e.notes = input.notes;
var f = $.NSDateFormatter.alloc.init;
f.dateFormat = 'yyyy-MM-dd\\'T\\'HH:mm';
e.startDate = f.dateFromString(input.start);
e.endDate = f.dateFromString(input.end);
var wanted = input.calendar;
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
return JSON.stringify(ok ? {id: ObjC.unwrap(e.eventIdentifier),
                     calendar: ObjC.unwrap(target.title)} : {error: 'save failed'});
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


def names(*, caller=None) -> list[str]:
    return (caller or eventkit.run)(_NAMES)


def window(*, back: int = 0, days: int = 7, caller=None) -> list[Event]:
    rows = (caller or eventkit.run)(_WINDOW, data={"back": int(back), "days": int(days)})
    events = [Event(calendar=r.get("calendar", ""), title=r.get("title", ""),
                    start=r.get("start", ""), end=r.get("end", ""),
                    location=r.get("location", ""))
              for r in rows if r.get("title", "").strip()]
    return sorted(events, key=lambda e: e.start)


def brief(*, on: datetime | None = None, caller=None) -> str:
    """Today, as a few lines a planner can actually use."""
    day = (on or datetime.now()).date()
    todays = [e for e in window(days=2, caller=caller)
              if e.starts_at and e.starts_at.date() == day]
    if not todays:
        return "Nothing in the calendar today."
    return "\n".join(
        f"- {e.starts_at:%H:%M} {e.title}" + (f" — {e.location}" if e.location else "")
        for e in todays
    )


def week(*, caller=None, limit: int = MAX_IN_PROMPT) -> str:
    """The next seven days, grouped by day."""
    events = window(days=7, caller=caller)[:limit]
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

    out = (caller or eventkit.run)(_CREATE, data={
        "title": title, "notes": notes, "calendar": calendar_name,
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": end.strftime("%Y-%m-%dT%H:%M"),
    })
    if "error" in out:
        raise eventkit.EventKitError(f"could not create the event: {out['error']}")
    return out["id"]


_FIND_OPERATION = """
var store = $.EKEventStore.alloc.init;
var f = $.NSDateFormatter.alloc.init;
f.dateFormat = 'yyyy-MM-dd\\'T\\'HH:mm';
var day = f.dateFromString(input.start);
var start = day.dateByAddingTimeInterval(-86400);
var end = day.dateByAddingTimeInterval(86400);
var pred = store.predicateForEventsWithStartDateEndDateCalendars(start, end, $());
var events = store.eventsMatchingPredicate(pred);
var ids = [];
for (var i = 0; i < events.count; i++) {
  var e = events.objectAtIndex(i);
  var text = e.notes.isNil() ? '' : ObjC.unwrap(e.notes);
  if (text.split('\\n').indexOf(input.reference) >= 0)
    ids.push(ObjC.unwrap(e.eventIdentifier));
}
return JSON.stringify(ids);
"""


def find_by_operation(operation_id: str, *, caller=None) -> list[str]:
    """Bound reconciliation to the captured event date. A moved event needs review."""
    from .recovery import get, reference
    value = get(operation_id)
    start = parse(value['action']['when']) if value else None
    if start is None:
        return []
    return (caller or eventkit.run)(_FIND_OPERATION, data={
        'reference': reference(operation_id), 'start': start.strftime('%Y-%m-%dT%H:%M')})
