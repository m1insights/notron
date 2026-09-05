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
MAX_WINDOW_DAYS = 31

_WINDOW = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeEvent) !== 3) throw new Error('calendar unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var cals = store.calendarsForEntityType($.EKEntityTypeEvent);
var start = input.start == null ? $.NSDate.dateWithTimeIntervalSinceNow(-86400 * input.back) : $.NSDate.dateWithTimeIntervalSince1970(input.start);
var end = input.end == null ? $.NSDate.dateWithTimeIntervalSinceNow(86400 * input.days) : $.NSDate.dateWithTimeIntervalSince1970(input.end);
var pred = store.predicateForEventsWithStartDateEndDateCalendars(start, end, cals);
var events = store.eventsMatchingPredicate(pred);
var f = $.NSDateFormatter.alloc.init;
f.dateFormat = 'yyyy-MM-dd\\'T\\'HH:mmXXX';
var rows = [];
for (var i = 0; i < events.count; i++) {
  var e = events.objectAtIndex(i);
  rows.push({
    id: ObjC.unwrap(e.eventIdentifier),
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
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeEvent) !== 3) throw new Error('calendar unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var cals = store.calendarsForEntityType($.EKEntityTypeEvent);
var names = [];
for (var i = 0; i < cals.count; i++) names.push(ObjC.unwrap(cals.objectAtIndex(i).title));
JSON.stringify(names);
"""

_CREATE = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeEvent) !== 3) throw new Error('calendar unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var e = $.EKEvent.eventWithEventStore(store);
e.title = input.title;
e.notes = input.notes;
e.startDate = $.NSDate.dateWithTimeIntervalSince1970(input.start_timestamp);
e.endDate = $.NSDate.dateWithTimeIntervalSince1970(input.end_timestamp);
var target = store.calendarWithIdentifier(input.target_id);
if (target.isNil() || !Boolean(target.allowsContentModifications)) throw new Error('selected calendar unavailable');
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
    id: str = ""

    @property
    def starts_at(self) -> datetime | None:
        return parse(self.start)


def names(*, caller=None) -> list[str]:
    return (caller or eventkit.run)(_NAMES)


def window(*, back: int = 0, days: int = 7, caller=None, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
    if start is None and end is None:
        bounded = 0 <= back <= MAX_WINDOW_DAYS and 0 < days <= MAX_WINDOW_DAYS and back + days <= MAX_WINDOW_DAYS
    else:
        bounded = bool(start and end and start.tzinfo and end.tzinfo and 0 < end.timestamp() - start.timestamp() <= MAX_WINDOW_DAYS * 86400)
    if not bounded:
        raise ValueError('Calendar reads must use a bounded window of at most 31 days')
    rows = (caller or eventkit.run)(_WINDOW, data={"back": int(back), "days": int(days), "start": start.timestamp() if start else None, "end": end.timestamp() if end else None})
    if not isinstance(rows, list):
        raise eventkit.EventKitError("Calendar unavailable; context incomplete")
    events = [Event(calendar=r.get("calendar", ""), title=r.get("title", "") or "Untitled event",
                    start=r.get("start", ""), end=r.get("end", ""),
                    location=r.get("location", ""), id=r.get("id", ""))
              for r in rows]
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
    all_events = window(days=7, caller=caller)
    events = all_events[:limit]
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
    if len(all_events) > limit:
        lines.append(f"Context truncated: {len(all_events) - limit} more events in this window.")
    return "\n".join(lines)


def create(title: str, *, start_iso: str, end_iso: str | None = None,
           calendar_name: str = "", notes: str = "", target_id: str | None = None, caller=None) -> str:
    """Add an event. Returns its identifier.

    There is no `update`, `move` or `delete` in this module, and there never will
    be. The user's standing instructions say nothing in their calendar gets moved
    without asking, and the strongest way to keep that promise is not to write the
    code that could break it.
    """
    from . import when as when_mod
    from .requests import local_timezone
    if not when_mod.has_time(start_iso) or not when_mod.has_time(end_iso):
        raise ValueError('Confirm an explicit start and end time; no duration is assumed.')
    start = when_mod.resolve_local(start_iso, local_timezone())
    end = when_mod.resolve_local(end_iso, local_timezone())
    if end.timestamp() <= start.timestamp():
        raise ValueError('The end must be after the start; confirm the duration.')
    if not target_id:
        hits = resolve_targets(calendar_name, caller=caller)
        if len(hits) != 1:
            raise ValueError('Which calendar? Give an existing, unambiguous calendar name.')
        target_id = hits[0]['id']
    out = (caller or eventkit.run)(_CREATE, data={
        "title": title, "notes": notes, "calendar": calendar_name, "target_id": target_id,
        "start": start.isoformat(timespec='minutes'), "end": end.isoformat(timespec='minutes'),
        "start_timestamp": start.timestamp(), "end_timestamp": end.timestamp(),
    })
    if "error" in out:
        raise eventkit.EventKitError(f"could not create the event: {out['error']}")
    return out["id"]


_FIND_OPERATION = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeEvent) !== 3) throw new Error('calendar unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var f = $.NSDateFormatter.alloc.init;
f.dateFormat = 'yyyy-MM-dd\\'T\\'HH:mmXXX';
var day = $.NSDate.dateWithTimeIntervalSince1970(input.start);
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
        'reference': reference(operation_id), 'start': start.timestamp()})

_TARGETS = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeEvent) !== 3) throw new Error('full access required');
var store = $.EKEventStore.alloc.init;
var cals = store.calendarsForEntityType($.EKEntityTypeEvent);
var def = store.defaultCalendarForNewEvents;
var rows = [];
for (var i = 0; i < cals.count; i++) {
  var c = cals.objectAtIndex(i);
  if (Boolean(c.allowsContentModifications)) rows.push({id: ObjC.unwrap(c.calendarIdentifier), title: ObjC.unwrap(c.title), default: !def.isNil() && ObjC.unwrap(def.calendarIdentifier) === ObjC.unwrap(c.calendarIdentifier)});
}
JSON.stringify(rows);
"""


def resolve_targets(name: str = "", *, target_id: str | None = None, caller=None) -> list[dict]:
    rows = (caller or eventkit.run)(_TARGETS)
    if not isinstance(rows, list):
        raise eventkit.EventKitError('Target inventory unavailable')
    return [row for row in rows if row.get('id') and
            (row.get('id') == target_id if target_id else True) and
            (row.get('title', '').casefold() == name.casefold() if name else (bool(target_id) or row.get('default') is True))]


def overlaps(start_iso: str, end_iso: str, *, caller=None) -> list[Event]:
    from .when import resolve_local
    from .requests import local_timezone
    start, end = (resolve_local(v, local_timezone()) for v in (start_iso, end_iso))
    if end.timestamp() <= start.timestamp():
        raise ValueError('Confirm an end after the start')
    events = window(start=start, end=end, caller=caller)
    found = []
    for event in events:
        if not event.id:
            raise eventkit.EventKitError('Calendar context incomplete; event identity unavailable')
        try:
            a, b = (resolve_local(v, local_timezone()) for v in (event.start, event.end))
        except ValueError:
            raise eventkit.EventKitError('Calendar context incomplete; event time unavailable') from None
        if a.timestamp() < end.timestamp() and b.timestamp() > start.timestamp():
            found.append(event)
    return found


def conflict_token(action, events: list[Event]) -> str:
    """Local deterministic confirmation, bound to proposal and current conflicts."""
    import hashlib, json
    value = [action.kind, action.op, action.title, action.when, action.ends, action.target_id, action.notes,
             sorted((e.id, e.start, e.end) for e in events)]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()[:16]
