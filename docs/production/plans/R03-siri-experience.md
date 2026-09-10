# R03 — Siri and the task experience Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkboxes; do not mark a device/provider gate complete with mocks.

**Goal:** Make Notron useful through Siri without requiring Siri to stay open for long-running work.

**Architecture:** Reuse P06 async process transport and the R01 task controller. Stable task entities expose start/status/cancel; Mac UI handles scoped approval, connection setup and detailed results, with guarded Notes delivery.

**Tech Stack:** Swift 5.10+/SwiftUI/App Intents, Python CLI/task controller, existing Apple Notes executor.

**Spec:** [Repositioning design](../repositioning-design.md), [existing production contracts](../design.md), [active roadmap](../README.md).

## Global constraints

- Python 3.11+ and Swift/SwiftUI; macOS 14+ code floor; initial distribution Apple silicon.
- State root: `~/Library/Application Support/com.m1labs.notron`; private encrypted payloads; Keychain unavailable means pause.
- Core inference remains Nebius with NVIDIA Nemotron; external-agent credentials and provider usage are separate, opt-in and disclosed.
- Plugins cannot grant permissions, rewrite policy, bypass prepared outbound content or invoke the Notes writer directly.
- No autonomous shell commands, repository writes, merge, deployment, messaging or payment in the default demonstration.

New paths below are proposals, not claims of existing implementation. Follow the shared design's names and JSON fields. Each task ends with targeted checks, relevant existing regressions, review, a task-only commit and a handoff. Run commands from the repository root unless stated otherwise. No real account/provider/Notes calls in automated unit tests.

---

## Task 1 — Async Siri entry and stable task identity

**Dependency:** P06 Task 2 must supply a tested async subprocess runner; R00 identifies the supported Siri configuration. Do not create another synchronous Process bridge.

**Files:** Modify `mac/Sources/Notron/AskNotronIntent.swift`, `Core.swift`, `mac/Package.swift`, `notron/cli.py`; Create `mac/Sources/Notron/TaskClient.swift`, `TaskEntity.swift`, `TaskIntents.swift`, `mac/Tests/NotronCoreTests/TaskClientTests.swift`, `tests/test_task_cli.py`. Swift file paths without a repeated directory in this plan are under `mac/Sources/Notron/`.

**Interfaces:** Python `tasks start --json` accepts stdin `{version:1, request_id, text, ingress:'siri'|'cli'|'app'}` calls R02 `enqueue_request` and returns `{version:1, task:TaskView}` before planning or external calls. Reuse `requests.create(text, source='cli', request_id=...)`; preserve user capture time/timezone and store host-validated ingress separately. `TaskClient.start(requestID: UUID, text: String) async throws -> TaskDTO`, `get(id: String) async throws -> TaskDTO`, `cancel(id: String) async throws -> TaskDTO`. `TaskDTO` maps the six `TaskView` fields exactly. Pass untrusted text over stdin, never shell arguments/source.

- [ ] Add fake ProcessRunner responses for accepted/running/failed/malformed output and timeout. Assert start returns a persisted task ID before external completion. In Python, duplicate request UUIDs return the same task; unknown ingress and extra fields fail. Retrieval/cancel require a locally authorized task ID, not an arbitrary external session ID.
- [ ] Implement async calls using P06's runner, keeping UI responsive and handling nonzero exit even with valid-looking stdout. Update Swift Package core-source inclusion/exclusion deliberately so pure DTO/client logic is testable without Siri. Keep App Intents types in the app target unless their availability permits tested extraction.
- [ ] Add branded start/status/cancel App Shortcuts and a stable-ID TaskEntity query. Resolve multiple task/project matches explicitly; no “latest” guess across projects. Start returns “Started …” or “Approval needed in Notron”, never a premature completion claim. Status reads stored state; cancel describes requested vs acknowledged. Speak a short non-sensitive status; detailed results open the app.
- [ ] Use available typed entities/schema/snippets only after R00 qualification and availability guards. Existing Ask remains a useful route for ordinary Notes questions; the task path handles delegated work. Do not assume the dynamic plugin catalog becomes a native Siri schema automatically.
- [ ] Run `.venv/bin/python -m pytest tests/test_task_cli.py -q` and `swift test --package-path mac`. On the qualified Mac, measure 10 real Siri acknowledgements and record timeout, app-not-running and locked/sleep behavior. Commit (`feat: expose durable tasks through Siri`) with actual OS/Siri evidence.

