# P02 Task 3 implementation report

Status: implementation plus review-round-1 corrections complete; ready for re-review. Worktree `production/p01-task1`,
base `8426142`. No roadmap status changed. Python/SQLite/AES-GCM with existing
AppleScript/JXA/EventKit adapters; no dependency, provider or model changes.
No real Notes, Calendar, Reminders, network, Keychain, listener or native app ran.

## Implementation

- `recovery.py` stores completed-node `State` checkpoints as encrypted immutable
  operation payloads. Dataclass-driven conversion reconstructs `Action`, `Write`,
  `Passage`, targets and context; the existing request owns its original envelope.
  Graph resumes after the last completed node. Successful router/scheduler/writer
  inference is reused rather than repeated for receipt repair. Every checkpoint
  carries the union of source/context/target provenance. Purged or unavailable
  payloads fail closed; no new identity is allocated to the interrupted request.
- A separate request-execution process/thread mutex covers graph/job admission and
  recovery, preventing a live request from being mistaken for a crashed one.
  It is separate from the Notes write transaction lock; inference never holds a
  Notes mutation lock. The OS releases the process lock on exit/crash. This does
  not implement Task 6 worker lifecycle/leases/maintenance orchestration.
- Actions receive stable `request_id:action:index` identities. Guard checks and
  source revalidation precede first application, APPLYING commits before EventKit,
  and the created item's notes contain an opaque operation reference. One exact
  reference match recovers the external ID; zero, unavailable or multiple matches
  pause at NEEDS_REVIEW without a new save. Reminder completion resolves its
  target before committing and reconciles that exact ID's completed state.
  Existing-operation calls reject conflicting payload/request/provenance.
- Reminder reconciliation includes completed reminders. Calendar reconciliation
  uses a bounded two-day window around the persisted intended start, never an
  unbounded calendar scan. Dynamic values remain data arguments to fixed JXA.
- Notes writes persist encrypted expected post-write hash evidence before APPLYING.
  Interrupted writes reconcile only an exact observed body hash, never text/title
  similarity. An inconclusive read records a review decision; later matching text
  does not automatically clear it. Verified/APPLIED writes never repeat the write.
- Filing version-2 plans persist classification and fully rendered, bound copy
  Writes (operation IDs, markdown/date/layout, target revisions and source checks),
  grouped items and contributors before copying. Stable `copy` and `source_receipt` identities allow
  replaying known suboperations. Destination copy identity must still be verifiable
  before source marking; a changed destination pauses for review. Original Brain
  Dump text remains, with only the existing validated tick/receipt additions.
- Explicit approved creation has its own persisted approval plan and `create_copy`
  identity and its exact rendered creation Write. The created body contains an
  opaque operation reference. A durably known created ID can be verified using its
  complete expected body hash. If the returned ID was lost, recovery pauses for
  review without scanning any same-title candidate bodies. New-home registration
  is deferred until both the copied source and approval line receipts are verified.
  Those receipts derive their titles from persisted approved proposal provenance;
  they do not read the newly created note as model/context input. Registration still
  triggers P01's conservative policy invalidation, after the receipts have landed.
  The invalidated parent remains a review tombstone and its purged payloads/cache
  are not repopulated. No exception to P01 policy-purge behavior was added.
- `State.receipt_complete` drives Watcher `_answer`; an action's checkmark result is
  no longer evidence its Notes receipt succeeded. Watcher has a bounded pending
  recovery pass, including requests whose own receipt hides the unanswered source
  from the scanner. Existing cooldown budgets govern repeated repair attempts.
- `audit.py` stores fixed metadata labels only, never user titles, exception text,
  model content or copied Notes text. Primary APPLIED metadata precedes audit work;
  failure to enqueue or append cannot change the primary outcome. Stable correlated
  queue IDs and bounded backfill from recent APPLIED/RECEIPTED metadata cover the
  enqueue gap. A batch attempts at most five queued deliveries. Queue and its child
  write/evidence rows/payloads are capped at 200 records and seven days. Async
  listener passes retry delivery through the ordinary guarded Notes executor.
  An uncertain Log write is reconciled conservatively, not blindly appended again.
- OperationStore schema remains 3. `prepare(require_active_request=True)` atomically
  checks the parent is still running with retained envelope before retaining new
  graph-derived payloads. This closes resurrection after a policy change that keeps
  a source readable but invalidates its parent. A checked review-reason refinement
  records an inconclusive Notes reconciliation without granting a state/replay edge.

## Tests and verification

TDD red runs reproduced missing action references/recovery, missing filing
suboperation IDs, watcher false receipt success, concurrent request takeover,
changed-source execution, conflicting-ID false success, lost audit backfill,
parent-purge content resurrection and silent later clearance of uncertain copies.
The earliest new-module imports failed before implementation; the core action and
filing harnesses were corrected to expose behavioral failures on the old code
before implementation. Audit and later API tests also failed before their features.

