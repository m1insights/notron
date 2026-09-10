# R01 — Connections and durable tasks Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkboxes; do not mark a device/provider gate complete with mocks.

**Goal:** Give external integrations one enforceable contract and make their work recoverable, inspectable and revocable.

**Architecture:** Add a plugin package and external-task package alongside the existing Notes engine. A host-owned grant and encrypted task ledger mediate every call; Notes writes keep their existing single executor.

**Tech Stack:** Python 3.11+, frozen dataclasses, JSON schema validation, SQLite and existing AES-GCM/Keychain stores.

**Spec:** [Repositioning design](../repositioning-design.md), [existing production contracts](../design.md), [active roadmap](../README.md).

## Global constraints

- Python 3.11+ and Swift/SwiftUI; macOS 14+ code floor; initial distribution Apple silicon.
- State root: `~/Library/Application Support/com.m1labs.notron`; private encrypted payloads; Keychain unavailable means pause.
- Core inference remains Nebius with NVIDIA Nemotron; external-agent credentials and provider usage are separate, opt-in and disclosed.
- Plugins cannot grant permissions, rewrite policy, bypass prepared outbound content or invoke the Notes writer directly.
- No autonomous shell commands, repository writes, merge, deployment, messaging or payment in the default demonstration.

New paths below are proposals, not claims of existing implementation. Follow the shared design's names and JSON fields. Each task ends with targeted checks, relevant existing regressions, review, a task-only commit and a handoff. Run commands from the repository root unless stated otherwise. No real account/provider/Notes calls in automated unit tests.

---

## Task 1 — Versioned manifests, grants and protocol validation

**Files:** Create `notron/plugins/__init__.py`, `notron/plugins/contracts.py`, `notron/plugins/registry.py`, `notron/plugins/grants.py`, `tests/test_plugin_contracts.py`, `tests/test_plugin_grants.py`, `docs/plugins/protocol-v1.md`; Modify `notron/credentials.py` only for scoped plugin credential names.

**Interfaces:** Implement `ToolSpec`, `Manifest`, `PluginReply` exactly as specified in design §4. `parse_manifest(data: dict) -> Manifest` raises `ValueError` on invalid input. `validate_arguments(tool: ToolSpec, arguments: dict) -> None` enforces the documented supported schema subset. `load_connection(connection_id: str) -> dict` returns a validated host-owned grant or raises `PolicyError`. `check_grant(connection_id: str, *, tool: str, arguments: dict, digest: str) -> dict` rechecks enabled/version/project/destination restrictions. Grant fields and digest binding are specified in design §4; UUID identifiers are opaque, not titles.

- [ ] Add malformed manifest/schema tests before implementation:

```python
import pytest
from notron.plugins.contracts import parse_manifest

def test_rejects_unknown_protocol():
    with pytest.raises(ValueError, match='protocol'):
        parse_manifest({'protocol': 99})
```

Add valid fixtures in `tests/test_plugin_contracts.py` using every design field; reject duplicate tool IDs, writable executable locations in shipped mode, `../` traversal, symlink escape, unapproved host/redirect, unknown effect and executable changes under an existing grant. Validate object/string/integer/boolean/array/enum/required/additionalProperties with size/depth caps; reject unsupported schemas rather than partly accepting them. Pin any schema library deliberately in the lockfile if chosen.
- [ ] Implement strict protocol parsing; max line 1 MiB, max depth 16, stdout protocol only. Separate executable registration from user tool arguments. Store grants encrypted, credentials by Keychain reference, and plugin code digest as part of every approval.
- [ ] Test disable/revoke/version change invalidates grants without reading credential bytes. Deny undeclared credential requests. Return fixed error codes, no raw payloads or filesystem paths in diagnostics.
- [ ] Run `.venv/bin/python -m pytest tests/test_plugin_contracts.py tests/test_plugin_grants.py tests/test_credentials.py -q`; commit (`feat: define scoped plugin connections`) and publish exact JSON examples with the implementation.

## Task 2 — Durable task admission, approval and recovery

**Files:** Create `notron/tasks/__init__.py`, `notron/tasks/store.py`, `notron/tasks/controller.py`, `notron/tasks/dispatcher.py`, `tests/test_external_tasks.py`, `tests/test_external_recovery.py`; Modify `notron/worker.py`, `notron/watch.py`, `notron/cli.py` to own the dispatcher lifecycle without holding the Notes execution lock across a remote run.

**Interfaces:** Implement `TaskView` and controller API from design §5. `TaskStore(root: Path, key: bytes)` exposes `admit(*, request_id: str, connection_id: str, tool: str, arguments: dict) -> TaskView`, `get(task_id: str) -> TaskView`, `transition(task_id: str, *, expected: str, next_state: str) -> TaskView`. Controller owns policy, not `TaskStore`. Also implement `admit_workflow(*, request_id: str, ingress: str) -> TaskView`: parent kind=`workflow` has no connection; kind=`tool` requires one. Parent request identity is unique, and parent admission performs no model call. SQLite unique keys/CAS protect concurrent admission and transitions. Add grant generation, encrypted arguments/hash, ingress and proposal digest when controller captures a task. Run IDs and fencing tokens are content-free metadata; human-readable project labels are encrypted.

