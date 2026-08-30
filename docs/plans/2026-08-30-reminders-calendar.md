# Reminders and Calendar — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Juno can set a real Reminder that buzzes on the phone, tick one off, read
the real calendar so `☀️ Today` is built around actual commitments, and create an
event — never moving or deleting anything the user already had.

**Architecture:** Both apps are reached through **EventKit**, Apple's own calendar
and reminder database, driven from JavaScript for Automation under `osascript`.
Not AppleScript — see the measurements below, which killed the original design.
Three new graph nodes — `agenda` (no model, reads the day), `scheduler` (Nano, turns
English into a structured Action), `doer` (no model, Guard inside, applies it). The
model still only ever proposes; plain code still judges and applies.

**Tech Stack:** Python 3.12, EventKit via `osascript -l JavaScript`, Nebius Token
Factory (Nemotron Nano for extraction), pytest. No new dependencies.

**Supersedes:** `docs/plans/reminders-calendar.md` (the sketch this was built from).

---

## The measurements that rewrote this plan

The sketch assumed Reminders and Calendar would behave like Notes: bulk AppleScript
queries, fast enough to poll. **They do not.** Measured on this Mac, 2026-08-30,
against a real library of 1,263 reminders and 1,757 calendar events:

| Operation | AppleScript | EventKit | Verdict |
|---|---|---|---|
| List reminder lists | 0.14s | — | fine either way |
| **Read 23 open reminders** (name only) | **20.1s** | — | unusable |
| **Read 23 open reminders** (name + id + due) | **65.7s** | **0.093s** | **700× faster** |
| Read 7-day calendar window, all calendars | **26.0s** | **0.025s** | **1000× faster** |
| Same window, one 347-event calendar | 7.7s | — | still unusable |
| Same window, one 1,256-event calendar | 24.1s | — | still unusable |

Three separate things are wrong with the AppleScript route, and none of them can be
optimised away:

1. **`whose` filters in these two apps are not real queries.** They walk every
   object. Notes is fast because `name of every note of f` is a properly
   implemented bulk accessor; Reminders and Calendar have no equivalent. Cost
   scales with the size of the user's whole history, not with the answer.
2. **Bulk property fetch is broken in Reminders.** The `notes.py` trick —
   `set rs to (every reminder whose completed is false)` then `name of rs` — raises
   `Can't get name of {reminder id "x-apple-reminder://…"}`. Reminders returns
   unresolvable specifiers from a filtered set. Asking for an empty list's name
   fails too: `Can't get name of {}`.
3. **It wedges the app that Juno's listener depends on.** Every AppleScript request
   goes through the shared lock, so a 26-second calendar read blocks the Notes
   listener for 26 seconds.

EventKit has none of these problems. It reads Apple's own store directly, never
launches or blocks the Reminders and Calendar apps, and **does not need the
AppleScript lock at all** — which is what makes reading the agenda cheap enough to
do on every scheduling request.

### Why JXA and not a compiled Swift helper

A Swift binary was built and timed first. It works, but macOS grants EventKit access
**per binary**, and an unsigned binary's identity changes every time it is rebuilt —
so every edit to Juno would re-prompt the user, and a background listener can never
answer a prompt. Running the same EventKit calls through `osascript -l JavaScript`
inherits the terminal's existing, stable identity. Same API, same speed, no
re-prompting. (Measured: the Swift probe hung indefinitely on its first run, waiting
for a permission dialog. The JXA version answered in 0.09s.)

---

## Permissions: the state right now

EventKit access is a different permission from the Automation approval Notes uses,
and it has four states. Checked 2026-08-30:

| | Status | Meaning |
|---|---|---|
| **Reminders** | `3` — full access | **ready, nothing to do** |
| **Calendar** | `4` — write only | **can create events, cannot read them** |

Write-only is the dangerous one, because it does not fail. It silently reports
1 calendar and 0 events — a calendar that looks empty rather than one that looks
blocked. That is why `permissions.py` reads the numeric status instead of just
trying a query and seeing if it works.

**The one thing only you can do:** System Settings → Privacy & Security → Calendars
→ turn on full access for your terminal. Then `juno permissions` shows three ✓.

---

## Design decisions worth knowing before you start

1. **EventKit reads do not take the AppleScript lock.** `applescript.run` exists to
   stop two callers wedging the single-threaded Notes app. EventKit never touches
   that app, so `eventkit.run` is a separate, lock-free path. Do not route it
   through `applescript.run` "for consistency" — that would reintroduce exactly the
   26-second stall this design exists to avoid.

2. **Every JXA script returns JSON.** One `JSON.stringify` at the end, one
   `json.loads` in Python. No delimiter parsing, no `missing value` coercion
   problems, no `RS`/`US` separators. This is a large part of why the EventKit path
   is simpler as well as faster.

3. **EventKit's async reads need a run loop.** `fetchReminders` calls back rather
   than returning. JXA has no `await`, so the script pumps
   `NSRunLoop.runModeBeforeDate` until the callback fires or a deadline passes.
   Verified working. Do not "simplify" this away — without it the script exits
   before the data arrives and silently returns nothing.

4. **The model resolves "Thursday"; plain code checks its work.** Models get dates
   wrong constantly. The Guard verifies the resolved date parses, is not in the
   past, is within a year — and, if the request named a weekday, that the resolved
   date actually falls on it. Every confirmation echoes the full resolved date.

5. **Why three nodes and not "the executor gains two methods."** Because the reply
   must be true. If the writer composed "Reminder set" before the Guard ruled, she
   could claim something that was blocked. Actions are applied *before* the writer
   runs: `scheduler` proposes, `doer` applies and records the real outcome, `writer`
   reports what actually happened. Two of the three run no model.

   ```
   watcher → router → retriever → researcher → agenda → planner → scheduler → doer → writer → executor
   ```

6. **`juno/calendar.py` shadows the stdlib `calendar`.** Harmless — Python 3 uses
   absolute imports, so `from . import calendar` is explicit and a bare
   `import calendar` still gets the stdlib. Just never write a bare one inside `juno/`.

7. **The one thing still unmeasured: writing.** Reads are proven. Creating a
   reminder, completing one, and creating an event have not been run against the
   real store, because that writes to the user's actual data. Task 3 and Task 5 each
   end with a verification step that does exactly that, once, deliberately.

---

## Task 0: Know exactly which permission is missing

**Files:**
- Create: `juno/permissions.py`
- Modify: `juno/cli.py` (new `permissions` command, called from `setup`)
- Test: `tests/test_permissions.py`

**Step 1: Human action — grant Calendar full access (1 minute, only you can do this)**

Reminders is already granted. Calendar is on **write only**, which reports an empty
calendar rather than a blocked one. Fix it:

System Settings → Privacy & Security → **Calendars** → turn on full access for your
terminal. Verify:

```bash
osascript -l JavaScript -e 'ObjC.import("EventKit"); "events=" + $.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeEvent) + " reminders=" + $.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeReminder)'
```
Expected: `events=3 reminders=3`. (`4` is write-only, `2` is denied, `0` is not asked yet.)

**Step 2: Write the failing test**

```python
# tests/test_permissions.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import permissions


def test_write_only_calendar_access_is_reported_as_a_problem():
    """The state that cost the most time to diagnose. Write-only access does not
    fail — it reports one calendar and zero events, so the calendar looks empty
    instead of blocked. Measured 2026-08-30: events=4, reminders=3."""
    checks = permissions.check(reader=lambda: {"events": 4, "reminders": 3})
    cal = next(c for c in checks if c.app == "Calendar")
    assert not cal.ok
    assert "write" in cal.detail.lower()
    assert "Calendars" in cal.fix


def test_full_access_to_both_is_reported_ready():
    checks = permissions.check(reader=lambda: {"events": 3, "reminders": 3})
    assert all(c.ok for c in checks if c.app in ("Calendar", "Reminders"))


def test_never_asked_and_denied_are_told_apart():
    checks = {c.app: c for c in permissions.check(reader=lambda: {"events": 0, "reminders": 2})}
    assert "not been asked" in checks["Calendar"].detail
    assert "denied" in checks["Reminders"].detail
```

