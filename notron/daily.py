"""Notron's morning routine.

One command, run by macOS at a fixed hour: catch up on anything you wrote since
yesterday, rebuild today's list, and tell you what she needs to keep working well.
The order matters — she learns your new notes first, so the plan she writes is
based on the life you actually have this morning.
"""

from __future__ import annotations

from .credentials import CredentialUnavailable
from .securestore import StorageError
from .policy import PolicyError

from datetime import datetime

from . import care, graph, index, notes, reflect, workspace


def morning(brain, *, dry_run: bool = False, on_step=None, envelope=None) -> dict:
    from .health import WorkerLock
    if not WorkerLock.owned():
        from .worker import submit
        return submit('morning', {'dry_run': dry_run},
                      lambda args: morning(brain, dry_run=args.dry_run, on_step=on_step, envelope=args._envelope))
    from . import retention
    retention.reconcile()
    def say(msg: str) -> None:
        if on_step:
            on_step(msg)

    out: dict[str, object] = {"at": datetime.now().isoformat(timespec="minutes")}

    # 0. Notes has a cold start of up to forty seconds after it has been idle.
    #    Absorb it here, where waiting costs nobody anything. Reminders and
    #    Calendar need no equivalent: EventKit reads the store directly and never
    #    wakes those apps — which is the whole reason the agenda is cheap enough
    #    to read per request.
    try:
        say(f"Notes ready in {notes.warm_up():.1f}s")
    except (CredentialUnavailable, StorageError, PolicyError):
        raise
    except Exception as e:
        say(f"Notes did not answer ({type(e).__name__}) — run `notron permissions`")

    # 1. Learn anything written since yesterday.
    if not dry_run:
        say("reading what you wrote since yesterday")
        from .brain import batch_deadline
        with batch_deadline():
            stats = index.build(brain, on_progress=say)
        out["indexed"] = stats
        say(f"learned {stats['embedded']} new passages")

    # 1.5 Learn from yesterday's own answers — the misses, if any.
    say("reflecting on yesterday's answers")
    out["reflect"] = reflect.run(brain, dry_run=dry_run, on_step=say)

    # 2. Rebuild today's list from your notes and standing instructions.
    say("rebuilding ☀️ Today")
    from . import requests
    envelope = envelope or requests.create('plan my day', source='morning')
    state = graph.run_request(envelope, brain=brain, dry_run=dry_run)
    out["today"] = state.results
    out["plan"] = state.answer

    # 3. Report her own upkeep.
    say("checking on herself")
    signals, body, result = care.run(brain, dry_run=dry_run)
    out["care"] = [s.fact for s in signals if s.severity != "ok"]
    out["care_written"] = result.ok

    say("done")
    return out


PLIST_LABEL = "io.m1labs.notron.morning"


def plist(python: str, project: str, hour: int, minute: int) -> str:
    import plistlib

    return plistlib.dumps({
        "Label": PLIST_LABEL,
        "ProgramArguments": [python, "-m", "notron", "morning"],
        "WorkingDirectory": project,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }).decode("utf-8")