`tests/test_action_recovery.py` and `tests/test_filing_recovery.py`: **41 cases**.
Harnesses run the production graph/job admission, ledger and guarded executor with
fake adapters whose Apple state survives ledger reopen. Controlled BaseException
crashes cover before APPLYING, after external save, before external ID persistence,
before receipt and after receipt for reminders, events, existing-note filing and
approved creation. Additional cases cover zero/multiple reconciliation matches,
changed copy identity, source edits/revocation, cached inference reuse, real adapter
argument boundaries, durable reminder-completion target selection, Notes receipt
save errors, background completion of hidden occurrences, audit limits/expiry and
metadata-only backfill. No ledger logic is patched away.

Existing assertions updated for the intentional opaque creation reference, generic
metadata-only audits, and stronger immediate purge after final registration. The
old listener test leaked a daemon thread into later tests; it now deterministically
stops and joins its synthetic listener, preserving its retry assertion.

Final verification from the worktree:

```sh
/Users/m1labs/Dev/apps/juno/.venv/bin/python -m pytest tests -o addopts='' -q
# 839 passed in 14.36s; exit 0 (baseline 798, +41 cases)
git diff --check
# exit 0
```

## Limits / next review attention

- Exact reconciliation is conservative, not an exactly-once guarantee. Delayed
  sync, renamed/moved/edited created copies, moved events outside the bounded
  window, stripped operation references, duplicate references, native HTML
  normalization or inaccessible stores can require manual review. Apple/iCloud's
  final non-atomic read/write race is unchanged. Native JXA/Notes behavior remains
  unverified; P06 startup pause remains intact.
- Approved new-home registration deliberately retains the prior P01 policy-purge
  contract. Copy and both receipts finish before that policy change; the request
  may then remain a content-free review tombstone. No automatic regrant/reset is
  introduced. A crash after final registration therefore cannot recreate/recopy
  the purged request.
- Audit remains best effort: retention, inaccessible/changed Log content or
  inconclusive append identity can leave gaps. Queue bounds can discard old audit
  work; they never discard the primary effect's durable operation identity.
- General request/operation history compaction and automatic resolution UI remain
  Task 6, and undo snapshot lifecycle remains Task 4. Graph serialization uses
  dataclass fields so Task 4 can extend Write without a second bespoke format.
- Only local commits are intended. No merge, push, deployment or publication.


## Review round 1 correction (supersedes original recovery claims where noted)

The independent review identified three reproduced defects in `fb8db5e`; all three
were accepted and corrected. This section supersedes the original verification
count and any implication that lost creation IDs can always be reconciled.

- **R1:** JSON key sorting changed destination traversal order after reopen, while
  operation IDs had been assigned afterward by positional index. Exact copy Writes
  and destination-bound operation IDs now persist before the first effect. Recovery
  consumes them directly. Verdict indices and multi-proposal receipt order are also
  canonical, so serialization cannot change suboperation construction order.
- **R2:** The original creation-reconciliation body scan crossed P01's read boundary.
  It is removed. Only a durably recorded created ID can receive narrow verification;
  a lost returned ID requires NEEDS_REVIEW without title-candidate body reads or a
  second creation. Both unselected and explicitly ignored same-title regressions
  confirm no denied read. Original lines remain. Existing creation crash tests now
  expect conservative review after save / before returned-ID persistence, and
  successful repair where the ID was already durable.
- **R3:** Copy and approved-creation plans now retain their complete rendered Writes
  before effects, including the original date, layout, source checks and operation
  ID. Neither path calls `_entry_markdown` during replay. Next-day tests cover
  retries before APPLYING and after verified copying / before source receipts.
  Old plan payloads without the version-2 exact-write bindings pause for review;
  there is no migration that guesses their identities or rerenders their content.

Six core review regressions were first run against `fb8db5e`: **6 failed**, exposing
both R1 variants, R2 unselected/ignored reads, and R3 existing/created journal copies.
All six passed after correction. Two additional pre-APPLYING midnight variants
extend coverage. No real Apple apps, user data, provider or network calls occurred.

Verification after corrections:

```sh
/Users/m1labs/Dev/apps/juno/.venv/bin/python -m pytest tests/test_action_recovery.py tests/test_filing_recovery.py tests/test_filer.py tests/test_executor.py tests/test_write_races.py -o addopts='' -q
# 207 passed in 12.23s; exit 0
/Users/m1labs/Dev/apps/juno/.venv/bin/python -m pytest tests -o addopts='' -q
# 847 passed in 15.45s; exit 0 (+8 this review round, +49 over baseline)
git diff --check
# exit 0
```

Remaining limits are conservative review for lost creation IDs and other uncertain
Apple outcomes, the existing final non-atomic write race, P01 post-registration
purging, and deferred native validation. No known accepted review finding is left
unaddressed. Root owns subsequent independent review and combined handoff/roadmap.