**Step 3: Run it and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_permissions.py -v
```
Expected: `ModuleNotFoundError: No module named 'juno.permissions'`

**Step 4: Implement**

```python
# juno/permissions.py
"""Which of your apps Juno is actually allowed to read.

Three separate permissions, and each fails in its own quiet way:

  * **Notes** uses AppleScript Automation. An unapproved app does not refuse —
    it *hangs*, waiting on a dialog a background job can never answer.
  * **Calendar and Reminders** use EventKit, which has four states, and the
    dangerous one is `write only`. Write-only access does not raise anything. It
    reports one calendar and zero events, so a blocked calendar is indistinguishable
    from a free week. Measured on this Mac 2026-08-30: Calendar was write-only and
    looked empty all day.

So this module reads the numeric authorization status rather than trying a query
and believing the answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import eventkit
from .applescript import run

PROBE_TIMEOUT = 8

# EKAuthorizationStatus
NOT_DETERMINED, RESTRICTED, DENIED, FULL, WRITE_ONLY = 0, 1, 2, 3, 4

_STATUS = """
ObjC.import('EventKit');
JSON.stringify({
  events: $.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeEvent),
  reminders: $.EKEventStore.authorizationStatusForEntityType($.EKEntityTypeReminder)
});
"""

_NOTES = 'on run argv\n tell application "Notes" to return (count of every folder) as text\nend run'


@dataclass(frozen=True)
class Check:
    app: str
    ok: bool
    detail: str
    fix: str


PANE = {"Calendar": "Calendars", "Reminders": "Reminders"}

EXPLAIN = {
    NOT_DETERMINED: ("has not been asked yet", True),
    RESTRICTED: ("is restricted by a profile on this Mac", True),
    DENIED: ("is denied", True),
    FULL: ("full access", False),
    WRITE_ONLY: ("is write only — Juno can add things but cannot read them, "
                 "so your calendar will look empty", True),
}


def _read() -> dict:
    return eventkit.run(_STATUS)


def check(reader=None, notes_runner=None) -> list[Check]:
    out: list[Check] = []

    notes_runner = notes_runner or (lambda: run(_NOTES, timeout=PROBE_TIMEOUT, retries=0))
    try:
        answer = notes_runner()
        out.append(Check("Notes", True, f"ready ({answer.strip()} folders)", ""))
    except Exception as e:
        out.append(Check("Notes", False, f"no answer ({type(e).__name__})",
                         "System Settings → Privacy & Security → Automation, "
                         "switch on Notes for your terminal."))

    try:
        status = (reader or _read)()
    except Exception as e:
        for app in ("Calendar", "Reminders"):
            out.append(Check(app, False, f"could not be checked ({type(e).__name__})", ""))
        return out

    for app, key in (("Calendar", "events"), ("Reminders", "reminders")):
        code = int(status.get(key, NOT_DETERMINED))
        detail, broken = EXPLAIN.get(code, (f"unknown status {code}", True))
        fix = (f"System Settings → Privacy & Security → {PANE[app]}, "
               f"give your terminal full access.") if broken else ""
        out.append(Check(app, not broken, detail, fix))
    return out
```

**Step 5: Wire it into the CLI**

```python
def cmd_permissions(args):
    from . import permissions

    print()
    for c in permissions.check():
        print(f"  {'✓' if c.ok else '✗'} {c.app:10} {c.detail}")
        if c.fix:
            print(f"    → {c.fix}")
    print()
```

```python
sub.add_parser("permissions", help="check Juno can reach Notes, Reminders and Calendar"
               ).set_defaults(fn=cmd_permissions)
```

Call it at the end of `cmd_setup` so a first-time user is never left with a calendar
that silently looks empty.

**Step 6: Verify**

```bash
.venv/bin/python -m pytest tests/test_permissions.py -v   # PASS
.venv/bin/python -m juno permissions                      # three ✓ lines
```

**Step 7: Commit**

```bash
git add juno/permissions.py juno/cli.py tests/test_permissions.py
git commit -m "feat: read EventKit's real permission state — write-only access looks like an empty calendar"
```

---

## Task 1: `juno/when.py` — dates that survive the round trip

**Files:**
- Create: `juno/when.py`
- Test: `tests/test_when.py`

**Step 1: Write the failing tests**

```python
# tests/test_when.py
import sys, pathlib
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import when


def test_an_iso_string_from_the_model_becomes_a_real_datetime():
    assert when.parse("2026-09-03T09:00") == datetime(2026, 9, 3, 9, 0)
    assert when.parse("2026-09-03") == datetime(2026, 9, 3, 0, 0)


def test_nonsense_from_the_model_is_none_not_an_exception():
    """A bad date must never stop the graph — it must be refusable."""
    assert when.parse("next Thursday") is None
    assert when.parse("") is None
    assert when.parse(None) is None


def test_a_date_is_passed_to_applescript_as_numbers_never_as_a_string():
    """date "3/9/2026" means two different days depending on the Mac's region."""
    assert when.components(datetime(2026, 9, 3, 9, 5)) == ("2026", "9", "3", "9", "5")


def test_a_confirmation_says_the_weekday_out_loud_so_a_wrong_date_is_obvious():
    assert when.human(datetime(2026, 9, 3, 9, 0)) == "Thursday 3 September at 09:00"
    assert when.human(datetime(2026, 9, 3)) == "Thursday 3 September"


def test_a_weekday_named_in_the_request_can_be_checked_against_the_answer():
    assert when.weekday_named("remind me to call the pharmacy thursday") == 3
    assert when.weekday_named("remind me tomorrow") is None
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_when.py -v
```
Expected: `ModuleNotFoundError: No module named 'juno.when'`

**Step 3: Implement**

```python
# juno/when.py
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
```

**Step 4: Verify**

```bash
.venv/bin/python -m pytest tests/test_when.py -v
```
Expected: 5 passed.

**Step 5: Commit**

```bash
git add juno/when.py tests/test_when.py
git commit -m "feat: when.py — dates that survive model, Python and AppleScript"
```

---

## Task 2: `juno/eventkit.py` — the one door to Apple's calendar store

**Files:**
- Create: `juno/eventkit.py`
- Test: `tests/test_eventkit.py`

**Step 1: Write the failing tests**

```python
# tests/test_eventkit.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from juno import eventkit


def test_a_script_result_comes_back_as_parsed_json():
    out = eventkit.run("x", runner=lambda script, timeout: '{"count": 3}')
    assert out == {"count": 3}


def test_an_empty_answer_is_an_error_not_a_silent_none():
    """EventKit returning nothing means the run loop exited before the callback
    fired. Silently treating that as 'no reminders' would be a lie."""
    with pytest.raises(eventkit.EventKitError):
        eventkit.run("x", runner=lambda script, timeout: "")


def test_a_script_error_is_reported_with_what_the_script_said():
    def boom(script, timeout):
        raise RuntimeError("execution error: Can't get x")

    with pytest.raises(eventkit.EventKitError) as e:
        eventkit.run("x", runner=boom)
    assert "Can't get x" in str(e.value)


def test_every_async_read_pumps_a_run_loop_or_it_returns_nothing():
    """JXA has no await. Without the run loop the script exits before EventKit
    calls back, and the read comes home empty every time."""
    assert "runModeBeforeDate" in eventkit.AWAIT
    assert "NSDefaultRunLoopMode" in eventkit.AWAIT
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_eventkit.py -v
```
Expected: `ModuleNotFoundError: No module named 'juno.eventkit'`

**Step 3: Implement**

```python
# juno/eventkit.py
"""Apple's own calendar and reminder store, read directly.

This module exists because the obvious route does not work. Driving the Reminders
and Calendar apps with AppleScript — the way `notes.py` drives Notes — was measured
on a real library of 1,263 reminders and 1,757 events:

    read 23 open reminders (name + id + due)   AppleScript 65.7s   EventKit 0.093s
    read a 7-day calendar window               AppleScript 26.0s   EventKit 0.025s

The gap is not tuning. `whose` filters in those two apps walk every object that has
ever existed in them, and Reminders cannot even return properties from a filtered
set — `name of rs` raises `Can't get name of {reminder id "x-apple-reminder://…"}`.
EventKit queries the store those apps are drawing from, so cost scales with the
answer instead of the history.

Two consequences worth holding on to:

  * **These calls never launch or block the Reminders and Calendar apps.** They also
    do not take the AppleScript lock, because there is no single-threaded app to
    protect. That is what makes reading the agenda cheap enough to do on every
    scheduling request instead of once a morning.
  * **EventKit's reads are asynchronous and JXA has no `await`.** A script that just
    calls `fetchReminders` and returns exits before the callback fires and comes home
    empty — every time, with no error. Every async read pumps a run loop; see AWAIT.

A compiled Swift helper was tried first and abandoned: macOS grants EventKit access
per binary, and an unsigned binary's identity changes on every rebuild, so every edit
to Juno would re-prompt for permission — which a background listener can never
answer. Running under `osascript` inherits the terminal's stable identity instead.
"""

from __future__ import annotations

import json
import subprocess

DEFAULT_TIMEOUT = 30

#: Pump the run loop until `done` is set or the deadline passes. EventKit's reads
#: call back rather than returning, and JXA cannot await.
AWAIT = """
function awaitDone(check, seconds) {
  var deadline = $.NSDate.dateWithTimeIntervalSinceNow(seconds);
  while (!check() && $.NSDate.date.compare(deadline) < 0) {
    $.NSRunLoop.currentRunLoop.runModeBeforeDate(
      $.NSDefaultRunLoopMode, $.NSDate.dateWithTimeIntervalSinceNow(0.02));
  }
  return check();
}
"""

