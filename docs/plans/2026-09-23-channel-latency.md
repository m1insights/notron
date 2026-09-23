# Channel latency — plan

**2026-09-23.** For a fresh session. Goal: a line dictated into a project channel
("Hey Siri, add *is CI green* to my Notron Synqology note") gets its full answer
in **15–25 s**, down from ~60–120 s today, without weakening a safety check. That
number decides whether the demo reads as a product or a prototype.

Run it through `notrontask.md` (worktree → plan → TDD → review → merge). Read
`CLAUDE.md` first, especially Invariants 3–5 and the P02 notes under "Apple Notes
limits": several steps below touch the write path.

## 1. Where the time goes (measured 2026-09-23, live, main @ b787c4d)

End to end, speech → answer in the note: **~1.5–2 min** (Siri line at ~11:24:30,
reply landed 11:26:10).

| Slice | Time | Why |
|---|---|---|
| iCloud phone → Mac | a few s | Not ours. Measure it once (Task 0). |
| Listener notices the line | 0–10 s | `watch.CHANNEL_POLL = 10` |
| Waits for typing to settle | 12 s | `watch.SETTLE = 12`, applied via `Watcher._settled` in `check_channels`. A Siri line arrives whole. |
| Graph, one reply (`prof3.py` below) | **~47 s** | of which **~34 s is 46 Apple Notes calls**; models ~6 s |

