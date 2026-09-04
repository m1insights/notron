# P02 — Reliable execution and recovery implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Interrupted work does not silently duplicate actions, overwrite new text, or disappear.
**Architecture:** Stable request/target IDs, a local durable operation ledger, revision-aware writes, explicit uncertain outcomes, and truthful worker status. Apple API writes remain nontransactional and are reconciled conservatively.
**Tech Stack:** Python 3.11+, sqlite3, pytest, existing AppleScript/JXA/EventKit adapters; P01 encrypted content storage.
**Spec:** [Shared design §§2–4, 6](../design.md).
**Dependencies:** P01 policy and secure storage contracts. No cloud service required.

## Global constraints

- Preserve user Notes and unrelated workspace changes. Tests use fake apps and temporary stores.
- No blanket exactly-once claim. Unknown external outcome becomes `needs_review`.
- Stable IDs select targets. Titles are display labels; identical wording does not uniquely identify an action.
- No automatic calendar moves/deletes or recurring actions. One active local worker; P05 adds managed account leases.

## Task 1 — Request envelopes and durable operation state

**Files:** Create `notron/requests.py`, `notron/operations.py`, `tests/test_requests.py`, `tests/test_operations.py`; Modify `notron/state.py`, `notron/graph.py`, `notron/watch.py`, `notron/mentions.py`, `notron/cli.py`, `tests/conftest.py`.
**Consumes:** Shared RequestEnvelope and OperationStatus; P01 EncryptedStore.
**Produces:** `OperationStore(path, payload_store)`, `.prepare(request_id, operation_id, payload_hash)`, `.transition(operation_id, expected, target, external_id=None)`, `.get(operation_id)`, `.pending()`; `run_request(envelope, *, brain, dry_run=False)` in graph, preserving `graph.run` as a compatibility wrapper.

- [ ] Add state-machine tests before implementation. Reopening the ledger must retain APPLIED state; duplicate operation IDs with a different payload hash are rejected.

```python
import pytest
from notron.operations import OperationStore, OperationConflict

def test_operation_identity_cannot_be_reused_for_different_work(tmp_path, payload_store):
    ledger = OperationStore(tmp_path / 'ops.sqlite3', payload_store)
    ledger.prepare('r1', 'r1:action:0', 'hash-a')
    ledger.prepare('r1', 'r1:action:0', 'hash-a')
    with pytest.raises(OperationConflict):
        ledger.prepare('r1', 'r1:action:0', 'hash-b')
```

- [ ] Implement SQL schema with request ID, operation ID primary key, payload hash, encrypted payload reference, state, timestamps, source/target ID, expected revision, external ID and failure code. Use transactions, foreign keys, WAL and durable synchronization; no plaintext note bodies in SQL metadata.
- [ ] Commit request identity before inference. Add persisted occurrence tracking keyed to note ID and observed source span/anchor. Keep the same ID while a request is pending despite unrelated edits; allocate a new ID for a newly submitted identical sentence after completion. Ambiguous copied/reordered pending occurrences ask rather than merge.
- [ ] Add test for two identical reminders submitted separately, restart before inference, changed text before execution, same text in different notes, and CLI supplied request ID. Inject temporary ledger/credential stores globally in tests.
- [ ] Run `.venv/bin/python -m pytest tests/test_requests.py tests/test_operations.py tests/test_watch.py tests/test_graph.py -q`; commit after the intended behavior passes.

## Task 2 — Stable targets and revision-aware writes

**Files:** Modify `notron/state.py`, `notron/notes.py`, `notron/executor.py`, `notron/nodes.py`, `notron/filer.py`, `notron/notedoc.py`, `tests/test_executor.py`, `tests/test_notes.py`, `tests/test_rewrite.py`; Create `tests/test_write_races.py`.
**Consumes:** Request/operation IDs and P01 final-write policy.
**Produces:** Write fields `note_id`, `expected_revision`, `operation_id`; `revision(body: str) -> str`; `Executor.apply_write(write) -> WriteResult` targeting IDs, not reselecting by title. New-note creation is a separate explicit operation.

- [ ] Add tests for duplicate titles in different folders, renaming/moving the target after classification, source deletion, and user edits while organizer inference runs.

```python
from notron.executor import revision

def test_replace_rejects_newer_user_text(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>original</div>')
    write = make_write(note_id=nid, mode='replace', expected_revision=revision('<div>original</div>'),
                       markdown='cleaned', rewrite_allowed=True)
    fake_note_store.set_body(nid, '<div>original plus new thought</div>')
    result = safe_executor.apply_write(write)
    assert not result.ok
    assert 'new thought' in fake_note_store.body(nid)
```

