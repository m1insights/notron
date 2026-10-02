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



def _setup_failure(problem):
    """Report a setup failure to the person who ran it, and stop.

    `main()` answers a `CredentialUnavailable` with "Protected processing paused.
    Secure storage requires setup or recovery." That is the right message for an
    agent command, where nobody is watching and the detail is not actionable.
    It is useless for a command a person just typed: it hid a wrong paste from
    `key set`, and it hid the list of blocking files from `storage initialize`,
    in both cases making a fixable problem look like a dead end.
    """
    print(f"  {problem}", file=sys.stderr)
    raise SystemExit(2)


def cmd_storage(args):
    """Offline maintenance, explicitly invoked by the user; never reads Apple data."""
    from . import credentials, migration
    try:
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
    except (CredentialUnavailable, StorageError) as problem:
        _setup_failure(problem)

def _read_secret(name):
    """One line from stdin. Hidden when a person is typing, read as-is from a pipe.

    Deliberately NOT a command-line argument: argv is visible to every process on
    the machine and lands in shell history. `printf '%s' "$KEY" | notron key set
    nebius` and an interactive paste both work; neither can leak through ps.
    """
    if sys.stdin.isatty():
        import getpass
        return getpass.getpass(f"Paste {name} (input hidden): ")
    line = sys.stdin.readline()
    # Refuse a multi-line paste instead of silently keeping the first line. A
    # truncated credential stores perfectly well and then fails at the first
    # inference call, far from the cause -- the exact failure this module is
    # arranged to prevent. Found by a test that piped two lines and expected a
    # refusal: the first version quietly stored "two".
    if sys.stdin.read().strip():
        raise CredentialUnavailable('That looks like more than one line; paste only the key.')
    return line


def cmd_key(args):
    """Store, list or remove the API keys Notron uses. Secrets arrive on stdin."""
    from . import credentials
    if credentials._provider is None:
        credentials.startup()
    if args.action == 'list':
        for name, present in credentials.provisioned():
            print(f"  {'stored' if present else 'missing':8} {name}")
        return
    if not args.name:
        raise SystemExit(f"'{args.action}' needs a key name; try: notron key list")
    if args.action == 'delete':
        credentials.forget_api_key(args.name)
        print(f"Removed {args.name}.")
        return
    try:
        credentials.provision_api_key(args.name, _read_secret(args.name))
    except credentials.CredentialUnavailable as problem:
        _setup_failure(problem)
    print(f"Stored {args.name}.")


def cmd_channel(args):
    """Project channels: a note per project that every new line in is for her."""
    from . import channels, credentials, tools
    if args.action != 'list' and credentials._provider is None:
        # Saving a grant walks retention, which needs secure storage unlocked.
        credentials.startup()
    if args.action == 'list':
        found = channels.load()
        if not found:
            print("\n  No channels yet. Try: notron channel add Synqology --repo ~/Dev/apps/synqology\n")
            return
        print()
        for c in found:
            where = ", ".join(x for x in (c.repo, c.github and f"github {c.github}") if x) or "no project linked"
            hand = f" · hands work to {c.hand}" if "run" in c.allow else ""
            print(f"  {c.title:28} {where}  [{', '.join(c.allow)}]{hand}")
            if c.test:
                print(f"  {'':28} tests: {' '.join(c.test)}")
            print(f"  {'':28} tools: {', '.join(t.name for t in tools.available(c)) or 'none'}")
            if c.connectors:
                print(f"  {'':28} connectors: {', '.join(c.connectors)}")
        print()
        return
    if not args.name:
        raise SystemExit(f"'{args.action}' needs a channel name, e.g. notron channel {args.action} Synqology")
    if args.action == 'remove':
        gone = channels.remove(" ".join(args.name))
        print(f"\n  Removed {gone.title}. The note stays in Notes; she no longer answers there.\n")
        return
    if args.action == 'set':
        allow = tuple(a.strip() for a in args.allow.split(',') if a.strip()) if args.allow else None
        try:
            name = " ".join(args.name)
            test = None
            if args.test is not None:
                old = next((c for c in channels.load() if c.name.lower() == name.strip().lower()), None)
                test = _test_argv(args.test, old.repo if old else "")
            ch = channels.update(name, github=args.github, allow=allow, hand=args.hand, test=test,
                                 connect=_names(args.connect), disconnect=_names(args.disconnect))
        except channels.ChannelError as problem:
            _setup_failure(problem)
        print(f"\n  {ch.title}: [{', '.join(ch.allow)}]" + (f" · hands work to {ch.hand}" if ch.hand else "")
              + (f"\n  tests after each hand-off: {' '.join(ch.test)}" if ch.test else "")
              + (f"\n  connectors: {', '.join(ch.connectors)}" if ch.connectors else "") + "\n")
        return
    _add_channel(args)