Inside the ~47 s graph run (after today's two perf commits `1f7db60`, `d2db248`):

| Caller | Calls | Time |
|---|---|---|
| `get_note < Executor._content_readable < recovery.put < recovery.checkpoint` | 10 | 11.3 s |
| `get_note < _content_readable < Executor._locked_write` | 7 | 8.0 s |
| `get_note < Executor._locked_write` | 3 | 3.0 s |
| `get_note < _content_readable < put < _locked_write` | 2 | 2.2 s |
| `retention.reconcile` (`list_all_notes`, 6 folders) | 6 | ~2 s |
| `watcher` node (About Me find + read) | — | 3.2 s |
| `audit.drain` inside the executor's `_log` | — | ~3.6 s |
| `project` node (Nemotron Super decision + tools) | — | ~3 s |
| `writer` node (Nemotron Super) | — | 3–7 s |

**The floor is Apple Notes: ~1 s per `note id` lookup**, and batching barely
helps. Four metadata reads took 4.9 s singly and 3.7 s in one batched script.
Fewer lookups is the only real lever. Nemotron is not the problem; do not
spend time on model tiers here.

Context from earlier: Nano on Nebius is unreliable (2.3 s, or a 30 s timeout that
puts the whole provider into cooldown), so channels already skip it (`9198a9a`).
Do not reintroduce a Nano call on the channel path.

## 2. Tasks, in order

### Task 0 — a repeatable measurement (~30 min)

Commit a profiler as `scripts/profile_channel.py`. It is the one below, taken from
this session's scratchpad. Record the before numbers in the handoff, then run it
after every task. **Stop the background listener first** (`notron listen --off`):
it holds the worker lock and competes for Notes. Reinstall at the end
(`notron listen --install`).

```python
# Usage: add a line to the channel note, then: .venv/bin/python scripts/profile_channel.py
import time, functools, traceback, collections
from notron import credentials
credentials.startup()
from notron.brain import Brain
from notron import watch, graph, audit, applescript, retention
def timed(name, fn):
    @functools.wraps(fn)
    def w(*a, **k):
        t = time.monotonic(); r = fn(*a, **k); print(f"    [{name}] {time.monotonic()-t:.1f}s"); return r
    return w
for k in list(graph.NODES): graph.NODES[k] = timed(k, graph.NODES[k])
audit.drain = timed("audit.drain", audit.drain)
retention.reconcile = timed("retention.reconcile", retention.reconcile)
o = applescript._osascript; by = collections.Counter(); tm = collections.Counter(); total = [0, 0.0]
def spy(*a, **k):
    t = time.monotonic(); r = o(*a, **k); d = time.monotonic() - t
    st = [f for f in traceback.extract_stack()[:-1] if '/notron/' in f.filename and 'applescript' not in f.filename]
    key = ' < '.join(f.name for f in reversed(st[-4:])); by[key] += 1; tm[key] += d
    total[0] += 1; total[1] += d; return r
applescript._osascript = spy
w = watch.Watcher(brain=Brain.from_credentials(), settle=0, on_event=lambda m: None)
w.check_channels()
t = time.monotonic(); print("answered", w.check_channels(), f"{time.monotonic()-t:.1f}s",
                           "osascript", total[0], f"{total[1]:.1f}s")
for k, v in tm.most_common(15): print(f"{v:6.1f}s {by[k]:3d}x  {k}")
```

Also measure iCloud once: time from speaking to the line appearing in the Mac's
note (`osascript` poll of the channel body every 1 s).

### Task 1 — verify each source note twice, not at every step (~25 s saved, ~3 h)

Today `recovery.put` (every checkpoint) and `_locked_write` (several times per
write) each call `Executor._content_readable(sources)`, which calls
`notes.get_note` for **every** contributing note. Sources for a channel reply are
About Me and the channel note, so that is 2 lookups × ~11 checks.

Change: a **per-request metadata memo** for `_content_readable`, scoped to one
`graph.run_request` call (a `ContextVar`, set and cleared in `run_request`).
- Checkpoints (`recovery.put`) may reuse a memoized `Note` read earlier **in the
  same request**.
- **The executor's final check before `notes.write_body` must bypass the memo
  and read fresh.** That is the check that makes Invariant 3 and the P02 "checks
  policy/revision and all contributing source permissions after final reads"
  claim true. Keep it exactly as strong as today.
- Policy (`policy.current()`) is always re-read; only the Notes metadata is memoized.
- An `ignore` flip or deletion mid-request must still stop the write. Write that
  test first: memo populated → note ignored → the write refuses.

Review gate: this touches the write path. Walk Invariants 3, 4, 5 and 9 by number
in the review and state how each still holds. Get a separate
`superpowers:code-reviewer` pass focused on "can a revoked source still reach a
write?"

### Task 2 — channels settle fast and are checked often (~15 s saved, ~1 h)

- `CHANNEL_SETTLE = 3` for channel keys (`chan:`), passed as `settle=` to
  `_settled` in `check_channels`. Keep `SETTLE = 12` for the Ask note and tags,
  where people type in pieces. Typed channel lines rarely span more than 3 s of
  pause; if a half-typed line gets answered, the user adds a line, so the cost is small.
- `CHANNEL_POLL = 3`. One body read per channel per poll. With 2–3 channels that
  is fine; if `notron channel list` grows past ~5, poll only channels whose
  `modification date` changed. The bulk `list_notes(FOLDER)` already returns
  modified stamps in one call; use that instead of N body reads.
- Tests: a channel line answers after 3 s, an Ask-note line still waits 12 s.

### Task 3 — move the 📊 Log receipt off the reply path (~4 s, ~1 h)

`Executor._log` calls `audit.drain(limit=1)` inline, which is a full Log-note
append (~3.6 s) before the reply is reported done. Enqueue only in `_log`; let
the listener's idle `audit.drain()` in `tick` deliver it. Invariant 4 already says
receipts are best-effort and asynchronous, so this matches the contract. Test:
a successful channel write returns without calling `drain`, and the queued
receipt lands on the next tick.

### Task 4 — cheaper per-request fixed costs (~3–5 s, ~1–2 h)

- `retention.reconcile()` runs `list_all_notes()` (6 folder reads) at the start
  of every `run_request`. Check whether it can reuse the listener's latest sweep
  result or run once per tick instead of once per request, without delaying a
  revocation by more than one tick. If not provably safe, leave it and say why.
- `watcher` node: About Me is found by listing her folder and then read. Cache its
  note id from `policy.system_notes` (already registered) and read by id.

### Task 5 — optional: an instant "On it" (perceived latency, ~½ day)

Only after 1–4 land and are measured. Goal: something visible in the note within
~10 s. Constraint that makes this non-trivial: `conversation.unanswered` treats
any signed `**Notron:**` turn after a line as the answer, so an ack turn marks
the question answered, and the real reply must then land **inside or directly
after the ack** without the Guard seeing a rewrite (channels are append/insert
only). Design options for that session to weigh:
1. The ack is her turn ("*On it — checking git_log…*"), and the final answer is a
   second insert directly under it, anchored to the ack text.
2. No Notes ack; show progress on the Mac task board (step 7) and via a short
   Siri reply instead.

Recommend 2 unless 1 is clean. A second write costs another ~5 s of Notes time.

### Task 6 — run the likely tools while Nemotron decides (~1–2 s, ~2 h)

Today `nodes.project` runs in series: Super decides (~2.5 s), then the chosen
tools run (`tools.run`, ~0.1–1 s each, `gh_*` slower because they go over the
network). The tools are read-only and fixed-argv, so starting them early is safe.

Change, inside `nodes.project`:
- Before the Super call, start a background thread that runs the channel's
  **cheap local tools** (`git_status`, `git_log`, `git_branches`,
  `git_diff_stat`) via `tools.run`. Measure `todos` (`git grep`) and the `gh_*`
  tools on the real Synqology folder and include them only if each takes under ~1 s.
- The Super call stays on the **main thread**. `brain._deadline_guard` uses
  `SIGALRM` and refuses to run off the main thread, so only the subprocess work
  moves to the thread.
- After Super decides, use prefetched output **only for the tools Super picked**.
  Run any picked tool that wasn't prefetched as today. Discard everything else:
  unpicked output must never reach the writer's prompt. Otherwise the decision
  stops meaning anything, and the prompt grows.
- If the prefetch thread fails or is still running when Super answers, join it
  with a short timeout (≤ 2 s), then fall back to running the picks serially.
  The receipt line ("checked … · decided by Nemotron Super in N s") is
  unchanged. Nemotron still makes and signs the decision.
- Tests, all with `tools._exec` faked: (1) a picked tool that was prefetched is
  not run twice; (2) an unpicked prefetched output is absent from `state.tools`;
  (3) a prefetch error falls back to a serial run; (4) the brain is called on the
  main thread.

Jev (TypeSafe's decision model) was considered for this slot and parked. It
would replace or pre-empt the Nemotron decision, the rubric's scored path, and
add a third provider. Revisit only as a speculative prefetch hint if this task
still leaves the decision step as the largest remaining slice.

## 3. Acceptance

- `scripts/profile_channel.py` on a live channel: graph run **≤ 20 s**, Notes
  calls **≤ 20**, recorded in the handoff with before/after.
- End to end from Siri: **≤ 30 s**, measured three times, median reported.
- Full suite green; every new behaviour has a test named after the measurement
  that motivated it (repo convention).
- Invariants 3, 4, 5, 9 walked by number in the review.
- Background listener reinstalled and `notron listen --status` shows `ready`.

## 4. Out of scope

Model/tier changes, Jev (see Task 6), OpenShell, the Claude/Codex hand-off (step 6), the task
board (step 7). Do not raise `MAX_BODY_CHARS` or touch the picture guard.
