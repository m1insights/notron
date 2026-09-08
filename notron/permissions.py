"""Which of your apps Notron is actually allowed to read.

Three separate permissions, and each fails in its own quiet way:

  * **Notes** uses AppleScript Automation. An unapproved app does not refuse —
    it *hangs*, waiting on a dialog a background job can never answer.
  * **Calendar and Reminders** use EventKit, which has four states, and the
    dangerous one is `write only`. Write-only access does not raise anything. It
    reports one calendar and zero events, so a blocked calendar is indistinguishable
    from a free week. Measured on this Mac 2026-08-30: Calendar was write-only and
    looked empty all day.
  * **Speech** — transcribing a voice memo — is the exception that proves the
    rule. Its numeric status is the *wrong* thing to read: measured 2026-09-05,
    `SFSpeechRecognizer.authorizationStatus` sits at `notDetermined` forever
    (the request never calls back under `osascript`, because there is no usage
    string in its bundle) while on-device transcription works perfectly. So
    this one is reported by what it can actually do.

So this module reads the numeric authorization status rather than trying a query
and believing the answer — everywhere the number is the honest one.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import eventkit
from .applescript import run

PROBE_TIMEOUT = 8

#: How long a *failing* check is believed before asking macOS again. A grant is
#: precisely the thing that changes while the process is running — the user goes
#: to System Settings *because* she said she was blind — so a listener that
#: caches "denied" for its whole life keeps apologising for hours after the
#: switch was flipped. A working check, by contrast, is cached for good: access
#: can be revoked, but that shows up as a failed read, which is loud.
RECHECK_SECONDS = 300

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


def _speech_check(speech=None) -> Check:
    """What on-device transcription can actually do, not what TCC says about it."""
    from . import attachments

    try:
        heard = (speech or attachments.speech_available)()
    except Exception:
        heard = False
    return Check(
        "Speech", heard,
        "on-device transcription is available" if heard
        else "no on-device speech recogniser — voice memos stay unread",
        "" if heard else "System Settings → General → Language & Region, add the "
                         "language you speak; macOS downloads the on-device model.")


def check(reader=None, notes_runner=None, speech=None) -> list[Check]:
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
        # Speech has nothing to do with EventKit — an unreadable calendar must
        # not make a working recogniser invisible.
        out.append(_speech_check(speech))
        return out

    for app, key in (("Calendar", "events"), ("Reminders", "reminders")):
        code = int(status.get(key, NOT_DETERMINED))
        detail, broken = EXPLAIN.get(code, (f"unknown status {code}", True))
        fix = (f"System Settings → Privacy & Security → {PANE[app]}, "
               f"give your terminal full access.") if broken else ""
        out.append(Check(app, not broken, detail, fix))

    out.append(_speech_check(speech))
    return out


_CACHE: tuple[float, list[Check]] | None = None


def forget() -> None:
    """Drop the cached answer. For tests, and for anything that has just changed
    a permission and wants the next question answered honestly."""
    global _CACHE
    _CACHE = None


def cached(*, checker=None, now=None) -> list[Check]:
    """`check()`, but not three osascript round trips per question.

    The agenda consults this on every scheduling request and a listener answers
    all day, so the answer is held. See RECHECK_SECONDS for why a bad answer is
    held for minutes and a good one for ever.
    """
    global _CACHE
    import time as _time

    clock = now or _time.time
    checker = checker or check
    if _CACHE is not None:
        when, answer = _CACHE
        if all(c.ok for c in answer) or clock() - when < RECHECK_SECONDS:
            return answer
    answer = checker()
    _CACHE = (clock(), answer)
    return answer


def blind(apps=("Calendar", "Reminders"), *, checker=None) -> list[Check]:
    """The named apps she cannot currently read, cheapest first from the cache."""
    return [c for c in cached(checker=checker) if c.app in apps and not c.ok]