def _names(text) -> tuple[str, ...]:
    """`--connect a,b` as names; absent or empty is none."""
    return tuple(n.strip() for n in (text or "").split(",") if n.strip())


def _test_argv(command: str, repo: str) -> tuple[str, ...]:
    """`--test ".venv/bin/python -m pytest -q"` as argv. A relative program path is
    the user's checkout's: the throwaway copy the tests run in has no `.venv`."""
    import shlex
    argv = shlex.split(command)
    if not argv:
        return ()
    if "/" in argv[0] and not argv[0].startswith("/") and repo:
        argv[0] = str(pathlib.Path(repo) / argv[0])
    return tuple(argv)


@maintenance
def _add_channel(args):
    from . import channels
    allow = tuple(a.strip() for a in (args.allow or 'read,research').split(',') if a.strip())
    if getattr(args, 'disconnect', None):
        raise SystemExit("A new channel has nothing to disconnect; use --connect, or `channel set`.")
    try:
        repo = str(pathlib.Path(args.repo).expanduser().resolve()) if args.repo else ""
        ch, state = channels.add(" ".join(args.name), repo=repo, github=args.github or "",
                                 allow=allow, hand=args.hand or "",
                                 test=_test_argv(args.test, repo) if args.test else (),
                                 connectors=_names(getattr(args, 'connect', None)))
    except channels.ChannelError as problem:
        _setup_failure(problem)
    print(f"\n  {state:8} {ch.title}  (in {workspace.FOLDER})"
          + (f"\n  connectors: {', '.join(ch.connectors)}" if ch.connectors else ""))
    print(f"\n  Say: “Hey Siri, add is CI green to my {ch.title} note.”")
    print("  Or type any line into it. She answers underneath, while `notron listen` runs.\n")


def _connect_args(rest, secrets, args=None):
    """`connect add NAME [--secret VAR ...] -- argv...`: argparse hands back
    everything after NAME, so declared secrets (and `--url`, `--bearer`,
    `--oauth`, onto `args`) are taken off the front here and the server's argv
    after `--` is kept exactly as typed."""
    rest, secrets = list(rest), list(secrets or ())
    while rest:
        if len(rest) >= 2 and rest[0] == '--secret':
            secrets.append(rest[1])
        elif args is not None and len(rest) >= 2 and rest[0] in ('--url', '--bearer'):
            setattr(args, rest[0][2:], rest[1])
        elif args is not None and rest[0] == '--oauth':
            args.oauth = True
            rest = rest[1:]
            continue
        else:
            break
        rest = rest[2:]
    if rest and rest[0] == '--':
        rest = rest[1:]
    return tuple(rest), tuple(secrets)