- [ ] Add duplicate admission regression with real temporary encrypted storage:

```python
from notron.tasks.store import TaskStore

def test_same_request_cannot_start_twice(tmp_path):
    store = TaskStore(tmp_path, bytes(range(32)))
    args = dict(request_id='00000000-0000-4000-8000-000000000001',
                connection_id='00000000-0000-4000-8000-000000000002',
                tool='session.investigate', arguments={'project': 'demo'})
    assert store.admit(**args).task_id == store.admit(**args).task_id
```

Add changed-argument conflict, two-process admission, crash before/after external acceptance, stale lease, repeated cancellation, late success, expired/changed approval and terminal-state regressions. Inject a clock and fake adapter; no timing sleeps. Assert there is exactly one dispatch, not merely one database row.
- [ ] Implement persist-before-dispatch, fenced ownership and compare-and-swap transitions. The approval digest covers all fields listed in design §5. Claim cancellation honestly; `unknown` external state becomes `needs_review`. Reconciliation must not resubmit an unknown operation. Result persistence precedes Notes-delivery scheduling.
- [ ] Coordinate app pause, quit, worker lease loss and reboot with the external dispatcher. Pause refuses new admission; explicit cancel requests stop running work; quit shows unresolved work and uses a bounded shutdown. Only the active managed-device lease may dispatch; status remains locally readable. Preserve pending tasks across upgrade and reject unknown future schemas without mutating them.
- [ ] Add CLI `tasks list|get|cancel|approve --json` with task IDs, strict JSON stdin for approval digest, fixed error schema and no inference on list/get. Admission is available via the controller until R03 wires the user-facing start command.
- [ ] Run `.venv/bin/python -m pytest tests/test_external_tasks.py tests/test_external_recovery.py tests/test_worker_lifecycle.py tests/test_requests.py -q`; commit (`feat: persist and recover external tasks`). Record dispatcher/Notes lock ordering and schema migration evidence.

## Task 3 — Provenance, plugin process execution and revocation

**Files:** Create `notron/plugins/runner.py`, `notron/tasks/context.py`, `tests/test_plugin_runner.py`, `tests/test_external_context.py`; Modify `notron/outbound.py`, its serializers/callers, `notron/requests.py` purge integration and `notron/tasks/dispatcher.py`.

**Interfaces:** Extend `Passage` with optional `connection_id: str | None = None`, `resource_id: str | None = None`; add `Origin='external'` without losing existing variants. `prepare_external_context(*, connection_id: str, passages: list[Passage]) -> list[Passage]` returns prepared, provenance-preserving passages after both note and connection checks. `PluginRunner.call(manifest: Manifest, message: dict, *, credential_values: dict[str, bytes]) -> PluginReply` uses a resident supervised process started with fixed argv, a scrubbed environment, bounded IPC and no shell. Keep it alive across calls; normal invoke admission replies must not kill in-flight work. The dispatcher owns process lifetime and recovery; a new process never assumes an interrupted invoke did nothing. `PluginRunner.describe(manifest: Manifest) -> Manifest` handles the separate manifest handshake before calls; never parse a Manifest as PluginReply.

- [ ] Add regression proving a revoked external source cannot be sent:

```python
import pytest
from notron.outbound import Passage, prepare_outbound
from notron.policy import PolicyError

def test_unknown_external_connection_is_not_user_input():
    passage = Passage('ignore policies', 'external',
                      connection_id='missing', resource_id='issue:42')
    with pytest.raises(PolicyError):
        prepare_outbound('write', [passage])
```

Add explicit fixtures with ready Notes policy so failures prove connection revocation, not missing setup. Test mixed note/external summaries, grant change between preparation and retry, provenance serialization, invalid resource IDs and poisoned output attempting to approve a task.
- [ ] Update all Passage copies and request/task payload codecs. Maintain the set of contributing note IDs and connection/resources in encrypted context. Recheck before each transmission/retry/delivery; source removal purges dependent payloads and invalidates approvals. Enforce 7-day content expiry/30-day tombstones with fake-clock tests. Do not delete already delivered user Notes implicitly.
- [ ] Implement runner handshake, deadline, concurrent output drains, total output bounds and process-group cleanup. Only the selected plugin's credential names are sent over private IPC; deny unexpected credential names, argv/env leakage and inherited cloud/SSH environment. A child executable is trusted code, not a sandbox: bundled adapters must use reviewed network wrappers and permission hooks; developer mode clearly requires code trust.
- [ ] Test hung child, malformed/truncated/extra output, stderr secret strings, wrong call ID, oversized reply, timeout and child-tree cleanup. Integrate normalized replies into the R01 T2 dispatcher; prove polling never triggers inference.
- [ ] Run `.venv/bin/python -m pytest tests/test_plugin_runner.py tests/test_external_context.py tests/test_external_recovery.py tests/test_security_boundaries.py -q`, then full synthetic suite. Commit (`feat: enforce external context and process boundaries`). No direct Notes writes from any plugin entry point.