PRELUDE = "ObjC.import('EventKit');\nObjC.import('Foundation');\n" + AWAIT


class EventKitError(RuntimeError):
    pass


def _osascript(script: str, timeout: int) -> str:
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", "-"],
        input=script, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "osascript failed")
    return proc.stdout.strip()


def run(body: str, *, timeout: int = DEFAULT_TIMEOUT, runner=None) -> dict | list:
    """Run one JXA snippet whose final expression is a JSON string."""
    caller = runner or _osascript
    try:
        raw = caller(PRELUDE + body, timeout)
    except subprocess.TimeoutExpired as e:
        raise EventKitError(f"EventKit did not answer within {timeout}s") from e
    except Exception as e:
        raise EventKitError(str(e)) from e

    if not raw:
        raise EventKitError(
            "EventKit returned nothing — the run loop exited before the callback fired")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise EventKitError(f"EventKit returned unparseable output: {raw[:200]!r}") from e
```

**Step 4: Verify**

```bash
.venv/bin/python -m pytest tests/test_eventkit.py -v
```
Expected: 4 passed.

**Step 5: Commit**

```bash
git add juno/eventkit.py tests/test_eventkit.py
git commit -m "feat: eventkit.py — read Apple's store directly, 700x faster than driving the apps"
```

---

## Task 3: `juno/reminders.py` — real checkboxes that buzz

**Files:**
- Create: `juno/reminders.py`
- Test: `tests/test_reminders.py`

**Step 1: Write the failing tests**

```python
# tests/test_reminders.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import reminders

SAMPLE = [
    {"id": "x-1", "title": "Buy milk", "list": "Shopping", "due": "2026-09-03T09:00"},
    {"id": "x-2", "title": "Call the pharmacy", "list": "GENERAL TO DO LIST", "due": ""},
]


def _fake(payload):
    return lambda body, **kw: payload


def test_open_reminders_come_back_with_list_name_and_due_date():
    items = reminders.open_items(caller=_fake(SAMPLE))
    assert [r.title for r in items] == ["Buy milk", "Call the pharmacy"]
    assert items[0].list_name == "Shopping"
    assert items[1].due == ""


def test_no_reminders_at_all_is_an_empty_list_not_a_crash():
    assert reminders.open_items(caller=_fake([])) == []


def test_the_summary_juno_reads_is_short_enough_for_a_prompt():
    many = [{"id": f"x-{i}", "title": f"Thing {i}", "list": "Inbox", "due": ""}
            for i in range(60)]
    text = reminders.summary(caller=_fake(many))
    assert "Thing 0" in text
    assert len(text) < 2000
    assert "and 35 more" in text


def test_a_free_list_says_so_rather_than_going_blank():
    """A blank section reads as 'the read failed', not 'you're all clear'."""
    assert "Nothing" in reminders.summary(caller=_fake([]))


def test_creating_a_reminder_sends_an_iso_date_the_script_can_use():
    seen = {}

    def spy(body, **kw):
        seen["body"] = body
        return {"id": "x-new"}

    rid = reminders.create("Call the pharmacy", when_iso="2026-09-03T09:00", caller=spy)
    assert rid == "x-new"
    assert "2026-09-03T09:00" in seen["body"]


def test_completing_a_reminder_never_deletes_it():
    """Completing is not deleting. There is no delete path in this module at all."""
    assert not hasattr(reminders, "delete")
    assert "remove" not in reminders._COMPLETE.lower()


def test_a_reminder_can_be_found_by_what_the_user_actually_called_it():
    payload = [
        {"id": "x-1", "title": "Call the pharmacy about the repeat", "list": "Inbox", "due": ""},
        {"id": "x-2", "title": "Buy milk", "list": "Inbox", "due": ""},
    ]
    assert reminders.find_open("call the pharmacy", caller=_fake(payload)).id == "x-1"
    assert reminders.find_open("book a flight", caller=_fake(payload)) is None
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_reminders.py -v
```
Expected: `ModuleNotFoundError: No module named 'juno.reminders'`

**Step 3: Implement**

```python
# juno/reminders.py
"""The Reminders app — real checkboxes you tap, real notifications, real phone sync.

This exists because Apple Notes cannot be given a tappable checkbox by script. Juno
writes `☐` and `✅` as plain text into notes; Reminders is where a task can actually
buzz. It is the most demonstrable thing she does.

Everything here goes through `eventkit`, not AppleScript. Reading 23 open reminders
took 65.7 seconds through the Reminders app and 0.093 seconds through EventKit — the
same answer, from the same store, 700 times faster. See `eventkit.py` for why.

There is no `delete` in this module and there never will be. Juno may tick something
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
JSON.stringify(ok ? {id: ObjC.unwrap(r.calendarItemIdentifier)} : {error: 'save failed'});
"""