def cmd_connect(args):
    """MCP servers Notron may use: register, look, approve, grant, remove.

    Every step is the user's, at the terminal. Nothing here touches Notes, and
    a server's own text (tool names, descriptions) is flattened before it is
    printed, so a hostile description cannot redraw the terminal.
    """
    import json as _json
    from . import channels, connectors, credentials

    def unlock(server):
        # Only a server with secrets needs the Keychain; one without never asks.
        if (server is not None and (server.secrets or server.auth == "oauth")
                and credentials._provider is None):
            credentials.startup()

    try:
        if args.action == 'add':
            argv, secrets = _connect_args(args.argv, args.secret, args)
            if args.url:
                if argv or secrets:
                    raise connectors.ConnectorError(
                        "A URL server takes --bearer VAR or --oauth, not a command or --secret.")
                if args.bearer and args.oauth:
                    raise connectors.ConnectorError("Pick one sign-in: --bearer VAR or --oauth.")
                auth = "bearer" if args.bearer else "oauth" if args.oauth else "none"
                server = connectors.add(args.name, url=args.url, auth=auth,
                                        secrets=(args.bearer,) if args.bearer else ())
                print(f"\n  Registered {server.name}: {server.url}")
            else:
                if args.bearer or args.oauth:
                    raise connectors.ConnectorError("--bearer and --oauth are for a --url server.")
                if not argv:
                    raise connectors.ConnectorError(
                        "Which command starts the server? e.g. notron connect add time -- uvx mcp-server-time"
                        " (or --url https://… for a remote server)")
                server = connectors.add(args.name, argv, secrets)
                print(f"\n  Registered {server.name}: {' '.join(server.argv)}")
            for var in server.secrets:
                print(f"  needs {var}: notron connect secret {server.name} {var}")
            if server.auth == "oauth":
                print(f"  Sign in once: notron connect login {server.name}")
            print(f"  Next: notron connect tools {server.name}\n")
        elif args.action == 'login':
            unlock(connectors.get(args.name))
            server = connectors.login(args.name)
            print(f"\n  Signed in to {server.name}. Next: notron connect tools {server.name}\n")
        elif args.action == 'tools':
            unlock(connectors.get(args.name))
            offers = connectors.discover(args.name)
            print()
            for o in offers:
                read_only = "read-only" if o.annotations.get("readOnlyHint") is True else "changes things"
                verdict = "approvable" if o.approvable else f"not approvable: {o.why}"
                print(f"  {connectors._flat(o.name)[:64]} · {read_only} · {verdict}")
                if o.description:
                    print(f"      {connectors._flat(o.description)}")
            if not offers:
                print("  The server offered no tools.")
            print()
        elif args.action == 'approve':
            if not args.tools:
                raise connectors.ConnectorError(f"Which tools? notron connect approve {args.name} <tool> ...")
            unlock(connectors.get(args.name))
            done = connectors.approve(args.name, args.tools)
            server = connectors.get(args.name)
            print(f"\n  Approved {', '.join(connectors.qualified(server.name, t) for t in done)}.")
            print(f"  Grant it to a channel: notron channel set <project> --connect {server.name}\n")
        elif args.action == 'secret':
            server = connectors.get(args.name)
            if server is None:
                raise connectors.ConnectorError(f"No connector called {args.name}.")
            if args.var not in server.secrets:
                declared = ", ".join(server.secrets) or "none"
                raise connectors.ConnectorError(
                    f"{server.name} was not registered with {args.var} (it has: {declared}).")
            if credentials._provider is None:
                credentials.startup()
            try:
                # The registered casing: `connectors` asks for exactly this name.
                credentials.provision_api_key(f"connector.{server.name}.{args.var}", _read_secret(args.var))
            except CredentialUnavailable as problem:
                _setup_failure(problem)
            print(f"Stored {args.var} for {server.name}.")
        elif args.action == 'list':
            found = connectors.load()
            granted = {}
            for ch in channels.load():
                for name in ch.connectors:
                    granted.setdefault(name.lower(), []).append(ch.name)
            rows = [dict(name=s.name, argv=list(s.argv), url=s.url, auth=s.auth, secrets=list(s.secrets),
                         tools=sorted(s.tools), changed=list(s.changed),
                         channels=granted.get(s.name.lower(), [])) for s in found]
            if args.json:
                print(_json.dumps(rows, indent=2))
                return
            if not rows:
                print("\n  No connectors yet. Try: notron connect add time -- uvx mcp-server-time\n")
                return
            print()
            for r in rows:
                where = f"{r['url']} ({r['auth']})" if r['url'] else ' '.join(r['argv'])
                print(f"  {r['name']:16} {where}")
                print(f"  {'':16} approved: {', '.join(r['tools']) or 'none'}")
                if r['changed']:
                    print(f"  {'':16} changed since approval: {', '.join(r['changed'])}")
                if r['secrets']:
                    print(f"  {'':16} secrets: {', '.join(r['secrets'])}")
                print(f"  {'':16} channels: {', '.join(r['channels']) or 'none'}")
            print()
        elif args.action == 'remove':
            server = connectors.get(args.name)
            if server is None:
                raise connectors.ConnectorError(f"No connector called {args.name}.")
            unlock(server)  # before any grant changes: a locked Keychain changes nothing
            # Revoke the grants first: a crash after this leaves a registered
            # server no channel uses, which is inert, never a grant to a name a
            # different server could later be registered under.
            for ch in channels.load():
                if any(c.lower() == server.name.lower() for c in ch.connectors):
                    channels.update(ch.name, disconnect=(server.name,))
            gone = connectors.remove(server.name)
            print(f"\n  Removed {gone.name}, its grants and its secrets.\n")
    except (connectors.ConnectorError, channels.ChannelError) as problem:
        print(f"  {problem}", file=sys.stderr)
        raise SystemExit(1)
    except CredentialUnavailable as problem:
        # A person is at the terminal: say why, rather than main()'s generic
        # pause line. `remove` unlocks before touching any grant, so this
        # leaves the grants and the registry exactly as they were.
        _setup_failure(problem)


