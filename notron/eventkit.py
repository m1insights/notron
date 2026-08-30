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
