# P07 — Pilot validation and public release plan

> **For agentic workers:** Use `superpowers:executing-plans` to execute this plan task-by-task. External testing, customer contact, deployment and publication require the owner's authorization for the concrete action.

**Goal:** Decide from evidence whether Notron is safe, usable and economically viable enough for paid public use.
**Architecture:** Automated failure scenarios, real-device installation matrix, independent security review, an assisted pilot, operational rehearsals and an explicit go/no-go packet.
**Tech Stack:** Existing Python/Swift suites, signed macOS build, Apple Notes/Reminders/Calendar test accounts, PostgreSQL/Stripe/OIDC staging if managed mode is included; optional P04 Shortcut.
**Spec:** [Shared design](../design.md), [roadmap milestone gates](../README.md).
**Dependencies:** M1 core and P06 signed candidate before external pilot; P05 before claims about consumer managed onboarding or paid launch. P04 is not required for a Mac-focused pilot.

## Global constraints

- Tests must not create/modify the developer's real Notes by accident. Use a dedicated Apple test account/library and explicit fixture allowlists.
- Zero observed failures is evidence for the tested conditions, not a claim that hacking/data loss is impossible.
- No marketing claims based on a mocked model or simulated mobile integration.
- No prompts, note bodies, tokens or payment details in analytics/screenshots/support bundles by default.
- Public paid release requires an owner decision on a concrete candidate, not an automatic action after tests.

## Task 1 — Integration harness and release evidence format

**Files:** Create `tests/integration/test_fault_matrix.py`, `tests/integration/conftest.py`, `scripts/run_release_checks.py`, `docs/production/evidence/release-evidence.schema.json`, `.github/workflows/core.yml`; Modify pytest configuration only to register explicit integration markers.
**Consumes:** P01 policy fixtures, P02 failure injection, P03 conversations, P05 staging API, P06 artifact metadata.
**Produces:** Machine-readable evidence with scenario ID, artifact/version, fixture mode, timestamp, expected/observed result, severity, and command exit code.

- [ ] Keep default unit tests fake/offline. Real Apple integration requires both `--live-apple-tests` and a configured synthetic account/fixture allowlist; no code may infer permission from the existence of macOS alone. Use a separate destructive-test confirmation outside CI before clearing any fixtures.
- [ ] Add an evidence checker that refuses a release when a required case is missing, failed or merely mocked where device proof is required:

```python
def test_required_device_case_cannot_pass_with_mocked_evidence():
    from release_checks import evaluate_gate
    evidence = [{'case': 'fresh_install', 'result': 'pass', 'mode': 'mock'}]
    decision = evaluate_gate(evidence, required_device_cases={'fresh_install'})
    assert not decision.ready
```

Create `scripts/release_checks.py` as the importable evaluation module and add its test under `tests/test_release_checks.py`; `run_release_checks.py` is the CLI wrapper. The checker reports missing evidence rather than fabricating a result.

- [ ] Execute synthetic failure matrix: every operation crash boundary; no-key/no-network; corrupt policy/cache; stale/duplicate request; interrupted undo; concurrent edit; denied/write-only EventKit; duplicate targets; unknown calendar; stale index; malicious note/web content; quota boundary and tenant access.
- [ ] Run core and service suites, Swift tests and release artifact verification on their actual candidates. Preserve machine-readable logs without personal content. Commit harness and reports.

## Task 2 — Real-device behavior matrix

**Files:** Create `docs/production/evidence/device-matrix.md`, `docs/production/evidence/restore-drill.md`, `docs/production/help.md`.
**Consumes:** Dedicated macOS/iPhone test accounts, signed artifact and explicitly approved test actions.
**Produces:** Device/OS/build-tagged evidence and user-readable limits.

