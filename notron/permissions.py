"""Which of your apps Notron is actually allowed to read.

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
    WRITE_ONLY: ("is write only — Notron can add things but cannot read them, "
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
