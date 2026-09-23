# Channel latency — handoff (2026-09-23)

Plan: `docs/plans/2026-09-23-channel-latency.md`. Branch `feature/channel-latency`, merged to `main`.

## Measured (`scripts/profile_channel.py`, live Synqology channel, listener stopped)

| | Graph run | Notes calls | Notes time |
|---|---|---|---|
| Before (main @ 2eef69a) | 24.2 s | 45 | 8.4 s |
| After, run 1 | 15.0 s | 28 | 4.8 s |
| After, run 2 | 14.8 s | 28 | 4.9 s |

Notes answered ~0.2 s per call today (vs ~1 s when the plan was written), so the
absolute saving is smaller than the plan's estimate; the call count is the stable
number. Plus up to ~16 s less waiting before the graph starts (poll 10→3 s,
settle 12→3 s for channels) — not visible in the graph run.

Acceptance: graph ≤ 20 s ✅. Notes calls ≤ 20 ❌ (28): 7 are `retention.reconcile`
(below). End-to-end from Siri ×3: **not yet measured** — needs the phone.

## What landed

- Task 0 — `scripts/profile_channel.py`.
- Task 1 — per-request metadata memo (`executor._MEMO`, `_content_readable(reuse=True)`).
  Only checkpoints and the first executor pre-check reuse it; entries expire after
  `MEMO_TTL` = 10 s (review finding: a note renamed private mid-request otherwise
  kept feeding checkpoints). Final checks before every Notes write / EventKit
  save read fresh; policy never memoized. Checkpoint lookups: 10 → 1.
- Task 2 — `CHANNEL_POLL = 3`, `CHANNEL_SETTLE = 3`; Ask note and tags keep 12 s.
- Task 3 — `Executor._log` only queues; the listener tick delivers. One-shot CLI
  commands that write (`cli.WRITES`) deliver their own receipts on exit.
- Task 4 — watcher reads About Me by its registered id (3 calls → 2, and seeds the memo).
  **`retention.reconcile` left per request, on purpose:** the listener answers at most
  one request per tick, so per request already equals per tick; reusing the
  20-second mention sweep would delay a deletion purge by up to 4 ticks, past the
  plan's one-tick bound.
- Task 5 — skipped (optional; recommended option 2 depends on the task board, out of scope).
- Task 6 — `nodes.PREFETCH` (4 git reads + `todos`, each 0.06–0.2 s on Synqology) runs
  on a thread while Super decides on the main thread; only picked output reaches
  `state.tools`. `GIT_OPTIONAL_LOCKS=0` so `git status` never takes the user's index lock.

## Invariants walked (review by separate reviewer)

3 holds (no model near writes; memo affects metadata reads only). 4 holds as best
effort; receipts now async, CLI self-delivers. 5 holds (outbound path untouched;
unpicked tool output never enters a prompt). 9 holds (ignore is policy, re-read at
every check; `test_an_ignore_flip_mid_request_still_stops_the_write`).

## Next

Remaining time is Nemotron (project ~3 s + writer ~4 s) and reconcile (~2 s).
Measure Siri → answer end to end three times.