- [ ] Exercise macOS floor and a current supported macOS version on Apple silicon; additional supported versions need their own results. Test fresh install, upgrade, revocation/regrant, empty library, large synthetic library, duplicate accounts/folders/titles and locked/rich notes.
- [ ] Exercise same-account iCloud phone edits while Mac asleep, during model processing and after an action before receipt. Test reboot, process crash, Wi-Fi loss, Keychain unavailable and service outage. Record actual sync/response latency rather than asserting a fixed number.
- [ ] Confirm calendar creation/reminder alarms on the phone using test items; date-only reminders do not promise a timed alarm. Verify an already synced reminder still works while the Mac sleeps; a pending Notes request is visibly distinct from a completed action.
- [ ] Test one active Mac and explicit managed device transfer. BYO second-worker behavior must be described honestly; it cannot claim cross-machine protection without a coordinating service.
- [ ] Run undo/backup recovery on synthetic notes with intervening edits. Restore service backup into isolated staging and verify account/operation integrity. Record which data is deliberately retained on uninstall and account cancellation.
- [ ] Publish only verified behavior in help: sleep requirements, processing delays, cloud access, filing permissions, cancellation, unsupported calendar operations and mobile trigger limits. Commit matrix and help.

## Task 3 — Independent security and privacy review

**Files:** Create `docs/production/evidence/security-review-scope.md`, `docs/production/evidence/security-findings.md`, `docs/production/privacy-data-map.md`; Finalize `SECURITY.md` disclosure route when owner supplies it.
**Consumes:** Threat model, signed app, source/dependency manifest, sanitized staging credentials with limited scope, data-flow/retention inventory.
**Produces:** Reviewed findings with severity, remediation evidence, owner and retest state; no claimed certification unless actually obtained.

- [ ] Prepare the concrete reviewer package before requesting a paid engagement: source commit, app hash, test-account setup, permitted test scope and excluded personal systems. Owner approves the engagement and access.
- [ ] Review native permission identity, Keychain access, untrusted-content execution boundaries, state corruption/migration, update signatures, dependency supply chain, URL fetching, service authentication, account isolation, billing replay, quota races, logs and retention.
- [ ] Verify outbound provider retention/settings and clarify that encrypted transport/storage does not mean the AI provider cannot process plaintext. Record a data map for each source, destination, purpose, consent and retention period.
- [ ] Fix all critical/high security or data-loss findings before external pilot/public launch. Lower-severity findings need an explicit mitigation or acceptance tied to the release scope. Reproduce and retest fixes; no blanket “scanner passed” sign-off.
- [ ] Review public privacy/billing/support language for accuracy; any required professional review is a concrete scoped task, not a substitute for implementing protections. Commit sanitized evidence; keep exploit-sensitive details in the approved private disclosure system.

## Task 4 — Assisted Becky pilot

**Files:** Create `docs/production/pilot-guide.md`, `docs/production/evidence/pilot-results.md`, `service/notron_service/metrics.py` and `service/tests/test_metrics_privacy.py` if managed metrics are used.
**Consumes:** M2-ready candidate, owner-authorized recruitment, 8–12 consented users with Apple Notes and a regularly used Mac.
**Produces:** Two-week observations of setup, repeated value, failure recovery and paid interest. No inference of total market size from this sample.

- [ ] Prepare invitation text, consent/data explanation and task script for owner review before sending anything. Include the awake-Mac requirement prominently. Separately label phone-dominant interview participants; do not quietly count them as satisfied Mac users.
- [ ] Give each tester five realistic starter tasks: file a thought, find a prior note fact, set an explicit reminder, ask a follow-up, pause/resume. Let them attempt setup unaided first; record every assistance step rather than hiding it in onboarding success.
- [ ] Collect only opt-in event metadata: setup-step durations, action result/failure codes, filing correction count, queue latency, usage amounts and active days. Never record note text or model prompts. Verify analytics payloads against an allowlist test.

```python
def test_analytics_rejects_note_text():
    import pytest
    from notron_service.metrics import validate_event
    with pytest.raises(ValueError):
        validate_event({'name': 'file_completed', 'note_body': 'synthetic private text'})
```

