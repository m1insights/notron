"""Which apps Juno is actually allowed to talk to.

macOS gates AppleScript per app, per calling process. The failure mode is the
nasty kind: an app you have not been granted does not refuse you, it *hangs*,
waiting on a dialog. A launchd background job can never show that dialog, so the
listener sits there looking wedged. Measured 2026-08-30: Notes answered in 0.12s,
Reminders and Calendar had not answered after forty seconds each.

So we ask each app one trivial question with a short deadline, and read silence
as "not approved yet" rather than "broken".
"""

from __future__ import annotations

from dataclasses import dataclass

from .applescript import run

PROBE_TIMEOUT = 8

PROBES = {
    "Notes": 'on run argv\n tell application "Notes" to return (count of every folder) as text\nend run',
    "Reminders": 'on run argv\n tell application "Reminders" to return (count of every list) as text\nend run',
    "Calendar": 'on run argv\n tell application "Calendar" to return (count of every calendar) as text\nend run',
}


@dataclass(frozen=True)
class Check:
    app: str
    ok: bool
    detail: str
    fix: str


FIX = ("Open System Settings → Privacy & Security → Automation and switch on {app} "
       "for your terminal, then run `juno permissions` again.")


def check(runner=None) -> list[Check]:
    runner = runner or (lambda s: run(s, timeout=PROBE_TIMEOUT, retries=0))
    out: list[Check] = []
    for app, script in PROBES.items():
        try:
            answer = runner(script)
        except Exception as e:
            out.append(Check(app, False, f"no answer ({type(e).__name__})", FIX.format(app=app)))
            continue
        out.append(Check(app, True, f"ready ({answer.strip()} items)", ""))
    return out