def cmd_tasks(args):
    """Hand-off tasks: what Nemotron briefed, what ran, what came back."""
    import json as _json
    import time as _time
    from . import credentials, handoff
    if args.action == 'fence':
        print("\n  What a coding agent's process is refused by macOS itself (no model asked):\n")
        for where, result in handoff.fence_check():
            print(f"  {where:24} {result}")
        print()
        return
    if credentials._provider is None:
        credentials.startup()
    if args.action == 'setup':
        return _tasks_setup(args)
    try:
        if args.action in ('approve', 'cancel', 'show'):
            if not args.id:
                raise SystemExit(f"Which task? notron tasks {args.action} <id>")
            task = handoff.get(args.id)
            if args.action == 'approve':
                # The digest the board showed, when it passes one; otherwise the one on record.
                task = handoff.approve(task.id, args.digest or task.digest)
            elif args.action == 'cancel':
                task = handoff.cancel(task.id)
            if args.json:
                print(_json.dumps(task.view()))
                return
            print(f"\n  {task.id[:8]}  {task.status:9} {task.channel} · {task.hand_name}")
            print(f"  goal: {task.goal}")
            for i, step in enumerate(task.brief.get('steps', []), 1):
                print(f"    {i}. {step}")
            if task.branch and task.changed:
                print(f"  branch: {task.branch} · {task.diffstat}")
            if task.output:
                print(f"  saved to: {task.output}")
            if task.review:
                print(f"  review: {task.review.get('verdict', '?')} — {task.review.get('summary', '')}")
            if task.error:
                print(f"  note: {task.error}")
            print()
            return
        tasks = handoff.all_tasks()[:args.limit]
    except handoff.TaskError as problem:
        raise SystemExit(str(problem))
    if args.json:
        print(_json.dumps([t.view() for t in tasks]))
        return
    if not tasks:
        print("\n  No tasks yet. Ask for a change in a channel that allows `run`.\n")
        return
    print()
    for t in tasks:
        age = int((_time.time() - t.created) / 60)
        print(f"  {t.id[:8]}  {t.status:9} {t.channel:14} {age:>4}m ago  {t.goal[:60]}")
    print()


def _mail_address(args):
    """Notron's own email address: a Mail account whose mail from approved senders
    becomes requests in the Notron Reminders list."""
    from . import letterbox, mail
    if args.account or args.allow:
        current = letterbox.settings()
        account = args.account or current.get("account")
        allow = ([a for a in args.allow.split(',')] if args.allow else current.get("senders", []))
        if not account:
            raise SystemExit("Which Mail account is Notron's? --account \"Notron\"")
        try:
            found = mail.accounts()
        except mail.MailError as problem:
            raise SystemExit(f"I can't read Mail from here: {problem}")
        if account not in found:
            raise SystemExit(f"Mail has no account called {account}. It has: {', '.join(found)}")
        try:
            letterbox.setup(account, allow, servers=args.server.split(',') if args.server else None,
                            to=args.to)
        except letterbox.LetterboxError as problem:
            raise SystemExit(str(problem))
    data = letterbox.settings()
    if not data.get("account"):
        print("\n  No address yet. Add one to Mail as its own account, then:"
              "\n    notron mail address --account \"Notron\" --allow you@example.com,manager@example.com\n")
        return
    where = (f"{data['to']} (in your Mail account “{data['account']}”)" if data.get("to")
             else f"the Mail account “{data['account']}”")
    print(f"\n  Notron's address: {where} — checked every {letterbox.EVERY // 60} min while `notron listen` runs.")
    print("  Who may send requests (and only when their mail server proves it was them):")
    print("".join(f"\n    ✉️  {s}" for s in data.get("senders", [])))
    print("\n  Their email becomes a request in Reminders → Notron. Nothing runs until you tick Approve.\n")


