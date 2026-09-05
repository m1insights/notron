"""The Reminders app — real checkboxes you tap, real notifications, real phone sync.

This exists because Apple Notes cannot be given a tappable checkbox by script. Notron
writes `☐` and `✅` as plain text into notes; Reminders is where a task can actually
buzz. It is the most demonstrable thing she does.

Everything here goes through `eventkit`, not AppleScript. Reading 23 open reminders
took 65.7 seconds through the Reminders app and 0.093 seconds through EventKit — the
same answer, from the same store, 700 times faster. See `eventkit.py` for why.

There is no `delete` in this module and there never will be. Notron may tick something
off; removing it is the user's to do.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import eventkit

MAX_IN_PROMPT = 25
FETCH_SECONDS = 15

_OPEN = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeReminder) !== 3) throw new Error('reminders unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var pred = store.predicateForIncompleteRemindersWithDueDateStartingEndingCalendars($(), $(), $());
var out = null;
store.fetchRemindersMatchingPredicateCompletion(pred, function (arr) {
  var rows = [];
  for (var i = 0; i < arr.count; i++) {
    var r = arr.objectAtIndex(i);
    var due = '';
    if (!r.dueDateComponents.isNil()) {
      var d = $.NSCalendar.currentCalendar.dateFromComponents(r.dueDateComponents);
      if (!d.isNil()) {
        var f = $.NSDateFormatter.alloc.init;
        f.dateFormat = 'yyyy-MM-dd\\'T\\'HH:mmXXX';
        due = ObjC.unwrap(f.stringFromDate(d));
      }
    }
    rows.push({
      id: ObjC.unwrap(r.calendarItemIdentifier),
      title: ObjC.unwrap(r.title) || '',
      list: ObjC.unwrap(r.calendar.title) || '',
      recurring: !r.recurrenceRules.isNil() && r.recurrenceRules.count > 0,
      due: due
    });
  }
  out = rows;
});
awaitDone(function () { return out !== null; }, %d);
if (out === null) throw new Error('reminders fetch timed out');
JSON.stringify(out);
""" % FETCH_SECONDS

