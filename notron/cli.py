#!/usr/bin/env python3
"""notron — a personal agent that lives inside your Apple Notes."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

from . import graph, workspace
from .brain import Brain, BrainUnavailable


def _brain():
    try:
        return Brain.from_env()
    except BrainUnavailable as e:
        print(f"\n  {e}\n", file=sys.stderr)
        raise SystemExit(2)


def cmd_setup(args):
    print(f"\nSetting up {workspace.FOLDER} in Apple Notes\n")
    for title, state in workspace.bootstrap().items():
        print(f"  {state:8} {title}")
    print(f"\n  Open Notes → {workspace.FOLDER} → {workspace.ABOUT} and tell Notron who you are.\n")
    print("\n  Checking Notron can reach your apps…")
    cmd_permissions(args)


def cmd_permissions(args):
    from . import permissions

    print()
    for c in permissions.check():
        print(f"  {'✓' if c.ok else '✗'} {c.app:10} {c.detail}")
        if c.fix:
            print(f"    → {c.fix}")
    print()


def cmd_agenda(args):
    from . import calendar, reminders

    print(f"\n  Today\n\n{calendar.brief()}\n")
    print(f"  This week\n\n{calendar.week()}\n")
    print(f"  Outstanding\n\n{reminders.summary()}\n")


def cmd_ask(args):
    brain = _brain()
    request = " ".join(args.request)

    if args.quiet:
        # For a caller that just wants the words back — a Shortcut piping into
        # "Speak Text", say. No trace, no blank lines, no results dump.
        state = graph.run(request, brain=brain, dry_run=args.dry_run)
        print(state.answer.strip() if state.answer else "I don't have anything to say to that.")
        return

    def trace(name, state):
        if state.trace and state.trace[-1].startswith(name):
            print(f"  · {state.trace[-1]}")

    print()
    state = graph.run(request, brain=brain, dry_run=args.dry_run, on_node=trace)
    print(f"\n{state.answer or '(nothing to say)'}\n")
    for r in state.results:
        print(f"  {r}")
    print()


def cmd_plan(args):
    args.request = ["plan my week"] if args.week else ["plan my day"]
    cmd_ask(args)


def cmd_index(args):
    from . import index

    print("\n  Reading your notes…")
    stats = index.build(_brain(), on_progress=lambda m: print(f"  {m}"), force=args.rebuild)
    print(f"\n  Indexed {stats['notes']} notes "
          f"({stats['embedded']} passages embedded, {stats['reused']} already current)\n")


def cmd_care(args):
    from . import care

    brain = None if args.offline else _brain()
    signals, body, result = care.run(brain, dry_run=args.dry_run)
    print()
    for s in signals:
        mark = {"ok": "  ", "nudge": " ·", "needs you": " !"}[s.severity]
        print(f" {mark} {s.fact}")
    print(f"\n{body}\n")
    print(f"  {'\u2713' if result.ok else '\u2717'} {workspace.CARE} — {result.reason}\n")


def cmd_reflect(args):
    from . import reflect

    print()
    out = reflect.run(_brain(), dry_run=args.dry_run, on_step=lambda m: print(f"  · {m}"))
    if out.get("skipped"):
        print(f"  {out['skipped']}\n")
        return
    print(f"\n  {out['misses']} miss(es), {out['proposed']} lesson(s) proposed, "
          f"{len(out['kept'])} kept\n")
    for l in out["kept"]:
        print(f"  + {l}")
    print()


def cmd_morning(args):
    from . import daily

    print(f"\n  Notron's morning — {__import__('datetime').datetime.now():%A %d %B, %H:%M}\n")
    out = daily.morning(_brain(), dry_run=args.dry_run, on_step=lambda m: print(f"  · {m}"))
    print(f"\n{out.get('plan') or ''}\n")
    if out.get("care"):
        print("  She needs something from you:")
        for f in out["care"]:
            print(f"    ! {f}")
    print(f"\n  Open Notes → {workspace.FOLDER}\n")


def cmd_schedule(args):
    import subprocess
    from . import daily

    target = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{daily.PLIST_LABEL}.plist"
    if args.off:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{daily.PLIST_LABEL}"],
                       capture_output=True)
        target.unlink(missing_ok=True)
        print(f"\n  Notron's morning routine is off.\n")
        return

    project = pathlib.Path(__file__).resolve().parents[1]
    python = project / ".venv" / "bin" / "python"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(daily.plist(str(python), str(project), args.hour, args.minute))
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{daily.PLIST_LABEL}"],
                   capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"\n  Could not schedule: {r.stderr.strip()}\n", file=sys.stderr)
        raise SystemExit(1)
    print(f"\n  Notron will run every morning at {args.hour:02d}:{args.minute:02d}.")
    print(f"  Your Mac must be awake. Turn it off with: notron schedule --off\n")


def cmd_listen(args):
    import subprocess
    from . import watch

    label, project = watch.WATCH_LABEL, pathlib.Path(__file__).resolve().parents[1]
    target = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    if args.off:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
        target.unlink(missing_ok=True)
        print("\n  Notron has stopped listening.\n")
        return

    if args.install:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(watch.plist(str(project / ".venv" / "bin" / "python"), str(project)))
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"\n  Could not start: {r.stderr.strip()}\n", file=sys.stderr)
            raise SystemExit(1)
        print(f"\n  Notron is listening, and will keep listening after you reboot.")
        print(f"  Type into Notes → {workspace.FOLDER} → {workspace.ASK}, from any device.")
        print(f"  Stop her with: notron listen --off\n")
        return

    print()
    try:
        watch.Watcher(_brain(), on_event=lambda m: print(m, flush=True)).run_forever()
    except KeyboardInterrupt:
        print("\n  Stopped listening.\n")


def cmd_file(args):
    from . import filer

    print()
    out = filer.run(_brain(), dry_run=args.dry_run, on_step=lambda m: print(f"  · {m}"))
    for r in out.results:
        print(f"  {r}")
    print(f"\n{out.summary()}\n")


def cmd_models(args):
    for m in _brain().available_models():
        mark = " ←" if "nemotron" in m.lower() else ""
        print(f"  {m}{mark}")


def cmd_graph(args):
    print(f"\n{graph.diagram()}\n")


def main(argv=None):
    p = argparse.ArgumentParser(prog="notron", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="create the NOTRON folder and its notes").set_defaults(fn=cmd_setup)

    a = sub.add_parser("ask", help="ask Notron something")
    a.add_argument("request", nargs="+")
    a.add_argument("--dry-run", action="store_true", help="run the graph, write nothing")
    a.add_argument("--quiet", action="store_true", help="print only the answer — for scripts/Shortcuts")
    a.set_defaults(fn=cmd_ask)

    pl = sub.add_parser("plan", help="have Notron plan your day or week")
    pl.add_argument("--week", action="store_true")
    pl.add_argument("--dry-run", action="store_true")
    pl.set_defaults(fn=cmd_plan)

    li = sub.add_parser("listen", help="watch the Ask note and answer what you type")
    li.add_argument("--install", action="store_true", help="keep listening in the background, always")
    li.add_argument("--off", action="store_true", help="stop listening")
    li.set_defaults(fn=cmd_listen)

    mo = sub.add_parser("morning", help="Notron's daily routine: catch up, plan, self-check")
    mo.add_argument("--dry-run", action="store_true")
    mo.set_defaults(fn=cmd_morning)

    sc = sub.add_parser("schedule", help="have macOS run the morning routine every day")
    sc.add_argument("--hour", type=int, default=6)
    sc.add_argument("--minute", type=int, default=30)
    sc.add_argument("--off", action="store_true", help="stop the daily run")
    sc.set_defaults(fn=cmd_schedule)

    rf = sub.add_parser("reflect", help="learn from her own answers that missed")
    rf.add_argument("--dry-run", action="store_true")
    rf.set_defaults(fn=cmd_reflect)

    ca = sub.add_parser("care", help="what Notron needs from you today")
    ca.add_argument("--offline", action="store_true", help="skip the model, use plain wording")
    ca.add_argument("--dry-run", action="store_true")
    ca.set_defaults(fn=cmd_care)

    fi = sub.add_parser("file", help=f"sort {workspace.DUMP} into the right notes now")
    fi.add_argument("--dry-run", action="store_true", help="judge every line, write nothing")
    fi.set_defaults(fn=cmd_file)

    ix = sub.add_parser("index", help="teach Notron your notes (run after adding a lot)")
    ix.add_argument("--rebuild", action="store_true", help="re-embed everything from scratch")
    ix.set_defaults(fn=cmd_index)

    sub.add_parser("models", help="list models this Nebius key can run").set_defaults(fn=cmd_models)
    sub.add_parser("graph", help="show the node graph").set_defaults(fn=cmd_graph)
    sub.add_parser("permissions", help="check Notron can talk to Notes, Reminders and Calendar"
                   ).set_defaults(fn=cmd_permissions)
    sub.add_parser("agenda", help="what's in your calendar and what's still open"
                   ).set_defaults(fn=cmd_agenda)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