_COMPLETE = """
var store = $.EKEventStore.alloc.init;
var item = store.calendarItemWithIdentifier(%(id)s);
if (item.isNil()) { JSON.stringify({error: 'not found'}); }
else {
  item.completed = true;
  var err = Ref();
  var ok = store.saveReminderCommitError(item, true, err);
  JSON.stringify(ok ? {title: ObjC.unwrap(item.title)} : {error: 'save failed'});
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
        raise eventkit.EventKitError(f"could not create the reminder: {out['error']}")
    return out["id"]


def complete(reminder_id: str, *, caller=None) -> str:
    """Tick one off. There is deliberately no way to delete a reminder from here:
    the user's list is theirs, and 'done' must never quietly mean 'gone'."""
    out = (caller or eventkit.run)(_COMPLETE % {"id": _js(reminder_id)})
    if "error" in out:
        raise LookupError(f"could not tick that off: {out['error']}")
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
```

**Step 4: Verify**

```bash
.venv/bin/python -m pytest tests/test_reminders.py -v
```
Expected: 7 passed.

**Step 5: Prove the read against the real store**

```bash
.venv/bin/python -c "
from juno import reminders; import time
t=time.time(); print(reminders.lists())
print(reminders.summary()); print(f'{time.time()-t:.3f}s')"
```
Expected: under half a second. (Measured 0.093s for 23 open reminders.)

**Step 6: Prove the write — the first thing here that changes real data**

This creates one reminder in your real Reminders app and then ticks it off.
Run it deliberately, watch it appear on your phone, and keep the numbers:

```bash
.venv/bin/python -c "
from juno import reminders
from datetime import datetime, timedelta
soon = (datetime.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
rid = reminders.create('Juno test — safe to delete', when_iso=soon)
print('created', rid)
hit = reminders.find_open('Juno test')
print('found', hit)
print('completed:', reminders.complete(hit.id))"
```

Expected: it appears in Reminders (and on the phone), then shows as completed —
**not deleted**. Check the phone before ticking it off; that is the demo.

If `saveReminderCommitError` fails, the likely cause is a `Ref()` shape mismatch in
JXA. Fall back to `store.saveReminder(r, true, $())`.

**Step 7: Commit**

```bash
git add juno/reminders.py tests/test_reminders.py
git commit -m "feat: reminders that actually buzz — create and complete, never delete"
```

---

## Task 4: `juno/calendar.py` — the day she plans around

**Files:**
- Create: `juno/calendar.py`
- Test: `tests/test_calendar.py`

**Step 1: Write the failing tests**

```python
# tests/test_calendar.py
import sys, pathlib
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import calendar as cal

SAMPLE = [
    {"calendar": "Work", "title": "Standup", "start": "2026-09-03T09:30",
     "end": "2026-09-03T09:45", "location": ""},
    {"calendar": "Home", "title": "Dentist", "start": "2026-09-03T14:00",
     "end": "2026-09-03T15:00", "location": "Baker St"},
    {"calendar": "Work", "title": "Next week thing", "start": "2026-09-10T09:30",
     "end": "2026-09-10T10:00", "location": ""},
]


def _fake(payload):
    return lambda body, **kw: payload


def test_events_come_back_with_calendar_start_and_location():
    events = cal.window(days=7, caller=_fake(SAMPLE))
    assert [e.title for e in events] == ["Standup", "Dentist", "Next week thing"]
    assert events[1].location == "Baker St"


def test_an_empty_calendar_is_an_empty_list():
    assert cal.window(caller=_fake([])) == []


def test_todays_brief_only_shows_today():
    text = cal.brief(on=datetime(2026, 9, 3), caller=_fake(SAMPLE))
    assert "Standup" in text and "Dentist" in text
    assert "Next week thing" not in text


def test_a_free_day_says_so_rather_than_going_blank():
    """A blank calendar section reads as 'the read failed', not 'you are free'.
    This mattered for real: write-only access reported an empty calendar all day."""
    assert "Nothing" in cal.brief(on=datetime(2026, 9, 3), caller=_fake([]))


def test_the_week_is_grouped_by_day_so_it_reads_on_a_phone():
    text = cal.week(caller=_fake(SAMPLE))
    assert "Thursday 3 September" in text
    assert "- 09:30 Standup" in text


def test_a_read_is_always_a_bounded_window():
    """Unbounded reads are what made the AppleScript version unusable. The habit
    is worth keeping even though EventKit is fast."""
    assert "predicateForEventsWithStartDateEndDateCalendars" in cal._WINDOW


def test_there_is_no_way_to_move_or_delete_an_event_from_this_module():
    """The user's standing instruction is 'never move anything already in my
    calendar without asking'. The safest enforcement is having no such code."""
    assert not hasattr(cal, "delete")
    assert not hasattr(cal, "move")
    assert not hasattr(cal, "update")
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_calendar.py -v
```
Expected: `ModuleNotFoundError: No module named 'juno.calendar'`

**Step 3: Implement**

```python
# juno/calendar.py
"""The Calendar app — so the day Juno plans is the day you actually have.

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
the `juno` package. Relative imports (`from . import calendar`) are unambiguous; a
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
var start = $.NSDate.dateWithTimeIntervalSinceNow(-86400 * %(back)d);
var end = $.NSDate.dateWithTimeIntervalSinceNow(86400 * %(days)d);
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
var f = $.NSDateFormatter.alloc.init;
f.dateFormat = 'yyyy-MM-dd\\'T\\'HH:mm';
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


def _js(value: str) -> str:
    import json

    return json.dumps(value or "")


def names(*, caller=None) -> list[str]:
    return (caller or eventkit.run)(_NAMES)


def window(*, back: int = 0, days: int = 7, caller=None) -> list[Event]:
    body = _WINDOW % {"back": back, "days": days}
    rows = (caller or eventkit.run)(body)
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
```

**Step 4: Verify**

```bash
.venv/bin/python -m pytest tests/test_calendar.py -v
```
Expected: 7 passed.

**Step 5: Prove the read against the real calendar**

Only works once Task 0 has moved Calendar off write-only:

```bash
.venv/bin/python -c "
from juno import calendar as c; import time
print(c.names())
t=time.time(); print(f'{len(c.window(days=7))} events in 7 days, {time.time()-t:.3f}s')
print(c.week())"
```

**If `names()` returns one calendar and the week is empty, Calendar is still on
write-only.** That is the failure this whole plan learned to recognise — go back to
Task 0 rather than assuming a quiet week.

**Step 6: Commit**

```bash
git add juno/calendar.py tests/test_calendar.py
git commit -m "feat: read the real calendar in 25ms, grouped by day"
```

---

## Task 5: Creating an event, and only creating

**Files:**
- Modify: `juno/calendar.py` (add `create`)
- Test: `tests/test_calendar.py` (extend)

**Step 1: Write the failing tests**

```python
def test_creating_an_event_sends_both_ends():
    seen = {}

    def spy(body, **kw):
        seen["body"] = body
        return {"id": "uid-1", "calendar": "Home"}

    uid = cal.create("Dentist", start_iso="2026-09-03T14:00",
                     end_iso="2026-09-03T15:00", caller=spy)
    assert uid == "uid-1"
    assert "2026-09-03T14:00" in seen["body"]
    assert "2026-09-03T15:00" in seen["body"]


def test_an_event_with_no_end_time_gets_a_sensible_hour():
    seen = {}
    cal.create("Coffee", start_iso="2026-09-03T14:00",
               caller=lambda body, **kw: (seen.setdefault("b", body), {"id": "u", "calendar": "x"})[1])
    assert "2026-09-03T15:00" in seen["b"]


def test_an_end_before_the_start_is_corrected_not_saved():
    seen = {}
    cal.create("Backwards", start_iso="2026-09-03T14:00", end_iso="2026-09-03T13:00",
               caller=lambda body, **kw: (seen.setdefault("b", body), {"id": "u", "calendar": "x"})[1])
    assert "2026-09-03T15:00" in seen["b"]


def test_an_unusable_start_is_refused_before_anything_is_saved():
    import pytest

    with pytest.raises(ValueError):
        cal.create("X", start_iso="next Thursday", caller=lambda *a, **kw: {})
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_calendar.py -v
```
Expected: `AttributeError: module 'juno.calendar' has no attribute 'create'`

**Step 3: Implement**

```python
# append to juno/calendar.py

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
        raise eventkit.EventKitError(f"could not create the event: {out['error']}")
    return out["id"]
```

**Step 4: Verify, then prove it once against the real calendar**

```bash
.venv/bin/python -m pytest tests/test_calendar.py -v
```

Then, deliberately — this creates a real event you will need to delete yourself:

```bash
.venv/bin/python -c "
from juno import calendar as c
from datetime import datetime, timedelta
start = (datetime.now() + timedelta(days=1)).replace(hour=14, minute=0).strftime('%Y-%m-%dT%H:%M')
print('created', c.create('Juno test — delete me', start_iso=start))
print(c.week())"
```

Expected: it appears in Calendar tomorrow at 14:00 and in the printed week.
**Delete it yourself** — Juno cannot, by design.

If `saveEventSpanCommitError` fails, try `store.saveEventSpanError(e, $.EKSpanThisEvent, $())`.

**Step 5: Commit**

```bash
git add juno/calendar.py tests/test_calendar.py
git commit -m "feat: create calendar events — create-only, by construction"
```

---

## Task 6: The Guard learns about actions

**Files:**
- Modify: `juno/state.py` (add `Action`, `State.actions`, `State.agenda`)
- Modify: `juno/guard.py` (add `check_action`, `earliest_hour`, `latest_hour`)
- Test: `tests/test_actions.py`

This is the most important task in the plan. Everything above it is plumbing; this
is where the promises live.

**Step 1: Write the failing tests**

```python
# tests/test_actions.py
"""The Guard's rules for anything that leaves Notes and touches a real app.

Each test is named after the promise it keeps. If one of these ever has to be
deleted to make a feature work, the feature is wrong.
"""

import sys, pathlib
from datetime import datetime, timedelta
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import guard
from juno.state import Action

SOON = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT14:00")
ABOUT_9AM = "## My rules\n- Never schedule me before 9am.\n- Keep it short."


def _reminder(**kw):
    return Action(kind="reminder", op="create", title="Call the pharmacy", when=SOON, **kw)


def _event(**kw):
    return Action(kind="event", op="create", title="Dentist", when=SOON, **kw)


# --- calendar events are create-only ---------------------------------------

def test_an_event_may_be_created():
    assert guard.check_action(_event(), about="")


def test_an_event_can_never_be_moved():
    v = guard.check_action(Action(kind="event", op="move", title="Dentist", when=SOON), about="")
    assert not v and "never move" in v.reason.lower()


def test_an_event_can_never_be_deleted():
    v = guard.check_action(Action(kind="event", op="delete", title="Dentist"), about="")
    assert not v


# --- reminders may be created and completed, never deleted -----------------

def test_a_reminder_may_be_created_and_completed():
    assert guard.check_action(_reminder(), about="")
    assert guard.check_action(Action(kind="reminder", op="complete", title="Buy milk"), about="")


def test_a_reminder_can_never_be_deleted():
    v = guard.check_action(Action(kind="reminder", op="delete", title="Buy milk"), about="")
    assert not v and "delet" in v.reason.lower()


# --- the standing instructions are enforced in code, not by the model ------

def test_nothing_is_scheduled_before_the_hour_the_user_said():
    early = Action(kind="event", op="create", title="Standup",
                   when=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT07:30"))
    v = guard.check_action(early, about=ABOUT_9AM)
    assert not v and "9" in v.reason


def test_the_same_time_is_fine_when_the_user_never_said_otherwise():
    early = Action(kind="event", op="create", title="Standup",
                   when=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT07:30"))
    assert guard.check_action(early, about="Keep it short.")


def test_an_all_day_item_is_not_caught_by_an_hour_rule():
    allday = Action(kind="reminder", op="create", title="Renew passport",
                    when=(datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"))
    assert guard.check_action(allday, about=ABOUT_9AM)


def test_the_hour_rule_is_read_out_of_plain_english():
    assert guard.earliest_hour("- Never schedule me before 9am.") == 9
    assert guard.earliest_hour("nothing before 08:30") == 8
    assert guard.earliest_hour("no meetings before 10 am please") == 10
    assert guard.earliest_hour("Keep it short.") is None


def test_an_evening_rule_works_the_same_way():
    late = Action(kind="event", op="create", title="Call",
                  when=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT21:00"))
    v = guard.check_action(late, about="- Nothing after 7pm.")
    assert not v


# --- the model's dates are checked, because models get dates wrong ---------

def test_a_date_that_does_not_parse_is_refused_clearly():
    v = guard.check_action(Action(kind="event", op="create", title="X", when="next Thursday"),
                           about="")
    assert not v and "date" in v.reason.lower()


def test_an_event_in_the_past_is_refused_because_it_is_almost_always_a_wrong_year():
    past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT14:00")
    v = guard.check_action(Action(kind="event", op="create", title="X", when=past), about="")
    assert not v and "past" in v.reason.lower()


def test_a_date_years_away_is_refused_because_it_is_a_typo_not_a_plan():
    far = (datetime.now() + timedelta(days=800)).strftime("%Y-%m-%dT14:00")
    v = guard.check_action(Action(kind="event", op="create", title="X", when=far), about="")
    assert not v


def test_if_you_said_thursday_and_she_heard_friday_she_asks_instead_of_guessing():
    """The single most likely way this feature embarrasses itself: a confident
    reminder on the wrong day. If the user named a weekday, the resolved date has
    to actually fall on it."""
    friday = datetime.now() + timedelta(days=(4 - datetime.now().weekday()) % 7 or 7)
    action = Action(kind="reminder", op="create", title="Call the pharmacy",
                    when=friday.strftime("%Y-%m-%dT09:00"))
    v = guard.check_action(action, about="", request="remind me to call the pharmacy thursday")
    assert not v and "thursday" in v.reason.lower()


def test_the_right_weekday_passes():
    thursday = datetime.now() + timedelta(days=(3 - datetime.now().weekday()) % 7 or 7)
    action = Action(kind="reminder", op="create", title="Call the pharmacy",
                    when=thursday.strftime("%Y-%m-%dT09:00"))
    assert guard.check_action(action, about="", request="remind me to call the pharmacy thursday")


# --- the same privacy promise as every other write -------------------------

def test_a_password_can_no_more_reach_reminders_than_it_can_reach_a_note():
    v = guard.check_action(
        Action(kind="reminder", op="create", title="wifi password is hunter2trombone",
               when=SOON),
        about="")
    assert not v


def test_an_empty_title_is_refused():
    assert not guard.check_action(Action(kind="reminder", op="create", title="   "), about="")


def test_an_unknown_kind_is_refused_rather_than_guessed_at():
    assert not guard.check_action(Action(kind="email", op="create", title="Hi"), about="")
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_actions.py -v
```
Expected: `ImportError: cannot import name 'Action' from 'juno.state'`

**Step 3: Add `Action` to `juno/state.py`**

```python
@dataclass
class Action:
    """Something Juno wants to do outside Notes. Nothing happens until the Guard
    passes it, and the Guard is plain code."""
    kind: str                        # "reminder" | "event"
    op: str                          # reminder: create|complete   event: create
    title: str
    when: str | None = None          # ISO 8601 local: "2026-09-03T09:00" or "2026-09-03"
    ends: str | None = None          # events only
    where: str = ""                  # list name / calendar name
    notes: str = ""
    target_id: str | None = None     # set by the doer for "complete"
```

And extend `State`:

```python
    agenda: str = ""                          # today's calendar + open reminders
    actions: list[Action] = field(default_factory=list)
```

**Step 4: Add the rules to `juno/guard.py`**

```python
# add to the imports
import re
from datetime import datetime, timedelta

from . import notedoc, privacy, when, workspace

MAX_TITLE_CHARS = 300
MAX_AHEAD_DAYS = 400
MAX_BEHIND_HOURS = 12

KINDS = {"reminder": {"create", "complete"}, "event": {"create"}}

# "Never schedule me before 9am" / "nothing before 08:30" / "no meetings before 10 am"
_HOUR = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
EARLIEST = re.compile(rf"(?i)\b(?:never|no|nothing|don'?t|not)\b[^.\n]{{0,40}}?\bbefore\s+{_HOUR}")
LATEST = re.compile(rf"(?i)\b(?:never|no|nothing|don'?t|not)\b[^.\n]{{0,40}}?\bafter\s+{_HOUR}")


def _hour(match) -> int | None:
    if not match:
        return None
    hour = int(match.group(1))
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return hour if 0 <= hour <= 23 else None


def earliest_hour(about: str) -> int | None:
    """The 'nothing before 9am' rule, read out of the user's own words."""
    return _hour(EARLIEST.search(about or ""))


def latest_hour(about: str) -> int | None:
    return _hour(LATEST.search(about or ""))


def check_action(action, *, about: str = "", request: str = "", now: datetime | None = None) -> Verdict:
    """Judge one thing Juno wants to do outside Notes.

    The two hard promises live here. A calendar event may only ever be created —
    the user's instruction note says nothing already in their calendar gets moved
    without asking, and that is enforced in code rather than left to a prompt. A
    reminder may be created or completed, never deleted: 'done' must not silently
    mean 'gone'.
    """
    now = now or datetime.now()

    allowed_ops = KINDS.get(action.kind)
    if allowed_ops is None:
        return Verdict(False, f"I don't know how to work with {action.kind!r}.")

    if action.op not in allowed_ops:
        if action.kind == "event":
            return Verdict(False, "I never move or delete anything already in your calendar. "
                                  "I can add something new, or you can change it yourself.")
        return Verdict(False, "I don't delete reminders — I can only tick one off. "
                              "Deleting is yours to do.")

    if not action.title.strip():
        return Verdict(False, "refusing to create something with no name")

    if len(action.title) > MAX_TITLE_CHARS:
        return Verdict(False, f"that title is {len(action.title)} characters — too long to be a task")

    if privacy.contains_secret(f"{action.title}\n{action.notes}"):
        return Verdict(False, "the text contains something that looks like a password or key")

    if action.op == "complete":
        return ALLOW

    # Everything below is about a date, and a model's date is not to be trusted.
    if not action.when:
        return ALLOW if action.kind == "reminder" else Verdict(
            False, "an event needs a date and a time — tell me when and I'll add it")

    moment = when.parse(action.when)
    if moment is None:
        return Verdict(False, f"I couldn't read {action.when!r} as a date. "
                              "Give me a day and a time and I'll set it.")

    if moment < now - timedelta(hours=MAX_BEHIND_HOURS):
        return Verdict(False, f"that lands in the past ({when.human(moment)}) — "
                              "I've probably got the year or the day wrong. Say the date.")

    if moment > now + timedelta(days=MAX_AHEAD_DAYS):
        return Verdict(False, f"that lands on {when.human(moment)}, over a year away. "
                              "That is usually a typo. Say the date and I'll set it.")

    # If they named a weekday, the date has to actually be that weekday. This is
    # the failure that would embarrass her most: a confident reminder, wrong day.
    named = when.weekday_named(request or "")
    if named is not None and moment.weekday() != named:
        said = when.WEEKDAYS[named].capitalize()
        return Verdict(False, f"you said {said}, but I worked that out as {when.human(moment)}. "
                              "Say the date and I'll set it.")

    if when.has_time(action.when):
        floor = earliest_hour(about)
        if floor is not None and moment.hour < floor:
            return Verdict(False, f"that's {moment:%H:%M}, and you asked me never to schedule "
                                  f"anything before {floor:02d}:00.")
        ceiling = latest_hour(about)
        if ceiling is not None and moment.hour >= ceiling:
            return Verdict(False, f"that's {moment:%H:%M}, and you asked me never to schedule "
                                  f"anything after {ceiling:02d}:00.")

    return ALLOW
```

**Step 5: Verify — and confirm nothing old broke**

```bash
.venv/bin/python -m pytest tests/test_actions.py -v
.venv/bin/python -m pytest tests -q
```
Expected: all of `test_actions.py` passes, and the 12 existing guard tests still
pass untouched (`guard.check` was not modified).

**Step 6: Commit**

```bash
git add juno/state.py juno/guard.py tests/test_actions.py
git commit -m "feat: the Guard judges reminders and events — create-only calendar, no deletes, your hours enforced in code"
```

---

## Task 7: `Executor.do` — the one place an action is applied

**Files:**
- Modify: `juno/executor.py`
- Test: `tests/test_actions.py` (extend)

**Step 1: Write the failing tests**

```python
def test_a_blocked_action_is_logged_and_never_reaches_the_app(monkeypatch):
    """Invariant 4: every write, allowed or blocked, lands in the Log."""
    from juno import executor as ex_mod
    from juno.state import Action

    logged, created = [], []
    monkeypatch.setattr(ex_mod.Executor, "_log", lambda self, line: logged.append(line))
    monkeypatch.setattr(ex_mod.reminders, "create",
                        lambda *a, **kw: created.append(a) or "x")

    r = ex_mod.Executor().do(Action(kind="reminder", op="delete", title="Buy milk"), about="")
    assert not r.ok
    assert created == []
    assert any("BLOCKED" in line for line in logged)


def test_an_allowed_reminder_reaches_the_app_and_is_logged(monkeypatch):
    from datetime import datetime, timedelta
    from juno import executor as ex_mod
    from juno.state import Action

    logged, created = [], []
    monkeypatch.setattr(ex_mod.Executor, "_log", lambda self, line: logged.append(line))
    monkeypatch.setattr(ex_mod.reminders, "create",
                        lambda title, **kw: created.append(title) or "x-7")

    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT14:00")
    r = ex_mod.Executor().do(Action(kind="reminder", op="create",
                                    title="Call the pharmacy", when=soon), about="")
    assert r.ok and r.ref == "x-7"
    assert created == ["Call the pharmacy"]
    assert logged


def test_a_dry_run_touches_nothing(monkeypatch):
    from datetime import datetime, timedelta
    from juno import executor as ex_mod
    from juno.state import Action

    called = []
    monkeypatch.setattr(ex_mod.reminders, "create", lambda *a, **kw: called.append(a) or "x")
    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT14:00")
    r = ex_mod.Executor(dry_run=True).do(
        Action(kind="reminder", op="create", title="X", when=soon), about="")
    assert r.ok and called == []


def test_completing_looks_the_reminder_up_by_what_the_user_called_it(monkeypatch):
    from juno import executor as ex_mod
    from juno.reminders import Reminder
    from juno.state import Action

    monkeypatch.setattr(ex_mod.Executor, "_log", lambda self, line: None)
    monkeypatch.setattr(ex_mod.reminders, "find_open",
                        lambda phrase, **kw: Reminder("Inbox", "x-3", "Call the pharmacy", ""))
    done = []
    monkeypatch.setattr(ex_mod.reminders, "complete", lambda rid, **kw: done.append(rid) or "ok")

    r = ex_mod.Executor().do(Action(kind="reminder", op="complete",
                                    title="call the pharmacy"), about="")
    assert r.ok and done == ["x-3"]


def test_ticking_off_something_that_isnt_there_says_so(monkeypatch):
    from juno import executor as ex_mod
    from juno.state import Action

    monkeypatch.setattr(ex_mod.Executor, "_log", lambda self, line: None)
    monkeypatch.setattr(ex_mod.reminders, "find_open", lambda phrase, **kw: None)
    r = ex_mod.Executor().do(Action(kind="reminder", op="complete", title="feed the cat"), about="")
    assert not r.ok and "couldn't find" in r.reason.lower()
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_actions.py -v
```
Expected: `AttributeError: 'Executor' object has no attribute 'do'`

**Step 3: Implement**

```python
# juno/executor.py — extend the imports
from . import calendar, guard, markup, notedoc, notes, reminders, workspace


# extend WriteResult
@dataclass(frozen=True)
class WriteResult:
    ok: bool
    reason: str
    note_id: str | None = None
    ref: str | None = None       # reminder id / event uid


# add to class Executor
    def do(self, action, *, about: str = "", request: str = "") -> WriteResult:
        """Apply one thing outside Notes. Still no model anywhere in this path."""
        verdict = guard.check_action(action, about=about, request=request)
        if not verdict:
            self._log(f"**BLOCKED** {action.op} {action.kind} *{action.title}* — {verdict.reason}")
            return WriteResult(False, verdict.reason)

        if self.dry_run:
            return WriteResult(True, "dry run — nothing created")

        try:
            ref, detail = self._perform(action)
        except Exception as e:
            # An app that is not approved yet hangs rather than failing, so a real
            # exception here is worth saying out loud instead of swallowing.
            self._log(f"**FAILED** {action.op} {action.kind} *{action.title}* — {type(e).__name__}: {e}")
            return WriteResult(False, f"{action.kind} app said no ({type(e).__name__})")

        self._log(f"{action.op} {action.kind} *{action.title}*{detail}")
        return WriteResult(True, detail.strip(" —") or "done", ref=ref)

    def _perform(self, action) -> tuple[str, str]:
        if action.kind == "reminder" and action.op == "create":
            ref = reminders.create(action.title, notes=action.notes,
                                   list_name=action.where, when_iso=action.when)
            return ref, self._said(action)

        if action.kind == "reminder" and action.op == "complete":
            hit = reminders.find_open(action.title)
            if hit is None:
                raise LookupError(f"couldn't find an open reminder called {action.title!r}")
            reminders.complete(hit.id)
            return hit.id, f" — ticked off “{hit.title}”"

        if action.kind == "event" and action.op == "create":
            ref = calendar.create(action.title, start_iso=action.when, end_iso=action.ends,
                                  calendar_name=action.where, notes=action.notes)
            return ref, self._said(action)

        raise ValueError(f"nothing to do for {action.kind}/{action.op}")

    @staticmethod
    def _said(action) -> str:
        from . import when as when_mod

        moment = when_mod.parse(action.when)
        return f" — {when_mod.human(moment)}" if moment else ""
```

Also make the `LookupError` from `_perform` read well. In `do`, special-case it:

```python
        except LookupError as e:
            self._log(f"**BLOCKED** {action.op} {action.kind} *{action.title}* — {e}")
            return WriteResult(False, str(e))
        except Exception as e:
            ...
```

**Step 4: Verify**

```bash
.venv/bin/python -m pytest tests/test_actions.py -v
.venv/bin/python -m pytest tests -q
```

**Step 5: Commit**

```bash
git add juno/executor.py tests/test_actions.py
git commit -m "feat: Executor.do — the single choke point for reminders and events"
```

---

## Task 8: Three new nodes, and the router that reaches them

**Files:**
- Modify: `juno/nodes.py` (router intents, `agenda`, `scheduler`, `doer`)
- Modify: `juno/graph.py` (NODES, EDGES, ORDER)
- Test: `tests/test_graph.py` (extend)

**Step 1: Write the failing tests**

```python
# add to tests/test_graph.py

class SchedulingBrain(FakeBrain):
    """A brain that routes to `remind` and extracts a structured action."""

    def __init__(self, intent="remind", when=None):
        super().__init__(intent=intent)
        from datetime import datetime, timedelta
        self.when = when or (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT09:00")

    def ask_json(self, **kw):
        self.calls.append(("json", kw.get("tier")))
        if "extract" in kw.get("system", "").lower():
            return {"kind": "reminder", "op": "create",
                    "title": "Call the pharmacy", "when": self.when}
        return {"intent": self.intent, "needs_context": False, "needs_web": False, "why": "t"}


def test_a_reminder_request_produces_an_action_not_a_note_write():
    brain = SchedulingBrain()
    state = graph.run("remind me to call the pharmacy", brain=brain, dry_run=True)
    assert [a.title for a in state.actions] == ["Call the pharmacy"]
    assert state.actions[0].kind == "reminder"


def test_extraction_runs_on_the_cheap_tier():
    brain = SchedulingBrain()
    graph.run("remind me to call the pharmacy", brain=brain, dry_run=True)
    assert ("json", "fast") in brain.calls
    assert ("json", "smart") not in brain.calls


def test_the_reply_says_what_actually_happened_not_what_was_intended():
    """The whole reason `doer` runs before `writer`: if the Guard blocks the
    action, she must not have already claimed she set it."""
    from datetime import datetime, timedelta
    past = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%dT09:00")
    brain = SchedulingBrain(when=past)
    state = graph.run("remind me to call the pharmacy", brain=brain, dry_run=True)
    assert "set" not in state.answer.lower() or "couldn" in state.answer.lower()
    assert any("✗" in r for r in state.results)


def test_a_scheduling_reply_costs_no_smart_model_call():
    """Confirming a reminder is a fact, not an essay. It is composed in code."""
    brain = SchedulingBrain()
    graph.run("remind me to call the pharmacy", brain=brain, dry_run=True)
    assert ("text", "smart") not in brain.calls


def test_planning_the_day_reads_the_real_calendar_first(monkeypatch):
    from juno import nodes

    monkeypatch.setattr(nodes, "_agenda_text", lambda: "- 09:30 Standup")
    brain = FakeBrain(intent="plan")
    state = graph.run("plan my day", brain=brain, dry_run=True)
    assert "Standup" in state.agenda


def test_a_calendar_that_cannot_be_read_never_stops_the_plan(monkeypatch):
    """Automation approval can be revoked at any time. A missing calendar costs
    her context, not the whole morning."""
    from juno import nodes

    def boom():
        raise RuntimeError("not approved")

    monkeypatch.setattr(nodes, "_agenda_text", boom)
    brain = FakeBrain(intent="plan")
    state = graph.run("plan my day", brain=brain, dry_run=True)
    assert state.writes
    assert any("calendar" in t.lower() for t in state.trace)


def test_the_declared_order_still_matches_the_declared_edges():
    assert set(graph.ORDER) == set(graph.NODES)
    for e in graph.EDGES:
        assert graph.ORDER.index(e.frm) < graph.ORDER.index(e.to)
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_graph.py -v
```
Expected: failures on `state.actions` and the new nodes.

**Step 3: Extend the router in `juno/nodes.py`**

```python
INTENTS = ("question", "task", "capture", "plan", "remind", "schedule", "ignore")
```

Add to `ROUTER_SYSTEM`, after the `plan` line:

```
- remind: they want a reminder set, or an existing one ticked off
- schedule: they want something put in their calendar
```

**Step 4: Add the three nodes**

```python
# ------------------------------------------------------------------ Agenda

SCHEDULING = ("plan", "remind", "schedule")


def _agenda_text() -> str:
    """Today's calendar and what is still outstanding. Two app reads, no model."""
    from . import calendar, reminders

    return (f"## In your calendar today\n{calendar.brief()}\n\n"
            f"## Still outstanding in Reminders\n{reminders.summary()}")


def agenda(state: State, *, brain=None) -> State:
    """No model. Reads the real day, but only when the request is about the day.

    This is deliberately not in `watcher`: the watcher runs on every wake-up of the
    listener, and firing a Calendar query every few seconds would wedge the very
    app the plan depends on.
    """
    if state.intent not in SCHEDULING:
        return state
    try:
        state.agenda = _agenda_text()
    except Exception as e:
        # Automation approval can be revoked at any time, and Calendar hangs rather
        # than failing when it is. Losing context is survivable; losing the morning
        # routine is not.
        state.note("agenda", f"could not read calendar or reminders ({type(e).__name__})")
        return state
    state.note("agenda", f"{len(state.agenda)} chars of real commitments")
    return state


# --------------------------------------------------------------- Scheduler

SCHEDULER_SYSTEM = """You extract one scheduling action from what someone said to their assistant.

Reply with JSON only:
{"kind": "reminder"|"event", "op": "create"|"complete", "title": "...",
 "when": "YYYY-MM-DDTHH:MM" or "YYYY-MM-DD" or null,
 "ends": "YYYY-MM-DDTHH:MM" or null, "where": "", "notes": ""}

- kind is "event" only when they clearly mean their calendar: a meeting, an
  appointment, something with other people or a fixed slot. Otherwise "reminder".
- op is "complete" when they are telling you something is already done.
- title is the task itself, in their words, with no "remind me to" in front of it.
- when: resolve relative dates against today's date, which you are given. If they
  gave no time of day, give the date only. Never invent a time they did not ask for.
- Output nothing but the JSON object."""


def scheduler(state: State, *, brain) -> State:
    """Turns English into one structured Action. The only new model call, on Nano."""
    if state.intent not in ("remind", "schedule"):
        return state
    try:
        out = brain.ask_json(system=SCHEDULER_SYSTEM, user=_prompt(state),
                             tier="fast", max_tokens=400)
    except Exception as e:
        state.note("scheduler", f"could not read that as a date ({type(e).__name__})")
        state.answer = ("I couldn't work out the date from that. "
                        "Say it as a day and a time and I'll set it.")
        return state

    action = Action(
        kind=out.get("kind") or ("event" if state.intent == "schedule" else "reminder"),
        op=out.get("op") or "create",
        title=(out.get("title") or "").strip(),
        when=out.get("when"),
        ends=out.get("ends"),
        where=out.get("where") or "",
        notes=out.get("notes") or "",
    )
    state.actions.append(action)
    state.note("scheduler", f"{action.op} {action.kind}: {action.title}")
    return state


# --------------------------------------------------------------------- Doer

def doer(state: State, *, brain=None, dry_run: bool = False) -> State:
    """No model. Applies the actions, then writes the truth into state.answer.

    This runs before the writer on purpose. If the writer went first she could
    announce a reminder the Guard was about to refuse.
    """
    if not state.actions:
        return state
    ex = Executor(dry_run=dry_run)
    done: list[str] = []
    for a in state.actions:
        r = ex.do(a, about=state.about, request=state.request)
        state.results.append(f"{'✓' if r.ok else '✗'} {a.kind} — {r.reason}")
        done.append(_confirmation(a, r))
    state.answer = "\n\n".join(done)
    state.note("doer", f"{len(state.actions)} actions")
    return state


def _confirmation(action, result) -> str:
    """Plain, specific, and always says the weekday out loud — a wrong date has to
    be obvious at a glance, not discovered on the day."""
    from . import when as when_mod

    if not result.ok:
        return f"I didn't set that. {result.reason}"
    if action.op == "complete":
        return f"Ticked off: {action.title}"
    moment = when_mod.parse(action.when)
    word = "In your calendar" if action.kind == "event" else "Reminder set"
    if moment is None:
        return f"{word}: {action.title}"
    return f"{word}: {action.title} — {when_mod.human(moment)}"
```

Extend the `writer` so it steps aside for scheduling (it already steps aside for
plans):

```python
def writer(state: State, *, brain) -> State:
    if state.intent in ("ignore", "plan"):
        return state
    if state.intent in ("remind", "schedule"):
        # The doer already said exactly what happened. Paying a smart model to
        # rephrase a fact would only give it room to get the fact wrong.
        if state.reply_to is None:
            state.writes.append(Write(title=workspace.ASK, mode="append",
                                      markdown=f"\n**Juno:** {state.answer}\n\n———\n\n"))
        else:
            title, folder, after = state.reply_to
            state.writes.append(Write(title=title, folder=folder, mode="insert", after=after,
                                      markdown=f"**Juno:** {state.answer}\n\n———\n"))
        state.note("writer", "confirmed without a model call")
        return state
    ...  # the rest is unchanged
```

Add the agenda to the model's prompt in `_prompt`, right before the request:

```python
    if state.agenda:
        parts.append(f"# Their real day, from Calendar and Reminders\n{state.agenda}")
```

And import `Action`:

```python
from .state import Action, State, Write
```

**Step 5: Wire the graph in `juno/graph.py`**

```python
NODES: dict[str, Node] = {
    "watcher": nodes.watcher,
    "router": nodes.router,
    "retriever": nodes.retriever,
    "researcher": nodes.researcher,
    "agenda": nodes.agenda,
    "planner": nodes.planner,
    "scheduler": nodes.scheduler,
    "doer": nodes.doer,
    "writer": nodes.writer,
    "executor": nodes.executor,
}

EDGES = (
    Edge("watcher", "router"),
    Edge("router", "retriever"),
    Edge("retriever", "researcher"),
    Edge("researcher", "agenda"),
    Edge("agenda", "planner"),
    Edge("planner", "scheduler"),
    Edge("scheduler", "doer"),
    Edge("doer", "writer"),
    Edge("writer", "executor"),
)

ORDER = ("watcher", "router", "retriever", "researcher", "agenda",
         "planner", "scheduler", "doer", "writer", "executor")
```

And in `run()`, `doer` needs `dry_run` too:

```python
        if name in ("executor", "doer"):
            state = fn(state, brain=brain, dry_run=dry_run)
        else:
            state = fn(state, brain=brain)
```

Update the docstring diagram at the top of `graph.py` to match.

**Step 6: Verify**

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m juno graph
```
Expected: everything green, and the diagram shows all nine edges.

**Step 7: Commit**

```bash
git add juno/nodes.py juno/graph.py tests/test_graph.py
git commit -m "feat: agenda, scheduler and doer — she sets a real reminder and says only what actually happened"
```

---

## Task 9: `☀️ Today` built around the day you actually have

**Files:**
- Modify: `juno/nodes.py` (`PLANNER_SYSTEM`)
- Modify: `juno/daily.py` (warm the two new apps)
- Modify: `juno/watch.py` (warm at startup)
- Test: `tests/test_graph.py` (extend)

**Step 1: Write the failing test**

```python
def test_the_planner_is_told_not_to_plan_over_a_real_appointment():
    from juno import nodes

    system = nodes.PLANNER_SYSTEM.lower()
    assert "calendar" in system
    assert "already" in system
```

**Step 2: Extend `PLANNER_SYSTEM`**

Add these lines to the Rules block:

```
- You are given their real calendar and their real open reminders. Plan around
  what is already there. Never put work on top of an appointment, and never
  invent a commitment that is not in the list you were given.
- If something is already in Reminders, do not re-list it as a new task. Refer to
  it, or leave it alone.
```

**Step 3: Warm Notes only — the other two need nothing**

In `juno/daily.py`, at the top of `morning()`:

```python
    # Notes has a cold start of up to forty seconds after it has been idle. Absorb
    # it here, where waiting costs nobody anything. Reminders and Calendar need no
    # equivalent: EventKit reads the store directly and never wakes those apps —
    # which is the whole reason the agenda is cheap enough to read per request.
    try:
        say(f"Notes ready in {notes.warm_up():.1f}s")
    except Exception as e:
        say(f"Notes did not answer ({type(e).__name__}) — run `juno permissions`")
```

Import `notes` in `daily.py`, and do the same in `watch.Watcher.run_forever()`
before the loop starts.

**Step 4: Verify**

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m juno morning --dry-run
```

**Step 5: Commit**

```bash
git add juno/nodes.py juno/daily.py juno/watch.py tests/test_graph.py
git commit -m "feat: the daily plan is built around real appointments, and the apps are warmed first"
```

---

## Task 10: Surfaces — `juno agenda`, docs, and the care note

**Files:**
- Modify: `juno/cli.py` (`agenda` command)
- Modify: `juno/care.py` (a signal for revoked permissions)
- Modify: `CLAUDE.md`, `README.md`
- Test: `tests/test_care.py` (extend)

**Step 1: Write the failing test**

```python
def test_she_asks_for_help_when_an_app_stops_answering(monkeypatch):
    """Automation approval can be revoked in System Settings at any time, and the
    only symptom is silence. The care note is where silence becomes a sentence."""
    from juno import care, permissions

    monkeypatch.setattr(care.permissions, "check", lambda: [
        permissions.Check("Notes", True, "ready", ""),
        permissions.Check("Reminders", False, "no answer", "Turn on Reminders"),
    ])
    signals = care.check()
    apps = [s for s in signals if s.key == "apps"]
    assert apps and apps[0].severity == "needs you"
    assert "Reminders" in apps[0].fact
```

**Step 2: Add the signal to `care.check()`**

```python
    # 6. Apps she is no longer allowed to talk to. The symptom is silence, so it
    #    has to be said out loud somewhere the user will read it.
    blocked = [c.app for c in permissions.check() if not c.ok]
    if blocked:
        out.append(Signal(
            "apps", "needs you",
            f"I can't reach {', '.join(blocked)} any more.",
            "System Settings → Privacy & Security → Automation, switch them back on.",
        ))
    else:
        out.append(Signal("apps", "ok", "Notes, Reminders and Calendar all answer.", ""))
```

Import it: `from . import index, markup, notes, permissions, workspace`.

**Step 3: Add `juno agenda`**

```python
def cmd_agenda(args):
    from . import calendar, reminders

    print(f"\n  Today\n\n{calendar.brief()}\n")
    print(f"  This week\n\n{calendar.week()}\n")
    print(f"  Outstanding\n\n{reminders.summary()}\n")
```

```python
sub.add_parser("agenda", help="what's in your calendar and what's still open"
               ).set_defaults(fn=cmd_agenda)
```

**Step 4: Update `CLAUDE.md`**

Add to Commands:

```bash
.venv/bin/python -m juno permissions       # can she reach Notes, Reminders, Calendar?
.venv/bin/python -m juno agenda            # today, this week, and what's outstanding
```

Add the two new modules to the module table:

| `eventkit.py` | Apple's calendar/reminder store, read directly. 700× faster than the apps. |
| `reminders.py` | Create and complete reminders; never delete. |
| `calendar.py` | Bounded date-range reads; create-only. |
| `when.py` | Dates, moved between model, Python and AppleScript without drift. |
| `permissions.py` | Which apps she is actually allowed to read — including write-only. |

Add two invariants:

```
6. **A calendar event may only ever be created.** No move, no delete, no update —
   there is no code in `calendar.py` that could do it.
7. **A reminder may be created or completed, never deleted.** Done is not gone.
```

Replace the Performance section's scope — it now covers three apps, and the rule is
different for each:

```
## Performance — measured on 358 notes, 1,263 reminders and 1,757 events

**Notes: bulk AppleScript queries, addressed by index.** (existing table stays)

**Reminders and Calendar: never AppleScript. EventKit.**

| Doing it the obvious way | Doing it right |
|---|---|
| 23 open reminders via the Reminders app — **65.7s** | via EventKit — **0.093s** |
| 7-day calendar window via the Calendar app — **26.0s** | via EventKit — **0.025s** |

`whose` filters in those two apps walk every object that has ever existed in them,
so cost scales with the user's history, not the answer. Reminders cannot even return
properties from a filtered set: `name of rs` raises
`Can't get name of {reminder id "x-apple-reminder://…"}`. There is no tuning that
closes a 700× gap — use `juno/eventkit.py`.
```

Add to Apple app limits:

```
- Three separate permissions, each with its own silent failure. **Notes** uses
  AppleScript Automation, and an unapproved app *hangs* rather than failing.
  **Calendar and Reminders** use EventKit, whose `write only` state is the nasty
  one: it raises nothing and reports one calendar and zero events, so a blocked
  calendar is indistinguishable from a free week. `juno permissions` reads the
  numeric status instead of trusting a query.
- EventKit reads are asynchronous and JXA has no `await`. A script that does not
  pump `NSRunLoop.runModeBeforeDate` exits before the callback fires and returns
  nothing, every time, with no error.
- Do not replace the JXA scripts with a compiled Swift helper. EventKit access is
  granted per binary, and an unsigned binary's identity changes on every rebuild —
  so every edit to Juno would re-prompt, and a background listener can never answer
  a prompt. `osascript` inherits the terminal's stable identity.
- A dated reminder needs an explicit `EKAlarm`. A due date alone shows in the app
  but does not notify, and a reminder that does not buzz is a note with a circle.
```

**Step 5: Update `README.md`** with a short "She can set a reminder" section
showing the Notes-typed request and the reminder that appears on the phone.

**Step 6: Verify and commit**

```bash
.venv/bin/python -m pytest tests -q
git add juno/cli.py juno/care.py CLAUDE.md README.md tests/test_care.py
git commit -m "docs: reminders and calendar — what she can do, and the traps that cost the time"
```

---

## Task 11: The demo, rehearsed

This is the hackathon money shot. Run it end to end, on a real Mac, with a real
phone in shot.

**Step 1: Set the stage**

```bash
.venv/bin/python -m juno permissions      # all three ✓
.venv/bin/python -m juno listen --install
```

**Step 2: In Apple Notes → `🤖 JUNO` → `📥 Ask Juno`, type:**

```
remind me to call the pharmacy thursday at 10am
```

Expected, within ~15 seconds: a reply appears underneath —
`Juno: Reminder set: Call the pharmacy — Thursday 3 September at 10:00` — and the
reminder appears in the Reminders app and on the phone.

**Step 3: Prove the Guard, on camera**

```
put a dentist appointment in my calendar for tomorrow at 7am
```

Expected: `I didn't set that. That's 07:00, and you asked me never to schedule
anything before 09:00.` Nothing is created. The block is in `📊 Log`.

**Step 4: Prove the date check**

```
remind me to renew my passport thursday
```

If the model resolves the wrong weekday, she says so rather than setting it. If it
resolves correctly, the confirmation names the weekday out loud so it is checkable.

**Step 5: Prove the plan changed**

```bash
.venv/bin/python -m juno plan
```

`☀️ Today` should now be built around whatever is actually in the calendar.

**Step 6: Check the receipts**

Open `📊 Log`. Every action — the reminder created, the 7am event blocked and why —
is there.

**Step 7: Commit and tag**

```bash
git add -A && git commit -m "feat: Reminders and Calendar — Juno runs a life, not just a notebook"
git tag reminders-calendar-v1
```

---

## Size

| Tasks | What | Time |
|---|---|---|
| 0 | Calendar full access + `juno permissions` | 30 min (1 min of it yours) |
| 1–2 | `when.py`, `eventkit.py` | 2 hours |
| 3 | Reminders read + write | 2 hours |
| 4–5 | Calendar read + write | 2 hours |
| 6–7 | Guard rules and `Executor.do` | 3 hours |
| 8–9 | Three nodes, router, planner | 3 hours |
| 10–11 | CLI, docs, demo rehearsal | 2 hours |

**About a day and a half** — shorter than the original estimate, because EventKit
removed the performance work, the delimiter parsing and the `missing value`
handling that the AppleScript design would have needed. The demo win — a real
reminder buzzing on the phone after being typed into Notes — lands at the end of
Task 8, roughly a day in.

## What is deliberately not here

- **Deleting anything.** Not reminders, not events. Ever.
- **Moving an event.** The user's own instruction note forbids it, and the plan
  enforces it by not writing the code.
- **Creating recurring events** ("every Tuesday"). *Reading* recurrence is free —
  EventKit expands occurrences inside a window. Creating a rule is not, and a wrong
  recurring event is a wrong event forty times. Single occurrences only in v1.
- **A `📅 Agenda` note.** The calendar is already an app. Mirroring it into Notes
  would create a second copy to keep wrong.