- [ ] Pilot operational targets: zero observed lost-text/wrong-account/unauthorized-write incidents; no unresolved duplicate-action incidents; at least 80% complete onboarding without Terminal/developer repair; at least 90% of reviewed eligible filing suggestions accepted as correct. Denominator excludes only predeclared unsupported cases, not failures.
- [ ] Product signals: record week-two use, repeat capture behavior, willingness to pay, Mac-sleep frustration and whether the extra mobile tap is acceptable. Treat 60% week-two return as a provisional learning target, not proof of product-market fit. Capture reasons for nonreturn.
- [ ] Any severe privacy/data-loss issue pauses the affected feature/pilot, preserves evidence and initiates recovery. Do not keep running solely to finish the two-week window. Summarize findings and scope changes; commit anonymized aggregate evidence.

## Task 5 — Cost, support and incident readiness

**Files:** Create `docs/production/evidence/unit-economics.csv`, `docs/production/operations-runbook.md`, `docs/production/release-policy.md`, `scripts/export_usage_summary.py`.
**Consumes:** Actual provider invoices/usage, search/embedding costs, test subscription fees, hosting costs and support time from pilot.
**Produces:** A price/allowance recommendation, documented support ownership and rehearsed containment/recovery procedures.

- [ ] Export aggregated per-user daily usage by model/input/output/search/embedding, accounting for retries, reasoning tokens, reservations and failures. Do not infer all cost from successful answer counts.
- [ ] Model contribution at the candidate price: price minus payment fees, inference/search, allocated infrastructure and variable support. Compare median and heavy-user cost; choose a bounded included allowance. Explicitly state sample limits and fixed/variable assumptions; no invented provider price.
- [ ] Rehearse provider-key rotation, managed-service disablement, compromised account revocation, compromised release/update response and rollback. Local pause and user data access remain available when managed service is disabled. BYO users need clear local mitigation instructions because the server cannot stop independent installs.
- [ ] Prepare diagnostic export with local preview and redaction; user explicitly chooses to send it. No automatic upload of Notes or undo history. Define incident severity, owner, notification route and support response commitments the team can actually meet.
- [ ] Owner chooses production price/allowance, support commitments and billing terms after reviewing evidence. Store nonsecret approved configuration; verify it matches P05/P06 displays before release.

## Task 6 — Concrete release candidate and owner decision

**Files:** Create `docs/production/evidence/release-decision.md`; Modify roadmap checkboxes and release notes; package artifact through P06.
**Consumes:** Exact commit, app/DMG hashes, service release tag, completed evidence cases and unresolved-risk list.
**Produces:** Reviewable go/no-go packet; publication only after explicit owner approval.

- [ ] Freeze a candidate; rerun checks appropriate to changes since the last verified build. Reconcile code commit, binary hash, migration version, service contract version and signed update feed. Do not cite evidence from a different binary as if it tested this one.
- [ ] Run `python scripts/run_release_checks.py --evidence docs/production/evidence --gate public-paid`; nonzero exit when any required gate is absent/failing. The evidence checker reads structured result files, not prose keyword matches.
- [ ] Packet includes what works, supported devices/OS, sleep/mobile limitations, completed security retests, pilot metrics/costs, rollback/revocation plan and outstanding risks. Draft release notes and download/update artifacts completely before requesting publication.
- [ ] Ask the owner to approve the concrete release, production billing enablement and publication destinations. Approval is a final release action, not a prerequisite to preparing artifacts.
- [ ] After authorized deployment, verify download signature, installation, login, subscription entitlement and a synthetic round trip. If rollout fails, execute the rehearsed rollback, preserve Notes and communicate actual status. Record the final release decision and handoff.

**Exit gate:** Release evidence supports the exact promises being sold. Core safety, native installation and managed billing are each proven in their own environment; no single green test suite substitutes for the others.