def cmd_mail(args):
    """Email to-dos: what each email asks of you, kept in Reminders until it is done."""
    from . import channels, credentials, eventkit, mail, mailroom
    if credentials._provider is None:
        credentials.startup()
    if args.action == 'status':
        import time as _time
        last = mailroom._read().get("last")
        if not last:
            print("\n  No mail pass has run yet.\n")
            return
        age = int((_time.time() - last["at"]) / 60)
        state = "list written to Notron Mail" if last["written"] else f"list NOT written: {last['reason']}"
        print(f"\n  Last pass {age} min ago, {last['hours']}h window: {last['todos']} to-dos, {state}.\n")
        return
    if args.action == 'address':
        _mail_address(args)
        return
    if args.action == 'people':
        try:
            if args.who and args.remove:
                keys = mailroom.remove_person(args.who)
            elif args.who:
                keys = mailroom.add_person(args.who)
            else:
                keys = mailroom.people()
        except mailroom.MailroomError as problem:
            raise SystemExit(str(problem))
        print("\n  Key people — their email is always read, and their to-dos come first:")
        print("".join(f"\n    ⭐ {k}" for k in keys) or "\n    (none yet: notron mail people vivek@example.com)")
        print()
        return
    if args.action == 'setup':
        try:
            found = mail.accounts()
        except mail.MailError as problem:
            raise SystemExit(f"I can't read Mail from here: {problem}")
        if args.accounts:
            wanted = [a.strip() for a in args.accounts.split(',') if a.strip()]
            missing = [a for a in wanted if a not in found]
            if missing:
                raise SystemExit(f"Mail has no account called {', '.join(missing)}. "
                                 f"It has: {', '.join(found)}")
            mailroom.choose_accounts(wanted)
        ch = mailroom.channel()
        if ch is None:
            try:
                ch, state = channels.add(mailroom.CHANNEL, allow=("research",))
            except channels.ChannelError as problem:
                raise SystemExit(str(problem))
            print(f"\n  {state.capitalize()} “{ch.title}” in {workspace.FOLDER} — your morning mail list lands here.")
        else:
            print(f"\n  “{ch.title}” is ready.")
        chosen = mailroom.chosen_accounts()
        print(f"  Reading: {', '.join(chosen) if chosen else 'every account in Mail (' + ', '.join(found) + ')'}")
        print("  Mail is only read: nothing is sent, drafted, moved or deleted.")
        try:
            found_list = mailroom.target()
        except eventkit.EventKitError as problem:
            print(f"  I can't read Reminders from here: {problem}\n")
            return
        if found_list:
            print(f"  To-dos go into the “{mailroom.LIST}” list in Reminders.")
            print("  Mark who matters: notron mail people vivek@example.com")
            print("  Try it now: notron mail\n")
        else:
            print(f"  One step left: in Reminders, make a list called “{mailroom.LIST}” (exactly one).\n")
        return
    return _mail_run(args)


@command('mail')
def _mail_run(args):
    from . import mail, mailroom
    try:
        out = mailroom.run(_brain(), dry_run=args.dry_run, hours=args.hours,
                           on_step=lambda m: print(f"  · {m}"))
    except (mailroom.MailroomError, mail.MailError) as problem:
        # Returned, not raised: inside the listener's worker a SystemExit would stop it.
        print(f"\n  {problem}\n")
        return {"written": False, "reason": str(problem)}
    print(f"\n{out['digest']}\n")
    if args.dry_run:
        print("  (dry run: no drafts saved, nothing written)\n")
    elif not out['written']:
        print(f"  The list was not written to Notes: {out['reason']}\n")
    return out


