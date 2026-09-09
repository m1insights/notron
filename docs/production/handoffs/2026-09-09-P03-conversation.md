# P03 — conversation history and follow-ups

## Work identity

Plan: P03 Tasks 1–4, `plans/03-ask-conversation.md`.
Started from `1bbf845` on an isolated `feature/p03-conversation` worktree.
The primary checkout's `.claude/`, `notrontask.md` and egg-info edits were unrelated
and preserved. No remote push or listener startup was requested or performed.

Implemented: bounded preceding exchanges; standalone New topic; source-positioned
question parsing; consistent router/writer history; transformation without search;
resolved factual search queries; conservative clarification; encrypted action
proposals and consumed-reply recovery; exact original/target binding; actual
verified reminder references; initial-note guidance and deterministic acceptance.

## Evidence

See [case matrix and model limits](../evidence/P03-conversation-cases.md).
Baseline: **1,190 tests passed** on Python 3.14.2. Initial Python 3.11 exposed an
existing CLI syntax incompatibility, not introduced by this task.
New tests exercised real parser, graph, policy preparation, encrypted persistence
and operation transitions, with synthetic provider/Notes/EventKit boundaries.
Targeted conversation/history/help verification: **50 passed**; final clarification,
delayed-request and target-resolution verification: **105 passed**. Implementation
commits include `6c01d9a`, `d04d2d2`, `15e915e`, `797c061`, `29deca1`, `e7da1a1`,
and final safety/acceptance corrections `534dc1d`, `7a376cd` and `6acf657`. Compileall and
whitespace checks passed. Final whole-suite command
`.venv/bin/python -m pytest tests -o addopts='' -q`: **1,264 passed in 25.30s**, exit 0.
Final scoped review approved; **28 tests passed**. Legacy tagged-envelope
compatibility fixes passed **130** request/write-race/conversation tests.
All P03 checkboxes and the M1 conversation-fixture checkbox are complete; native,
live-model and release gates remain open.

Review corrections included exact title matching, omission of mixed filing receipts,
stale antecedent invalidation after deletion, source changes during context loading,
tagged full-source matching, current target snapshots, and original source binding.
Initial whole-suite failures in delayed-date ordering and legacy tagged-source
compatibility were corrected before completion.

## State and recovery

- Python and SQLite; existing AES-GCM payload storage. No dependency/model changes.
- Additive clarification/reply tables, sensitive content encrypted; hard expiry and
  bounded cleanup. Source revocation/reset purges clarification content. Operation
  identities remain durable. See [shared contract](../design.md#p03-implementation-contract-2026-09-09).
- A follow-up owns its current active lease and receipt. Original request ID plus
  clarification ID bind fulfillment, without reopening the original receipt request.
- No live Notes, Calendar, Reminders or provider endpoint was accessed. Real
  processing remains paused behind the existing secure-startup gate.
- No new backup or live rollback was performed because no live state was modified.
  Rollback must retain compatible readers or use matched offline ledger/payload/config
  backup; old code must not prune new payload references. Never reset identity to retry.
- Invariants 1–3: About Me remains read-only; replies retain existing insertion and
  preservation checks; native writes and action checks remain code-only in Executor.
  Invariant 4: operation identities and source-bound encrypted recovery remain intact.
  Invariant 5: history/resolved queries use P01 provenance and redaction; secret tests
  exercise actual transport. Invariants 6–7: events remain create-only; reminders only
  create/complete. Invariant 8: parser excludes receipts, filing mutations unchanged.
  Remaining policy/permission tests continue to gate access.

## Limits and next session

No live-model quality/latency or native Apple/iCloud acceptance was performed.
The native nontransactional write race and P06/P07 signing/startup/security gates remain.
History removal affects future context, not the encrypted recovery ledger or explicit
Memory. Thread boundaries or original source edits can invalidate pending confirmation;
asking again is intentional.

Next roadmap task: **P04 Task 1 — Shortcut feasibility**; this needs a real iPhone and
synthetic test account for device evidence. First command: `git status --short`, then
read the P04 plan and current production roadmap. Do not repeat P01–P03 or enable the
listener as part of that device investigation.
