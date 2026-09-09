#!/usr/bin/env python3
"""notron — a personal agent that lives inside your Apple Notes."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

from . import graph, rewrite, workspace
from .brain import Brain, BrainUnavailable
from .credentials import CredentialUnavailable
from .securestore import StorageError
from .worker import command, maintenance


def _brain():
    try:
        from . import credentials
        if credentials._provider is None:
            credentials.startup()
        return Brain.from_credentials()
    except (BrainUnavailable, CredentialUnavailable, StorageError) as e:
        print(f"\n  {e}\n", file=sys.stderr)
        raise SystemExit(2)



def cmd_storage(args):
    """Offline maintenance, explicitly invoked by the user; never reads Apple data."""
    from . import credentials, migration
    if credentials._provider is None:
        credentials.startup()
    source, target = pathlib.Path(args.source).expanduser(), pathlib.Path(args.target).expanduser()
    if args.action == 'initialize':
        credentials.provision_storage_key(target)
        print('Secure storage initialized.')
        return
    key = credentials.storage_key()
    if args.action == 'migrate':
        report = migration.migrate(source, target, key)
        print(f"Migration {report['status']}. Originals and encrypted recovery copies preserved.")
        print('Review before storage accept, which retires both recovery copies and original caches.')
    else:
        migration.accept(source, target, key)
        print('Migration accepted. Legacy originals and recovery copies retired; approved-note rebuild required.')

@maintenance
def cmd_setup(args):
    print(f"\nSetting up {workspace.FOLDER} in Apple Notes\n")
    for title, state in workspace.bootstrap().items():
        print(f"  {state:8} {title}")
    print(f"\n  Open Notes → {workspace.FOLDER} → {workspace.ABOUT} and tell Notron who you are.\n")
    print("\n  Checking Notron can reach your apps…")
    cmd_permissions(args)


def cmd_permissions(args):
    import dataclasses, json
    from . import permissions

    checks = permissions.check()
    if getattr(args, "json", False):
        print(json.dumps([dataclasses.asdict(c) for c in checks]))
        return

    print()
    for c in checks:
        print(f"  {'✓' if c.ok else '✗'} {c.app:10} {c.detail}")
        if c.fix:
            print(f"    → {c.fix}")
    print()


def cmd_agenda(args):
    from . import calendar, reminders

    print(f"\n  Today\n\n{calendar.brief()}\n")
    print(f"  This week\n\n{calendar.week()}\n")
    print(f"  Outstanding\n\n{reminders.summary()}\n")


@command('ask')
def cmd_ask(args):
    brain = _brain()
    request = " ".join(args.request)
    from . import requests
    envelope = getattr(args, '_envelope', None) or requests.create(request, request_id=getattr(args, 'request_id', None))

    if getattr(args, 'quiet', False):
        # For a caller that just wants the words back — a Shortcut piping into
        # "Speak Text", say. No trace, no blank lines, no results dump.
        state = graph.run_request(envelope, brain=brain, dry_run=args.dry_run)
        print(state.answer.strip() if state.answer else "I don't have anything to say to that.")
        return

    def trace(name, state):
        if state.trace and state.trace[-1].startswith(name):
            print(f"  · {state.trace[-1]}")

    print()
    state = graph.run_request(envelope, brain=brain, dry_run=args.dry_run, on_node=trace)
    print(f"\n{state.answer or '(nothing to say)'}\n")
    for r in state.results:
        print(f"  {r}")
    print()


@command('plan')
def cmd_plan(args):
    args.request = ["plan my week"] if args.week else ["plan my day"]
    cmd_ask(args)


@command('index')
def cmd_index(args):
    from . import index

    print("\n  Reading your notes…")
    from .brain import batch_deadline
    with batch_deadline():
        stats = index.build(_brain(), on_progress=lambda m: print(f"  {m}"), force=args.rebuild,
                            read_attachments=args.attachments)
    print(f"\n  Indexed {stats['notes']} notes "
          f"({stats['embedded']} passages embedded, {stats['reused']} already current)\n")


@command('care')
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
    return result


@command('reflect')
def cmd_reflect(args):
    from . import reflect

    print()
    out = reflect.run(_brain(), dry_run=args.dry_run, on_step=lambda m: print(f"  · {m}"))
    if out.get("skipped"):
        print(f"  {out['skipped']}\n")
        return out
    print(f"\n  {out['misses']} miss(es), {out['proposed']} lesson(s) proposed, "
          f"{len(out['kept'])} kept\n")
    for l in out["kept"]:
        print(f"  + {l}")
    print()
    return out


@command('morning')
def cmd_morning(args):
    from . import daily

    print(f"\n  Notron's morning — {__import__('datetime').datetime.now():%A %d %B, %H:%M}\n")
    out = daily.morning(_brain(), dry_run=args.dry_run, on_step=lambda m: print(f"  · {m}"),
                        envelope=getattr(args, '_envelope', None))
    print(f"\n{out.get('plan') or ''}\n")
    if out.get("care"):
        print("  She needs something from you:")
        for f in out["care"]:
            print(f"    ! {f}")
    print(f"\n  Open Notes → {workspace.FOLDER}\n")
    return out


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

    if any(getattr(args, name, False) for name in ('status', 'pause', 'resume')):
        import json, sqlite3
        from .health import HealthStore
        try:
            store = HealthStore()
            if getattr(args, 'pause', False):
                store.set_paused(True)
            elif getattr(args, 'resume', False):
                store.set_paused(False)
            status = store.status(registered=watch.is_running())
        except (StorageError, sqlite3.DatabaseError, OSError):
            status = {'version': 1, 'state': 'error', 'reason_code': 'storage_unavailable',
                      'heartbeat_at': None, 'last_success_at': None, 'pending_count': None, 'running': False}
        print(json.dumps(status))
        return

    if args.off:
        from .health import HealthStore
        HealthStore().set_stop(True)
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
        target.unlink(missing_ok=True)
        print("\n  Stop requested. Foreground work will finish its current guarded job before exiting.\n")
        return

    if args.install:
        from .health import HealthStore
        HealthStore().set_stop(False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(watch.plist(str(project / ".venv" / "bin" / "python"), str(project)))
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"\n  Could not start: {r.stderr.strip()}\n", file=sys.stderr)
            raise SystemExit(1)
        print(f"\n  Listener registered. Check readiness with: notron listen --status")
        print(f"  Type into Notes → {workspace.FOLDER} → {workspace.ASK}, from any device.")
        print(f"  Stop her with: notron listen --off\n")
        return

    print()
    try:
        from .health import HealthStore
        HealthStore().set_stop(False)
        watch.Watcher(None, on_event=lambda m: __import__("notron.diagnostics", fromlist=["record"]).record("listener_event")).run_forever()
    except KeyboardInterrupt:
        print("\n  Stopped listening.\n")


@command('file')
def cmd_file(args):
    from . import filer, requests

    brain = _brain()
    envelope = getattr(args, '_envelope', None) or requests.create('file my brain dump', request_id=getattr(args, 'request_id', None))
    print()
    from .brain import batch_deadline
    with batch_deadline():
        outcome = requests.run_job(envelope, lambda: filer.run(brain, dry_run=args.dry_run,
                                                          on_step=lambda m: print(f"  · {m}")),
                               dry_run=args.dry_run)
    if outcome.result is None:
        print(outcome.message)
        return
    out = outcome.result
    for r in out.results:
        print(f"  {r}")
    print(f"\n{out.summary()}\n")


def cmd_library(args):
    """Which notes she may file into, and which she never reads."""
    import json
    from datetime import datetime

    from . import library, notes, policy

    if args.action == "save":
        library.save_selection(json.load(sys.stdin))
        print(json.dumps({"ok": True, "status": "ready"}))
        return
    if args.action == "recover":
        policy.restore_policy(library.STATE)
        print("Policy restored from validated backup. Review permissions with notron library.")
        return

    if args.action == "scan":
        print(json.dumps(library.scan()))
        return

    if args.action in ("peek", "open"):
        # Both serve the Mac app's preview panel: peek fills it, open is the
        # escape hatch to the real note. Ignored notes included on purpose —
        # the user is looking, not the model.
        if not args.note:
            print(f"\n  Which note? notron library {args.action} <note id>\n")
            raise SystemExit(1)
        if args.action == "peek":
            print(json.dumps(library.peek(args.note, reveal=args.reveal)))
        else:
            notes.show_note(args.note)
            print(json.dumps({"ok": True}))
        return

    if args.reset:
        library.save(library.Library(), reset=True)
        print("\n  Reset — no notes are readable and no filing homes are selected. Run setup and select notes again.\n")
        return

    lib = library.load()
    live: list | None = None

    def resolve(ref: str) -> str:
        nonlocal live
        if live is None:
            live = [n for n in notes.list_all_notes() if n.folder != workspace.FOLDER]
        if any(n.id == ref for n in live):
            return ref
        hits = [n for n in live if n.title == ref]
        if len(hits) == 1:
            return hits[0].id
        if not hits:
            print(f"\n  No note called {ref!r}.\n")
            raise SystemExit(1)
        print(f"\n  {len(hits)} notes are called {ref!r} — say which by id:")
        for n in hits:
            print(f"    {n.id}   ({n.folder}, edited {n.modified})")
        print()
        raise SystemExit(1)

    changed = False
    for ref in args.home:
        nid = resolve(ref); lib.decided.add(nid); lib.homes.add(nid); lib.ignore.discard(nid); changed = True
    for ref in args.ignore:
        nid = resolve(ref); lib.decided.add(nid); lib.ignore.add(nid); lib.homes.discard(nid); changed = True
    for ref in args.read:
        nid = resolve(ref); lib.decided.add(nid); lib.homes.discard(nid); lib.ignore.discard(nid); changed = True
    if args.start_from:
        try:
            lib.start_from = library.parse_start(args.start_from)
        except ValueError as e:
            print(f"\n  {e}\n"); raise SystemExit(1)
        changed = True
    if changed:
        lib.chosen_at = datetime.now().isoformat(timespec="minutes")
        library.save(lib)

    out = library.scan(lib)
    c = out["counts"]
    since = f" · start from {out['start_from']}" if out["start_from"] else ""
    print(f"\n  {c['home']} homes · {c['read']} read only · {c['ignore']} ignored{since}")
    if out["status"] == "corrupt":
        print("  Policy corrupt — AI paused. Use notron library recover or --reset.")
    elif not out["configured"]:
        print("  (nothing chosen yet — these are her guesses; open the app or pass --home/--ignore)")
    print()
    for row in out["notes"]:
        if row["state"] == "home":
            print(f"  \u2302 {row['title']}")
    print()


def cmd_pins(args):
    """Which of her notes to pin in Apple Notes, and why.

    She cannot pin them herself — Notes exposes no `pinned` property to any
    script — so this command names them and the user Control-clicks. The
    Mac app reads `--json`; a person reads the plain list.
    """
    import json

    rows = workspace.pin_guide()
    if args.json:
        print(json.dumps(rows))
        return

    print("\n  Pin these in Notes and they'll sit above everything else —")
    print("  in her folder and in All iCloud. Control-click a note → Pin Note.\n")
    for row in rows:
        mark = "★" if row["suggested"] else " "
        print(f"  {mark} {row['title']} — {row['why']}")
    print("\n  ★ = start with these three.\n")


def cmd_rewrite(args):
    """Where a brand-new note starts: ask each time, always clean up in place, or never."""
    if args.recover:
        rewrite.restore_permissions()
        print("Rewrite permissions restored from validated backup.")
        return
    rewrite.set_default_for_new_notes(args.default)
    print(f"\n  New notes will default to: {args.default}\n")


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
    a.add_argument("--request-id", help="stable caller ID for this request; reuse only for retries")
    a.set_defaults(fn=cmd_ask)

    pl = sub.add_parser("plan", help="have Notron plan your day or week")
    pl.add_argument("--week", action="store_true")
    pl.add_argument("--dry-run", action="store_true")
    pl.set_defaults(fn=cmd_plan)

    li = sub.add_parser("listen", help="watch the Ask note and answer what you type")
    control = li.add_mutually_exclusive_group()
    control.add_argument("--install", action="store_true", help="keep listening in the background, always")
    control.add_argument("--off", action="store_true", help="stop listening")
    control.add_argument("--status", action="store_true", help="worker health (versioned JSON)")
    control.add_argument("--pause", action="store_true", help="persistently pause admission after current work")
    control.add_argument("--resume", action="store_true", help="resume after readiness checks")
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
    fi.add_argument("--request-id", help="stable caller ID for this filing request")
    fi.set_defaults(fn=cmd_file)

    ix = sub.add_parser("index", help="teach Notron your notes (run after adding a lot)")
    ix.add_argument("--rebuild", action="store_true", help="re-embed everything from scratch")
    ix.add_argument("--attachments", action="store_true",
                    help="also look at pictures and listen to recordings (costs a "
                         "vision call per picture; without it only what she has "
                         "already read is indexed)")
    ix.set_defaults(fn=cmd_index)

    lb = sub.add_parser("library", help="which notes she may file into, and which she never reads")
    lb.add_argument("action", nargs="?", choices=["show", "scan", "peek", "open", "save", "recover"], default="show",
                    help="scan = JSON for the Mac app; peek = one note's text; open = show it in Notes")
    lb.add_argument("note", nargs="?", metavar="NOTE_ID", help="the note peek/open acts on")
    lb.add_argument("--home", action="append", default=[], metavar="TITLE_OR_ID",
                    help="a note she may file lines into (repeatable)")
    lb.add_argument("--ignore", action="append", default=[], metavar="TITLE_OR_ID",
                    help="a note she must never read (repeatable)")
    lb.add_argument("--read", action="append", default=[], metavar="TITLE_OR_ID",
                    help="readable; explicit tagged replies allowed, never automatic filing")
    lb.add_argument("--start-from", metavar="YEAR", help="ignore notes last edited before this, e.g. 2026")
    lb.add_argument("--reveal", action="store_true",
                    help="with peek: show a note even if its body looks like credentials")
    lb.add_argument("--reset", action="store_true", help="explicitly clear permissions; zero readable notes and zero homes")
    lb.set_defaults(fn=cmd_library)

    pn = sub.add_parser("pins", help="which of her notes to pin in Apple Notes")
    pn.add_argument("--json", action="store_true", help="JSON for the Mac app")
    pn.set_defaults(fn=cmd_pins)

    rw = sub.add_parser("rewrite", help="how new notes handle 'clean this up' by default")
    rw_choice = rw.add_mutually_exclusive_group(required=True)
    rw_choice.add_argument("--recover", action="store_true", help="explicitly restore validated rewrite backup")
    rw_choice.add_argument("--default", choices=rewrite.DEFAULTS,
                    help="ask each time / always clean it up in place / never")
    rw.set_defaults(fn=cmd_rewrite)

    sub.add_parser("models", help="list models this Nebius key can run").set_defaults(fn=cmd_models)
    sub.add_parser("graph", help="show the node graph").set_defaults(fn=cmd_graph)
    pe = sub.add_parser("permissions", help="check Notron can talk to Notes, Reminders and Calendar")
    pe.add_argument("--json", action="store_true", help="machine-readable output for the Mac app")
    pe.set_defaults(fn=cmd_permissions)
    sub.add_parser("agenda", help="what's in your calendar and what's still open"
                   ).set_defaults(fn=cmd_agenda)

    from .paths import DATA_DIR
    storage = sub.add_parser('storage', help='explicit offline storage setup or migration')
    storage.add_argument('action', choices=['initialize', 'migrate', 'accept'])
    storage.add_argument('--source', default=str(pathlib.Path(__file__).resolve().parents[1] / '.notron'))
    storage.add_argument('--target', default=str(DATA_DIR))
    storage.set_defaults(fn=cmd_storage)

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except (CredentialUnavailable, StorageError):
        print("Protected processing paused. Secure storage requires setup or recovery.", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
