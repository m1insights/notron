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
to Notron would re-prompt for permission — which a background listener can never
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

#: Every write here ends `saveXCommitError(…, err)` with an `err` that macOS fills
#: in and that this code used to throw away — so "could not create the event" was
#: the whole story whether the calendar was denied, read-only, or gone. Ask the
#: `Ref()` what happened. A Ref that was never written to raises rather than
#: reading nil, hence the try.
REASON = """
function reason(err) {
  try {
    var e = err[0];
    if (e && !e.isNil()) return ObjC.unwrap(e.localizedDescription) || '';
  } catch (x) {}
  return '';
}
"""

#: Dates, pinned. An `NSDateFormatter` with a fixed `dateFormat` and no locale
#: reads the Mac's region setting, and `yyyy` in a Buddhist or Japanese calendar
#: is not the year we mean: `stringFromDate` writes a date nobody asked for and
#: `dateFromString` returns nil, so an event silently lands in the wrong year or
#: a save fails for no visible reason. `en_US_POSIX` plus an explicit Gregorian
#: calendar is Apple's own prescription for a fixed-format date. The time zone
#: is deliberately *not* pinned — `calendarWithIdentifier` keeps the system one
#: (checked 2026-09-07: America/New_York, matching `currentCalendar`), and the
#: user's 2pm means 2pm where they are standing.
#:
#: Nothing in this package builds an `NSDateFormatter` of its own. Going through
#: `pinned()` is what makes that a rule a test can enforce rather than a habit.
DATES = """
function gregorian() {
  return $.NSCalendar.calendarWithIdentifier('gregorian');
}
function pinned(fmt) {
  var f = $.NSDateFormatter.alloc.init;
  f.dateFormat = fmt;
  f.locale = $.NSLocale.localeWithLocaleIdentifier('en_US_POSIX');
  f.calendar = gregorian();
  return f;
}
"""

ISO_MINUTES = "yyyy-MM-dd'T'HH:mm"
ISO_DAY = "yyyy-MM-dd"

PRELUDE = ("ObjC.import('EventKit');\nObjC.import('Foundation');\n"
           + AWAIT + REASON + DATES)


class EventKitError(RuntimeError):
    pass


def _osascript(script: str, timeout: int, *args: str) -> str:
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", "-", *args],
        input=script, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "osascript failed")
    return proc.stdout.strip()


def run(body: str, *, data: dict | None = None, timeout: int = DEFAULT_TIMEOUT, runner=None) -> dict | list:
    """Run trusted JXA only. Dynamic bodies return JSON inside a fixed run handler.

    User/model fields travel in one JSON argv value, never executable source.
    Static no-data readers retain their final-expression convention.
    """
    caller = runner or _osascript
    try:
        if data is None:
            raw = caller(PRELUDE + body, timeout)
        else:
            script = PRELUDE + 'function run(argv) {\nvar input = JSON.parse(argv[0]);\n' + body + '\n}'
            raw = caller(script, timeout, json.dumps(data, allow_nan=False))
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


def failure(out: dict, prefix: str) -> str:
    """The message a failed EventKit write should carry.

    `out["error"]` is our own four words; `out["why"]` is macOS's. The second one
    is the one that tells the user what to do — "Calendar access denied" names a
    switch in System Settings, "save failed" names nothing.
    """
    why = str(out.get("why") or "").strip()
    said = str(out.get("error") or "failed").strip()
    return f"{prefix}: {said}" + (f" — {why}" if why else "")