def _tasks_setup(args):
    """The Tasks note and the Reminders list: the two things the inbox needs."""
    from . import channels, eventkit, inbox
    ch = inbox.tasks_channel()
    if ch is None:
        try:
            ch, state = channels.add(inbox.TASKS, allow=("research", "run"), hand=args.hand)
        except channels.ChannelError as problem:
            raise SystemExit(str(problem))
        print(f"\n  {state.capitalize()} “{ch.title}” in 🤖 NOTRON — answers to your Reminders requests land here.")
    else:
        print(f"\n  “{ch.title}” is ready ({ch.hand or 'no agent'}; {', '.join(ch.allow)}).")
    try:
        found = inbox.target()
    except eventkit.EventKitError as problem:
        print(f"  I can't read Reminders from here: {problem}\n")
        return
    if found:
        print(f"  The “{inbox.LIST}” list in Reminders is ready.")
        print(f"  Try: “Hey Siri, remind me to draft a packing list for Lisbon in {inbox.LIST}.”\n")
    else:
        print(f"  One step left: in Reminders, make a list called “{inbox.LIST}” (exactly one).\n")


def cmd_review(args):
    """What is on hold, and the explicit way to let it go."""
    from . import credentials, executor, operations, requests
    if credentials._provider is None:
        credentials.startup()
    store = requests.current()
    held = [r for r in store.pending() if r.status == 'needs_review' and r.envelope]
    # A write whose outcome could not be proven holds its note: every later
    # write there is refused until it is settled or let go. Measured 2026-09-23:
    # one such write froze 📊 Log, another 📥 Ask Notron, for a day, and neither
    # showed up here because neither was a request.
    writes = executor.held_writes()
    if args.action == 'dismiss':
        refs = args.ids
        if not refs and not args.all:
            raise SystemExit("Which? notron review dismiss <id> …, or --all")
        chosen = held if args.all else [r for r in held if any(r.request_id.startswith(x) for x in refs)]
        gone = sum(store.dismiss(r.request_id) for r in chosen)
        dismissed = {r.request_id for r in chosen}
        ops = operations.current()
        released = sum(executor.release_held(oid) for _, oid, _ in writes
                       if args.all or any(oid.startswith(x) for x in refs)
                       or getattr(ops.get(oid), 'request_id', None) in dismissed)
        print(f"\n  Dismissed {gone + released}. Nothing was re-run; write a line again to ask again.")
        if released:
            print("  Each note's copy from before is kept — `@notron undo` there still offers it.")
        print()
        return
    if not held and not writes:
        print("\n  Nothing on hold.\n")
        return
    if held:
        print(f"\n  {len(held)} on hold — each also holds later lines in its note:\n")
        for r in held:
            where = r.envelope.reply_to[0] if r.envelope.reply_to else r.envelope.source
            text = " ".join(r.envelope.text.split())[:60]
            print(f"  {r.request_id[:8]}  {where[:24]:24}  {r.failure_code or '':20}  {text}")
    if writes:
        from . import notes
        print(f"\n  {len(writes)} write(s) I could not confirm — each stops me writing to that note:\n")
        for note_id, oid, code in writes:
            note = notes.get_note(note_id)
            print(f"  {oid[:14]:14}  {(note.title if note else 'a note')[:24]:24}  {code or ''}")
    print("\n  Let go of one: notron review dismiss <id>   ·   all: notron review dismiss --all\n")


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
        return {"answer": state.answer, "results": list(state.results)}

    def trace(name, state):
        if state.trace and state.trace[-1].startswith(name):
            print(f"  · {state.trace[-1]}")

    print()
    state = graph.run_request(envelope, brain=brain, dry_run=args.dry_run, on_node=trace)
    print(f"\n{state.answer or '(nothing to say)'}\n")
    for r in state.results:
        print(f"  {r}")
    print()
    # A queued ask from an MCP client is answered by the listener, whose stdout
    # goes nowhere; worker.execute keeps this for the producer waiting on it.
    return {"answer": state.answer, "results": list(state.results)}


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
                            extract=args.attachments)
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
    # The glyph stays out of the f-string expression: `f"{'\u2713' if …}"` is
    # PEP 701 and only parses on 3.12+, while pyproject declares a 3.11 floor.
    mark = '\u2713' if result.ok else '\u2717'
    print(f"  {mark} {workspace.CARE} — {result.reason}\n")
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


