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
        f.dateFormat = 'yyyy-MM-dd\\'T\\'HH:mm';
        due = ObjC.unwrap(f.stringFromDate(d));
      }
    }
    rows.push({
      id: ObjC.unwrap(r.calendarItemIdentifier),
      title: ObjC.unwrap(r.title) || '',
      list: ObjC.unwrap(r.calendar.title) || '',
      due: due
    });
  }
  out = rows;
});
awaitDone(function () { return out !== null; }, %d);
JSON.stringify(out === null ? [] : out);
""" % FETCH_SECONDS

_LISTS = """
var store = $.EKEventStore.alloc.init;
var cals = store.calendarsForEntityType($.EKEntityTypeReminder);
var names = [];
for (var i = 0; i < cals.count; i++) names.push(ObjC.unwrap(cals.objectAtIndex(i).title));
JSON.stringify(names);
"""

_CREATE = """
var store = $.EKEventStore.alloc.init;
var r = $.EKReminder.reminderWithEventStore(store);
r.title = %(title)s;
r.notes = %(notes)s;
var listName = %(list)s;
var target = $();
var cals = store.calendarsForEntityType($.EKEntityTypeReminder);
for (var i = 0; i < cals.count; i++) {
  var c = cals.objectAtIndex(i);
  if (listName && ObjC.unwrap(c.title) === listName) { target = c; break; }
}
if (target.isNil()) target = store.defaultCalendarForNewReminders;
r.calendar = target;
var iso = %(when)s;
if (iso) {
  var f = $.NSDateFormatter.alloc.init;
  f.dateFormat = iso.length > 10 ? 'yyyy-MM-dd\\'T\\'HH:mm' : 'yyyy-MM-dd';
  var d = f.dateFromString(iso);
  var units = iso.length > 10
    ? ($.NSCalendarUnitYear | $.NSCalendarUnitMonth | $.NSCalendarUnitDay | $.NSCalendarUnitHour | $.NSCalendarUnitMinute)
    : ($.NSCalendarUnitYear | $.NSCalendarUnitMonth | $.NSCalendarUnitDay);
  r.dueDateComponents = $.NSCalendar.currentCalendar.componentsFromDate(units, d);
  if (iso.length > 10) r.addAlarm($.EKAlarm.alarmWithAbsoluteDate(d));
}
var err = Ref();
var ok = store.saveReminderCommitError(r, true, err);
JSON.stringify(ok ? {id: ObjC.unwrap(r.calendarItemIdentifier)}
                  : {error: 'save failed', why: reason(err)});
"""

_COMPLETE = """
var store = $.EKEventStore.alloc.init;
var item = store.calendarItemWithIdentifier(%(id)s);
if (item.isNil()) { JSON.stringify({error: 'not found'}); }
else {
  item.completed = true;
  var err = Ref();
  var ok = store.saveReminderCommitError(item, true, err);
  JSON.stringify(ok ? {title: ObjC.unwrap(item.title)}
                    : {error: 'save failed', why: reason(err)});
}
"""


@dataclass(frozen=True)
class Reminder:
    id: str
    title: str
    list_name: str
    due: str          # ISO, or "" when undated


def _js(value: str) -> str:
    """A Python string as a JavaScript literal. Everything user- or model-supplied
    goes through here — a title with a quote in it must not be able to change what
    the script does."""
    import json

    return json.dumps(value or "")


def lists(*, caller=None) -> list[str]:
    return (caller or eventkit.run)(_LISTS)


def open_items(*, caller=None) -> list[Reminder]:
    rows = (caller or eventkit.run)(_OPEN)
    return [Reminder(id=r.get("id", ""), title=r.get("title", ""),
                     list_name=r.get("list", ""), due=r.get("due", ""))
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
           when_iso: str | None = None, caller=None) -> str:
    """Make a reminder. Returns its id. Called only by the Executor.

    A dated reminder also gets an alarm — a due date alone shows in the app but does
    not notify, and a reminder that does not buzz is just a note with a circle.
    """
    body = _CREATE % {"title": _js(title), "notes": _js(notes),
                      "list": _js(list_name), "when": _js(when_iso or "")}
    out = (caller or eventkit.run)(body)
    if "error" in out:
        raise eventkit.EventKitError(eventkit.failure(out, "could not create the reminder"))
    return out["id"]


def complete(reminder_id: str, *, caller=None) -> str:
    """Tick one off. There is deliberately no way to delete a reminder from here:
    the user's list is theirs, and 'done' must never quietly mean 'gone'."""
    out = (caller or eventkit.run)(_COMPLETE % {"id": _js(reminder_id)})
    if "error" in out:
        raise LookupError(eventkit.failure(out, "could not tick that off"))
    return out["title"]


def find_open(phrase: str, *, caller=None) -> Reminder | None:
    """The open reminder the user most likely means. Exact match, then substring,
    then the shortest title containing every word they said."""
    items = open_items(caller=caller)
    needle = phrase.strip().lower()
    if not needle:
        return None
    for r in items:
        if r.title.lower() == needle:
            return r
    for r in items:
        if needle in r.title.lower():
            return r
    words = [w for w in needle.split() if len(w) > 2]
    hits = [r for r in items if words and all(w in r.title.lower() for w in words)]
    return min(hits, key=lambda r: len(r.title)) if hits else None