The three fixtures above belong in `tests/test_write_races.py`: `fake_note_store` is an in-memory ID/body map; `safe_executor` injects that store, ready policy, temporary ledger and encrypted undo; `make_write` constructs the production Write with a unique operation ID. They must not suppress policy or revision checks.

- [ ] Capture revision before model input, not when the executor starts. Validate policy and expected revision immediately before each write, under one local transaction lock. Abort stale replace/restore; rebase append/insert only when anchor matching is unambiguous. Re-read after writing, recording observed revision and unexpected divergence.
- [ ] Reject in-place rewrite of unsupported attachment/checklist/rich-object bodies. Offer a separate plain-text result without replacing the original. Do not infer attachment preservation from an HTML character-count check.
- [ ] Add final-read/final-write and post-write divergence cases. Document that remote iCloud writers do not honor the local lock and safe revision checks reduce but cannot eliminate the final race.
- [ ] Run executor/notes/rewrite/write-race suites and commit.

## Task 3 — Side effects and receipts recover independently

**Files:** Modify `notron/executor.py`, `notron/filer.py`, `notron/reminders.py`, `notron/calendar.py`, `notron/nodes.py`, `notron/watch.py`; Create `tests/test_action_recovery.py`, `tests/test_filing_recovery.py`.
**Consumes:** Durable operation store and ID-based writes.
**Produces:** Reconciliation adapters `find_by_operation(operation_id) -> list[str]` for created events/reminders; receipt-only recovery for APPLIED actions; filing suboperation records `copy` and `source_receipt`.

- [ ] Add a failpoint harness around every boundary: before APPLYING commit, after external save, before external ID persistence, before receipt, after receipt. Use a fake EventKit/Notes store whose state survives ledger reopen.
- [ ] Assert the contract through a fake action adapter:

```python
def test_receipt_failure_does_not_create_a_second_reminder(recovery_harness):
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    h.fail_at('before_receipt')
    h.run_once()
    h.restart()
    h.run_once()
    assert h.reminder_count('r1:action:0') == 1
    assert h.receipt_count('r1') == 1
```

Implement `recovery_harness` in `tests/test_action_recovery.py` using production graph/ledger with injected fake adapters. Failpoints raise controlled exceptions and never patch away ledger logic.

- [ ] Persist APPLYING before an external call; add an opaque operation reference to created reminder/event notes. On one exact reconciliation match, recover the ID. On zero/ambiguous matches after an uncertain save, stop at NEEDS_REVIEW; do not treat a delayed sync result as proof nothing was created.
- [ ] For filing, record/verifiably recognize destination copy before marking the source. On retry, repair only the missing mark. If destination changed so copy identity cannot be established, ask for review. Keep original Brain Dump lines.
- [ ] Separate `_answer` completion from `any(result.startswith('✓'))`: an action success is not proof its user receipt succeeded. Retry known suboperations, not the entire graph. Cache successful inference per pending request to avoid charging again for receipt repair.
- [ ] Make audit failures independent: a failed Log append cannot convert an applied event into an unknown event. Store local metadata first and retry audit asynchronously. Bound growth and apply P01 redaction/retention.
- [ ] Run recovery tests across all failpoints, restart each time, then existing action/filer/watch suites. Commit.

## Task 4 — Revision-bound undo with recoverable snapshots

**Files:** Modify `notron/undo.py`, `notron/executor.py`, `notron/nodes.py`, `tests/test_undo.py`, `tests/test_executor.py`.
**Consumes:** Securestore, target ID, pre-write and post-write revisions.
**Produces:** `undo.peek(note_id) -> Snapshot | None`, `undo.consume(note_id, snapshot_id) -> None`; `Snapshot(snapshot_id, before_html, after_revision, operation_id)`. Remove early destructive `pop` use.

- [ ] Write cases for failed restore, user edit after Notron, second undo, and snapshot save failure before write.

```python
def test_failed_restore_keeps_snapshot(undo_harness):
    h = undo_harness
    h.write_and_snapshot('n1', 'before', 'after')
    h.fail_restore = True
    assert not h.undo('n1').ok
    assert h.snapshot('n1') is not None
```

