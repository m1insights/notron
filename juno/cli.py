#!/usr/bin/env python3
"""juno — a personal agent that lives inside your Apple Notes."""

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
    print(f"\n  Open Notes → {workspace.FOLDER} → {workspace.ABOUT} and tell Juno who you are.\n")
    print("\n  Checking Juno can reach your apps…")
    cmd_permissions(args)


def cmd_permissions(args):
    from . import permissions

    print()
    for c in permissions.check():
        print(f"  {'✓' if c.ok else '✗'} {c.app:10} {c.detail}")
        if c.fix:
            print(f"    → {c.fix}")
    print()


def cmd_ask(args):
    brain = _brain()
    request = " ".join(args.request)

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


def cmd_morning(args):
    from . import daily

    print(f"\n  Juno's morning — {__import__('datetime').datetime.now():%A %d %B, %H:%M}\n")
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
        print(f"\n  Juno's morning routine is off.\n")
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
    print(f"\n  Juno will run every morning at {args.hour:02d}:{args.minute:02d}.")
    print(f"  Your Mac must be awake. Turn it off with: juno schedule --off\n")


def cmd_listen(args):
    import subprocess
    from . import watch

    label, project = watch.WATCH_LABEL, pathlib.Path(__file__).resolve().parents[1]
    target = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    if args.off:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
        target.unlink(missing_ok=True)
        print("\n  Juno has stopped listening.\n")
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
        print(f"\n  Juno is listening, and will keep listening after you reboot.")
        print(f"  Type into Notes → {workspace.FOLDER} → {workspace.ASK}, from any device.")
        print(f"  Stop her with: juno listen --off\n")
        return

    print()
    try:
        watch.Watcher(_brain(), on_event=lambda m: print(m, flush=True)).run_forever()
    except KeyboardInterrupt:
        print("\n  Stopped listening.\n")


def cmd_models(args):
    for m in _brain().available_models():
        mark = " ←" if "nemotron" in m.lower() else ""
        print(f"  {m}{mark}")


def cmd_graph(args):
    print(f"\n{graph.diagram()}\n")


def main(argv=None):
    p = argparse.ArgumentParser(prog="juno", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="create the JUNO folder and its notes").set_defaults(fn=cmd_setup)

    a = sub.add_parser("ask", help="ask Juno something")
    a.add_argument("request", nargs="+")
    a.add_argument("--dry-run", action="store_true", help="run the graph, write nothing")
    a.set_defaults(fn=cmd_ask)

    pl = sub.add_parser("plan", help="have Juno plan your day or week")
    pl.add_argument("--week", action="store_true")
    pl.add_argument("--dry-run", action="store_true")
    pl.set_defaults(fn=cmd_plan)

    li = sub.add_parser("listen", help="watch the Ask note and answer what you type")
    li.add_argument("--install", action="store_true", help="keep listening in the background, always")
    li.add_argument("--off", action="store_true", help="stop listening")
    li.set_defaults(fn=cmd_listen)

    mo = sub.add_parser("morning", help="Juno's daily routine: catch up, plan, self-check")
    mo.add_argument("--dry-run", action="store_true")
    mo.set_defaults(fn=cmd_morning)

    sc = sub.add_parser("schedule", help="have macOS run the morning routine every day")
    sc.add_argument("--hour", type=int, default=6)
    sc.add_argument("--minute", type=int, default=30)
    sc.add_argument("--off", action="store_true", help="stop the daily run")
    sc.set_defaults(fn=cmd_schedule)

    ca = sub.add_parser("care", help="what Juno needs from you today")
    ca.add_argument("--offline", action="store_true", help="skip the model, use plain wording")
    ca.add_argument("--dry-run", action="store_true")
    ca.set_defaults(fn=cmd_care)

    ix = sub.add_parser("index", help="teach Juno your notes (run after adding a lot)")
    ix.add_argument("--rebuild", action="store_true", help="re-embed everything from scratch")
    ix.set_defaults(fn=cmd_index)

    sub.add_parser("models", help="list models this Nebius key can run").set_defaults(fn=cmd_models)
    sub.add_parser("graph", help="show the node graph").set_defaults(fn=cmd_graph)
    sub.add_parser("permissions", help="check Juno can talk to Notes, Reminders and Calendar"
                   ).set_defaults(fn=cmd_permissions)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
