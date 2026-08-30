#!/usr/bin/env python3
"""juno — a personal agent that lives inside your Apple Notes."""

from __future__ import annotations

import argparse
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

    sub.add_parser("models", help="list models this Nebius key can run").set_defaults(fn=cmd_models)
    sub.add_parser("graph", help="show the node graph").set_defaults(fn=cmd_graph)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