## Task 2 — Connections, approvals and task controls

**Files:** Create `mac/Sources/Notron/ConnectionsView.swift`, `TasksView.swift`, `TaskApprovalView.swift`, `mac/Tests/NotronCoreTests/TaskApprovalTests.swift`; Modify `NotronApp.swift`, `KeychainStore.swift`, `TaskClient.swift`, `notron/cli.py`, `docs/design/02-screens.md`, `docs/design/04-onboarding-flow.md`.

**Interfaces:** CLI `connections list|add|disable|remove --json` operates on R01 grants; credentials are entered natively and saved by Keychain reference, not stdin JSON. `TaskClient.approve(id: String, proposalDigest: String) async throws -> TaskDTO` consumes the R01 controller signature. Proposal display is derived from persisted host metadata and prepared-context provenance, not plugin-written HTML.

- [ ] Read the existing design system before any UI work. Resolve its Advanced-tier Skills & Plugins assumptions: reviewed connection setup and task safety controls must be available in the open-source BYO app; advanced developer-code installation remains clearly separated. Reuse tokens, light Notes-native styling and existing accessibility patterns.
- [ ] Add a pure approval model regression proving a changed proposal digest disables the old approve action. Cover grant revoked while the screen is open, duplicate approval clicks, expiry, mismatched task ID and app restart. UI must fetch current proposal before executing approval.
- [ ] Build connection setup with selected repository/project, declared tools, provider/model, destination disclosure, permission scope and connection test. Store only selected credentials. No automatic all-project scan, imported shell environment or implicit `.claude` access. Removing a connection requests cancellation and explains local purge vs already transmitted/delivered content.
- [ ] Build task list/detail with real lifecycle, errors, cancellation and separate Notes-delivery state. The approval screen shows requested action, destination, context sources, bounds and provider cost caveat from R00. Decline performs no dispatch. Show fallback if Siri is unavailable. Never force Calendar/Reminders permission to connect a developer tool.
- [ ] Run `swift test --package-path mac`, CLI connection/grant tests and manual keyboard/VoiceOver/reduced-motion checks. Commit (`feat: add connections and task approval screens`) with screenshots using synthetic data only.

## Task 3 — Notes results, follow-ups and sleep recovery

**Files:** Create `notron/tasks/delivery.py`, `tests/test_task_delivery.py`, `tests/test_task_followups.py`; Modify `notron/nodes.py`, `notron/worker.py`, `notron/tasks/workflows.py`, `notron/tasks/controller.py` and existing history serialization only where needed to store task references.

**Interfaces:** `schedule_delivery(task_id: str, *, destination_note_id: str) -> TaskView` creates a delivery operation under the existing Notes ledger, preserving contributing note IDs and source revisions. `resolve_task_reference(request: RequestEnvelope) -> str | None` returns an unambiguous accessible task ID or no match; ambiguous candidates cause a clarification before execution. These functions live in `delivery.py` and `workflows.py`, respectively.

- [ ] Build tests around the real guarded executor with fake Notes: successful external task + failed Notes append keeps task `succeeded`, sets delivery `needs_review`, and retries only delivery. Assert SDK start count remains one. Cover source revoked/edited after delegation, deleted destination, rich/photo note, ignored note and stale explicit reply scope.
- [ ] Deliver concise diagnosis, evidence, artifact location and verified applied/tested status to the user-selected destination. File paths are references to approved artifacts, not commands. Reuse existing Notes operation IDs and receipt recovery. Do not revive an expired request-specific read-only reply grant; ask for a new valid destination/approval when required.
- [ ] Add task-aware “what did it find?”, “continue that investigation” and “cancel it” tests; distinguish status, new delegated work and cancellation. A follow-up carries the original task/project association but no inherited extra permissions. Do not write a permanent Memory entry just because an agent produced a result.
- [ ] Exercise pause, sleep/wake, app quit and restart across all phases. Reconcile external state before dispatching; never treat a missing heartbeat as proof the remote side did nothing. Expose unresolvable state for review and offer a deliberate new request with a new ID.
- [ ] Run `.venv/bin/python -m pytest tests/test_task_delivery.py tests/test_task_followups.py tests/test_external_recovery.py tests/test_requests.py -q`. Complete the real Siri→GitHub→Claude→Notes scenario on synthetic content, including one cancelled run and one denied scope escalation. Commit (`feat: deliver external task results safely`). This is the October 1 demo gate.
