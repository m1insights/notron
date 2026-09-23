"""Where a channel reply's time goes, measured on the live Mac.

Usage: stop the background listener (`notron listen --off`; it holds the worker
lock and competes for Notes), add a line to a channel note, then run
`.venv/bin/python scripts/profile_channel.py`. Reinstall the listener after.

Prints each graph node's time, then every Apple Notes call grouped by the four
Notron frames that made it. Notes is the floor (~1 s per `note id` lookup,
measured 2026-09-23), so the call count is the number to watch.
"""
import collections
import functools
import time
import traceback

from notron import credentials

credentials.startup()

from notron import applescript, audit, graph, retention, watch  # noqa: E402
from notron.brain import Brain  # noqa: E402


def timed(name, fn):
    @functools.wraps(fn)
    def w(*a, **k):
        t = time.monotonic()
        r = fn(*a, **k)
        print(f"    [{name}] {time.monotonic() - t:.1f}s")
        return r
    return w


for k in list(graph.NODES):
    graph.NODES[k] = timed(k, graph.NODES[k])
audit.drain = timed("audit.drain", audit.drain)
retention.reconcile = timed("retention.reconcile", retention.reconcile)

_osascript = applescript._osascript
by, tm = collections.Counter(), collections.Counter()
total = [0, 0.0]


def spy(*a, **k):
    t = time.monotonic()
    r = _osascript(*a, **k)
    d = time.monotonic() - t
    st = [f for f in traceback.extract_stack()[:-1]
          if '/notron/' in f.filename and 'applescript' not in f.filename]
    key = ' < '.join(f.name for f in reversed(st[-4:]))
    by[key] += 1
    tm[key] += d
    total[0] += 1
    total[1] += d
    return r


applescript._osascript = spy
w = watch.Watcher(brain=Brain.from_credentials(), settle=0, on_event=lambda m: None)
w.check_channels()  # first sight of the line; settle=0 answers on the next look
total[:] = [0, 0.0]
by.clear()
tm.clear()
t = time.monotonic()
print("answered", w.check_channels(), f"{time.monotonic() - t:.1f}s",
      "osascript", total[0], f"{total[1]:.1f}s")
for k, v in tm.most_common(15):
    print(f"{v:6.1f}s {by[k]:3d}x  {k}")