_LISTS = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeReminder) !== 3) throw new Error('reminders unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var cals = store.calendarsForEntityType($.EKEntityTypeReminder);
var names = [];
for (var i = 0; i < cals.count; i++) names.push(ObjC.unwrap(cals.objectAtIndex(i).title));
JSON.stringify(names);
"""

_CREATE = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeReminder) !== 3) throw new Error('reminders unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var r = $.EKReminder.reminderWithEventStore(store);
r.title = input.title;
r.notes = input.notes;
var target = store.calendarWithIdentifier(input.target_id);
if (target.isNil() || !Boolean(target.allowsContentModifications)) throw new Error('selected list unavailable');
r.calendar = target;
var iso = input.when;
if (iso) {
  var d = $.NSDate.dateWithTimeIntervalSince1970(input.timestamp);
  var units = iso.length > 10
    ? ($.NSCalendarUnitYear | $.NSCalendarUnitMonth | $.NSCalendarUnitDay | $.NSCalendarUnitHour | $.NSCalendarUnitMinute)
    : ($.NSCalendarUnitYear | $.NSCalendarUnitMonth | $.NSCalendarUnitDay);
  var cal = $.NSCalendar.alloc.initWithCalendarIdentifier($.NSCalendarIdentifierGregorian);
  cal.timeZone = $.NSTimeZone.timeZoneForSecondsFromGMT(input.offset);
  r.dueDateComponents = cal.componentsFromDate(units, d);
  r.dueDateComponents.timeZone = cal.timeZone;
  if (iso.length > 10) r.addAlarm($.EKAlarm.alarmWithAbsoluteDate(d));
}
var err = Ref();
var ok = store.saveReminderCommitError(r, true, err);
return JSON.stringify(ok ? {id: ObjC.unwrap(r.calendarItemIdentifier)} : {error: 'save failed'});
"""

_COMPLETE = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeReminder) !== 3) throw new Error('reminders unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var item = store.calendarItemWithIdentifier(input.id);
if (item.isNil()) { return JSON.stringify({error: 'not found'}); }
else {
  if (!item.recurrenceRules.isNil() && item.recurrenceRules.count > 0) throw new Error('recurring reminders require manual completion');
  item.completed = true;
  var err = Ref();
  var ok = store.saveReminderCommitError(item, true, err);
  return JSON.stringify(ok ? {title: ObjC.unwrap(item.title)} : {error: 'save failed'});
}
"""


@dataclass(frozen=True)
class Reminder:
    id: str
    title: str
    list_name: str
    due: str          # ISO, or "" when undated
    recurring: bool = False


def lists(*, caller=None) -> list[str]:
    return (caller or eventkit.run)(_LISTS)


def open_items(*, caller=None) -> list[Reminder]:
    rows = (caller or eventkit.run)(_OPEN)
    if not isinstance(rows, list):
        raise eventkit.EventKitError("Reminders unavailable; context incomplete")
    return [Reminder(id=r.get("id", ""), title=r.get("title", ""),
                     list_name=r.get("list", ""), due=r.get("due", ""), recurring=bool(r.get("recurring")))
            for r in rows if r.get("title", "").strip()]


def summary(*, caller=None, limit: int = MAX_IN_PROMPT) -> str:
    """What is outstanding, short enough to sit in a prompt without crowding it."""
    items = open_items(caller=caller)
    if not items:
        return "Nothing outstanding in Reminders."
    lines = [f"- {r.title}" + (f" (due {r.due})" if r.due else "") + f" [{r.list_name}]"
             for r in items[:limit]]
    if len(items) > limit:
        lines.append(f"- …and {len(items) - limit} more")
    return "\n".join(lines)


def create(title: str, *, notes: str = "", list_name: str = "",
           when_iso: str | None = None, target_id: str | None = None, timezone_name: str | None = None, caller=None) -> str:
    """Make a reminder. Returns its id. Called only by the Executor.

    Timed reminders get an explicit alarm. Date-only reminders retain a due date
    without an explicit timed alarm; receipts explain that distinction.
    """
    from . import when
    from .requests import local_timezone
    if not target_id:
        hits = resolve_targets(list_name, caller=caller)
        if len(hits) != 1:
            raise ValueError('Which reminder list? Give an existing, unambiguous list name.')
        target_id = hits[0]['id']
    dt = when.resolve_local(when_iso, timezone_name or local_timezone()) if when_iso else None
    out = (caller or eventkit.run)(_CREATE, data={
        "title": title, "notes": notes, "list": list_name, "target_id": target_id,
        "when": when_iso or "", "timestamp": dt.timestamp() if dt else None,
        "offset": dt.utcoffset().total_seconds() if dt else None})
    if "error" in out:
        raise eventkit.EventKitError(f"could not create the reminder: {out['error']}")
    return out["id"]


def complete(reminder_id: str, *, caller=None) -> str:
    """Tick one off. There is deliberately no way to delete a reminder from here:
    the user's list is theirs, and 'done' must never quietly mean 'gone'."""
    out = (caller or eventkit.run)(_COMPLETE, data={"id": reminder_id})
    if "error" in out:
        raise LookupError(f"could not tick that off: {out['error']}")
    return out["title"]


def find_open(phrase: str, *, list_name: str = "", caller=None) -> list[Reminder]:
    """Return zero, one or many; titles never disambiguate duplicate records."""
    items = [r for r in open_items(caller=caller) if not list_name or r.list_name.casefold() == list_name.casefold()]
    needle = phrase.strip().casefold()
    if not needle:
        return []
    exact = [r for r in items if r.title.casefold() == needle]
    if exact:
        return exact
    hits = [r for r in items if needle in r.title.casefold()]
    if hits:
        return hits
    words = [w for w in needle.split() if len(w) > 2]
    return [r for r in items if words and all(w in r.title.casefold() for w in words)]


_FIND_OPERATION = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeReminder) !== 3) throw new Error('reminders unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var pred = store.predicateForRemindersInCalendars($());
var out = null;
store.fetchRemindersMatchingPredicateCompletion(pred, function(arr) {
  var ids = [];
  for (var i = 0; i < arr.count; i++) {
    var r = arr.objectAtIndex(i);
    var text = r.notes.isNil() ? '' : ObjC.unwrap(r.notes);
    if (text.split('\\n').indexOf(input.reference) >= 0)
      ids.push(ObjC.unwrap(r.calendarItemIdentifier));
  }
  out = ids;
});
awaitDone(function() { return out !== null; }, 15);
if (out === null) throw new Error('reconciliation unavailable');
return JSON.stringify(out);
"""

_COMPLETED = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeReminder) !== 3) throw new Error('reminders unavailable: full access required');
var store = $.EKEventStore.alloc.init;
var item = store.calendarItemWithIdentifier(input.id);
return JSON.stringify(!item.isNil() && Boolean(item.completed));
"""


def find_by_operation(operation_id: str, *, caller=None) -> list[str]:
    """Exact opaque reference, including reminders completed after creation."""
    from .recovery import reference
    return (caller or eventkit.run)(_FIND_OPERATION, data={'reference': reference(operation_id)})


def is_completed(reminder_id: str, *, caller=None) -> bool:
    return (caller or eventkit.run)(_COMPLETED, data={'id': reminder_id}) is True

_TARGETS = """
if ($.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeReminder) !== 3) throw new Error('full access required');
var store = $.EKEventStore.alloc.init;
var cals = store.calendarsForEntityType($.EKEntityTypeReminder);
var def = store.defaultCalendarForNewReminders;
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