- [ ] Snapshot durably before changing Notes; abort a destructive write if its required backup cannot be saved. Verify live body equals the stored post-write revision before restoring. Consume only after verified restore; do not refill the slot from the undo receipt.
- [ ] If user edits intervene, offer a recovery copy requiring explicit confirmation. Never overwrite later words to satisfy an old undo request.
- [ ] Run undo/executor/rewrite tests; commit.

## Task 5 — Time, target ambiguity and fresh context

**Files:** Modify `notron/when.py`, `notron/nodes.py`, `notron/calendar.py`, `notron/reminders.py`, `notron/filer.py`, `notron/layout.py`, `notron/index.py`; Create `tests/test_delayed_requests.py`, `tests/test_target_resolution.py`; Modify `tests/test_when.py`.
**Consumes:** RequestEnvelope time/source confidence, ready policy, stable target IDs.
**Produces:** `resolve_time_context(envelope, now, resumed) -> TimeContext`; target resolvers return zero/one/many candidates instead of silently selecting the first; stale-index reads refresh selected changed notes before answering.

- [ ] Add a Friday capture processed Monday, a timezone change, DST overlap/nonexistent local time, explicit past time, date-only alarm, missing duration, mismatched calendar name and duplicate reminder names.

```python
def test_relative_date_after_resume_requires_confirmation(delayed_request):
    from notron.when import resolve_time_context
    request, monday = delayed_request(text='remind me tomorrow', captured_at=None)
    result = resolve_time_context(request, now=monday, resumed=True)
    assert result.needs_confirmation
```

- [ ] Use aware datetimes plus `zoneinfo`; never strip UTC offsets. Resolve explicit capture times against their timezone. For unknown original capture after backlog/resume, ask a precise date before creating. Label journal filing date when capture date is unknown.
- [ ] Preserve actual target IDs through completion. Unknown named list/calendar asks instead of defaulting; multiple candidates ask; bounded context identifies truncation and unavailable calendars. Date-only reminders explain they have no explicit timed alarm; event duration assumptions are confirmed. Reject unsupported recurrence/multi-action extraction rather than partially claiming success.
- [ ] Refresh/validate changed-note context at query time within a bounded read budget; if unavailable, say context is incomplete. Do not treat denied EventKit access or timed-out fetch as an empty free schedule. Add deterministic overlap checks before a new event; conflicts require confirmation tied to that exact proposed event.
- [ ] Run time/target/calendar/reminders/index tests with mocked clocks; commit.

## Task 6 — Worker lifecycle, queue health and migration

**Files:** Create `notron/health.py`, `tests/test_health.py`, `tests/test_worker_lifecycle.py`; Modify `notron/watch.py`, `notron/cli.py`, `notron/daily.py`, `notron/brain.py`, `notron/mentions.py`.
**Consumes:** Ledger and policy; P06 consumes health JSON.
**Produces:** `notron listen --status` versioned JSON with `state`, `heartbeat_at`, `last_success_at`, `pending_count`, `reason_code`; `--pause`/`--resume` with durable intent. Proposed states: starting, ready, paused, offline, permission_needed, error, stopped.

- [ ] Add tests showing a registered but dead job is not ready, locked Keychain pauses, a long provider call cannot block heartbeat, restarting preserves pending work, and duplicate local processes cannot both claim operations.

```python
def test_registered_job_with_expired_heartbeat_is_not_ready():
    from notron.health import classify
    assert classify(registered=True, heartbeat_age=120, paused=False,
                    permission_ok=True, network_ok=True) == 'error'
```

- [ ] Make heartbeat independent of serial job execution, update every five seconds and mark stale after 30 seconds. Add finite provider deadlines (30 seconds interactive, 60 seconds filing/index batches), bounded retries with jitter, persisted cooldowns and no hidden retry on uncertain side effects. Status metadata contains no user text.
- [ ] Install one worker lock for listener, CLI and morning operations; a second producer enqueues work rather than independently executing it. Sleep/resume triggers permission/network checks, time ambiguity policy and reconciliation before fresh jobs. Handle failure to prime mention scanning by retrying initialization, not permanently disabling it.
- [ ] Migrate legacy seen/filer state conservatively with backup; unknown prior effects get review, not an automatic replay of all old tagged notes.
- [ ] Run targeted lifecycle/recovery tests and `.venv/bin/python -m pytest tests -q`. Write a handoff describing schema version, migration and rollback. Commit.

**Exit gate:** Every crash boundary has a verified recovery path; known completed effects are not repeated; uncertain effects require review. Concurrent remote Notes edits remain a documented Apple-platform constraint.