#: One-shot commands that can write. A write only queues its 📊 Log receipt
#: (the listener's tick delivers it); without a listener nothing would, so these
#: deliver their own on the way out. Best effort, like every receipt.
WRITES = frozenset({'ask', 'plan', 'morning', 'file', 'care', 'reflect', 'mail'})


def _deliver_receipts():
    try:
        from . import audit, credentials
        if credentials._provider is not None:
            audit.drain()
    except Exception:
        pass


def cmd_mcp(args):
    """Notron's Apple bridge for any MCP client. Not a worker command: a server
    lives as long as its client, and `ask_notron` takes the worker lock per call.
    Stdout belongs to the protocol, so every word here goes to stderr except the
    config block, which is the whole point of `mcp config`."""
    from . import mcp_server
    if args.action == 'config':
        print(mcp_server.config())
        return
    try:
        app = mcp_server.build(writes=args.writes, ask=not args.no_ask,
                               brain_factory=_brain, after_writes=_deliver_receipts)
    except mcp_server.SDKMissing as missing:
        print(f"  {missing}", file=sys.stderr)
        raise SystemExit(1)
    if args.writes:
        print("  notron mcp: writes are ON. ask_notron can file notes and create reminders "
              "through Notron's own checks.", file=sys.stderr)
    app.run()


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

    ch = sub.add_parser('channel', help='project channels: a note per project you can talk to, or tell Siri')
    ch.add_argument('action', choices=['add', 'list', 'set', 'remove'])
    ch.add_argument('name', nargs='*', help='the project name; the note is called "Notron <name>"')
    ch.add_argument('--repo', help='the local repository folder')
    ch.add_argument('--github', help='the GitHub repository, owner/name')
    ch.add_argument('--allow', default=None,
                    help='what she may use there: read (repo + GitHub, read-only), research (web), '
                         'run (hand an approved brief to a coding agent). Default read,research')
    ch.add_argument('--hand', choices=['claude', 'codex'], default=None,
                    help='the coding agent `run` hands work to: your own Claude Code or Codex install')
    ch.add_argument('--test', default=None,
                    help='the project\'s test command; Notron runs it after each hand-off, network off '
                         '(e.g. ".venv/bin/python -m pytest -q"; "" removes it)')
    ch.add_argument('--connect', default=None,
                    help='let Nemotron use these registered connectors (MCP servers) here, e.g. github,linear')
    ch.add_argument('--disconnect', default=None, help='stop using these connectors here')
    ch.set_defaults(fn=cmd_channel)

    cn = sub.add_parser('connect', help='MCP servers Notron may use in a channel: add, approve, remove')
    cs = cn.add_subparsers(dest='action', required=True)
    c = cs.add_parser('add', help='register a server: notron connect add time -- uvx mcp-server-time, '
                                  'or notron connect add vercel --url https://mcp.vercel.com --oauth')
    c.add_argument('name')
    c.add_argument('--url', default='', help='a remote (streamable HTTP) server instead of a command')
    c.add_argument('--bearer', default='', metavar='VAR',
                   help='with --url: send this secret as "Authorization: Bearer", e.g. GITHUB_TOKEN')
    c.add_argument('--oauth', action='store_true',
                   help='with --url: sign in through the browser, once: notron connect login <name>')
    c.add_argument('--secret', action='append', default=[],
                   help='an environment variable the server needs, e.g. GITHUB_TOKEN (repeatable); '
                        'set its value with `notron connect secret`')
    c.add_argument('argv', nargs=argparse.REMAINDER, help='after --: the command that starts the server')
    c = cs.add_parser('login', help='sign in to a --oauth server in the browser, once')
    c.add_argument('name')
    c = cs.add_parser('tools', help='what the server offers, and what v1 could approve')
    c.add_argument('name')
    c = cs.add_parser('approve', help='approve read-only tools, one by one')
    c.add_argument('name')
    c.add_argument('tools', nargs='*')
    c = cs.add_parser('secret', help="store a server's secret; the value is read from stdin, never argv")
    c.add_argument('name')
    c.add_argument('var')
    c = cs.add_parser('list', help='servers, approved tools, and the channels using them')
    c.add_argument('--json', action='store_true')
    c = cs.add_parser('remove', help='unregister a server, ungrant it everywhere, forget its secrets')
    c.add_argument('name')
    cn.set_defaults(fn=cmd_connect)

    tk = sub.add_parser('tasks', help='hand-off tasks: briefed by Nemotron, run by your coding agent')
    tk.add_argument('action', nargs='?', choices=['list', 'show', 'approve', 'cancel', 'fence', 'setup'], default='list')
    tk.add_argument('--hand', choices=['claude', 'codex'], default='claude',
                    help='setup: which of your agents writes the documents')
    tk.add_argument('id', nargs='?', help='task id (the first 6+ characters are enough)')
    tk.add_argument('--digest', help='approve only if the brief still has this digest (the task board passes it)')
    tk.add_argument('--json', action='store_true')
    tk.add_argument('--limit', type=int, default=20)
    tk.set_defaults(fn=cmd_tasks)

    ml = sub.add_parser('mail', help='email to-dos: what each email asks of you, in Reminders until done')
    ml.add_argument('action', nargs='?', choices=['run', 'setup', 'people', 'status', 'address'], default='run')
    ml.add_argument('who', nargs='?', help='people: an email address, or @domain.com')
    ml.add_argument('--remove', action='store_true', help='people: take this one off the list')
    ml.add_argument('--accounts', help='setup: only these Mail accounts, comma-separated')
    ml.add_argument('--account', help="address: the Mail account that is Notron's own address")
    ml.add_argument('--allow', help='address: who may email Notron requests, comma-separated addresses')
    ml.add_argument('--to', help='address: only mail sent to this address is for Notron, '
                                 'e.g. a plus address like you+notron@example.com in your own account')
    ml.add_argument('--server', help="address: your mail server's name in Authentication-Results "
                                     "(default mx.google.com, for Gmail / Google Workspace)")
    ml.add_argument('--hours', type=int, default=24, help='how far back to look (default 24)')
    ml.add_argument('--dry-run', action="store_true", help='decide and show, but save no drafts and write nothing')
    ml.set_defaults(fn=cmd_mail)

    rv = sub.add_parser('review', help='what is on hold, and dismissing it')
    rv.add_argument('action', nargs='?', choices=['list', 'dismiss'], default='list')
    rv.add_argument('ids', nargs='*', help='id prefixes, as `notron review` prints them')
    rv.add_argument('--all', action='store_true', help='dismiss everything on hold')
    rv.set_defaults(fn=cmd_review)

    mc = sub.add_parser('mcp', help="use your Apple Notes, Calendar and Reminders from any MCP client")
    mc.add_argument('action', choices=['serve', 'config'])
    mc.add_argument('--writes', action='store_true',
                    help='serve: let ask_notron write (through the Guard); off means a dry run')
    mc.add_argument('--no-ask', action='store_true', help='serve: offer only the read tools')
    mc.set_defaults(fn=cmd_mcp)

    from .credentials import PROVISIONABLE
    keys = sub.add_parser('key', help='store, list or remove the API keys Notron uses')
    keys.add_argument('action', choices=['set', 'list', 'delete'])
    keys.add_argument('name', nargs='?', choices=list(PROVISIONABLE),
                      help='which key; the secret itself is read from stdin, never argv')
    keys.set_defaults(fn=cmd_key)

    from .paths import DATA_DIR
    storage = sub.add_parser('storage', help='explicit offline storage setup or migration')
    storage.add_argument('action', choices=['initialize', 'migrate', 'accept'])
    storage.add_argument('--source', default=str(pathlib.Path(__file__).resolve().parents[1] / '.notron'))
    storage.add_argument('--target', default=str(DATA_DIR))
    storage.set_defaults(fn=cmd_storage)

    args = p.parse_args(argv)
    try:
        args.fn(args)
        if args.cmd in WRITES and not getattr(args, 'dry_run', False):
            _deliver_receipts()
    except (CredentialUnavailable, StorageError) as problem:
        # Generic by default: this line can land in a log, a launchd file or a
        # notification, where the detail is not actionable and may be sensitive.
        # But when a person is watching the terminal, generic is actively
        # unhelpful -- it turned a wrong paste, a stale leftover file and a
        # missing setup step into the same dead end on three separate occasions
        # while diagnosing this. Show the reason when there is someone there to
        # read it.
        print("Protected processing paused. Secure storage requires setup or recovery.", file=sys.stderr)
        if sys.stderr.isatty():
            print(f"  {problem}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
