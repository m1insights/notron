# P02 Tasks 3–4 final scoped re-review

Reviewed correction `088e7f9..bc44082`, its actual Git diff, `final-fix-report.md`, the updated completion flow and all seven new graph/Watcher cases. This re-review supplements the complete batch review in `final-review.md` for `8426142..088e7f9`.

**Spec compliance: approved. Quality: approved. Final whole-batch verdict (`8426142..bc44082`): approved for the requested local implementation scope.** The one remaining P2 finding is closed. No additional important findings remain from this review.

## Finding closure

Filing now marks source-tick completion only when no separate Write is owed and no filing result failed. Executor derives completion exclusively from all queued writes when any exist. Consequently an old or newly created checkpoint with earlier source success cannot conceal a failed Ask/mention reply. Valid source-only filing still completes, and partial filing stays incomplete.

The original real-graph failing probe is now permanent coverage and passes. Added cases cover both Ask and mention surfaces, saves that landed before acknowledgement failure versus saves with no matching body, ledger/payload reopen, one destination copy, one source tick, no duplicate receipt, and a single inference call. Verified saved receipts reconcile to completion; inconclusive receipts remain under review. Repeated unsuccessful background repairs now consume the retry budget and honor cooldown.

Task 4 routing is intact: recovery creation still uses `apply_creation`, failed creation still stops before its receipt, all required writes must verify before completion/RECEIPTED transitions, and no snapshot proof, consumption, historical-text transformation or filing-home permission behavior changed. The focused run includes the undo and recovery-copy graph/scanner regressions.

## Independent verification

```sh
/Users/m1labs/Dev/apps/juno/.venv/bin/python -m pytest tests/test_action_recovery.py tests/test_filing_recovery.py tests/test_undo.py tests/test_write_races.py tests/test_executor.py tests/test_nodes.py tests/test_graph.py tests/test_watch.py -o addopts='' -q
# 248 passed in 29.02s; exit 0

git diff --check 088e7f9..bc44082
# exit 0
```

The coordinator independently verified the final full suite: 889 passed in 44.03s, exit 0. All review execution used synthetic stores and temporary encrypted state. No production source edits, commits, subagents, real Apple applications, user data, providers, network, worker restart, deployment, merge or publication were performed by this reviewer.

Approval does not change the previously documented Apple non-atomic write race, conservative review for lost creation IDs, policy invalidation after approved filing-home registration, or deferred native/lifecycle/release validation gates.
