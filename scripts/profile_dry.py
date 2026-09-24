"""Where a reply's time goes, measured live — and nothing is written.

Usage (stop the background listener first: `notron listen --off`; reinstall after):
  .venv/bin/python scripts/profile_dry.py ask "what supplements am I taking right now?"
  .venv/bin/python scripts/profile_dry.py channel Synqology "what branch am I on?"
  .venv/bin/python scripts/profile_dry.py tick 4

`ask` and `channel` walk the whole graph with dry_run=True (every read and model
call is real; the executor stops before the write). `channel` answers as if the
line were in that channel, so the executor refuses at the anchor — after its
checks, which is what is being timed. `tick` runs idle listener ticks.

Prints each node, then Apple Notes calls grouped by caller, and how many times
the Keychain helper was spawned. Numbers from 2026-09-23 are in
docs/production/handoffs/2026-09-23-e2e-latency.md.
"""
import collections
import functools
import sys
import time
import traceback

from notron import credentials

credentials.startup()

from notron import applescript, graph, retention  # noqa: E402
from notron.brain import Brain  # noqa: E402

where = ['-']
calls, spent = collections.Counter(), collections.Counter()
spawned = [0]


def timed(name, fn):
    @functools.wraps(fn)
    def w(*a, **k):
        prev, where[0] = where[0], name
        t = time.monotonic()
        try:
            return fn(*a, **k)
        finally:
            print(f"    [{name}] {time.monotonic() - t:.2f}s")
            where[0] = prev
    return w


for k in list(graph.NODES):
    graph.NODES[k] = timed(k, graph.NODES[k])
retention.reconcile = timed("reconcile", retention.reconcile)

_osascript = applescript._osascript


def spy(*a, **k):
    t = time.monotonic()
    r = _osascript(*a, **k)
    st = [f for f in traceback.extract_stack()[:-1]
          if '/notron/' in f.filename and 'applescript' not in f.filename]
    key = where[0] + ' | ' + ' < '.join(f.name for f in reversed(st[-3:]))
    calls[key] += 1
    spent[key] += time.monotonic() - t
    return r


applescript._osascript = spy
_request = credentials.KeychainStore._request


def counted(self, *a, **k):
    spawned[0] += 1
    return _request(self, *a, **k)


credentials.KeychainStore._request = counted


def report(label, started):
    print(f"{label} {time.monotonic() - started:.2f}s · Notes calls {sum(calls.values())} "
          f"({sum(spent.values()):.2f}s) · Keychain helper {spawned[0]}")
    for k, v in spent.most_common(10):
        print(f"   {v:5.2f}s {calls[k]:3d}x  {k}")


mode = sys.argv[1]
if mode == 'tick':
    from notron import watch
    from notron.health import HealthStore, WorkerLock
    HealthStore().set_stop(False)
    w = watch.Watcher(brain=Brain.from_credentials(), on_event=lambda m: None)
    with WorkerLock() as lock:
        if not lock.acquired:
            sys.exit("The listener is running: `notron listen --off` first.")
        for i in range(int(sys.argv[2]) if len(sys.argv) > 2 else 4):
            calls.clear(); spent.clear(); spawned[0] = 0
            t = time.monotonic()
            w.tick()
            report(f"tick {i}", t)
            time.sleep(w.ask_poll)
    HealthStore().set_stop(True)
else:
    brain = Brain.from_credentials()
    t = time.monotonic()
    if mode == 'ask':
        state = graph.run(sys.argv[2], brain=brain, dry_run=True)
    else:
        from notron import channels, policy, workspace
        ch = next(c for c in channels.load() if c.name == sys.argv[2])
        with policy.explicit_reply(ch.note_id):
            state = graph.run(sys.argv[3], brain=brain, trigger='notes', dry_run=True,
                              reply_to=(ch.title, workspace.FOLDER, 0),
                              source=sys.argv[3], source_note_id=ch.note_id)
    report("graph", t)
    print("\n".join(state.trace))
