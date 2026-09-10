# R00 — Baseline and integration feasibility Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkboxes; do not mark a device/provider gate complete with mocks.

**Goal:** Establish a reliable baseline and prove the external-agent and Siri assumptions before building their abstractions.

**Architecture:** Keep probes disposable and synthetic. Fix baseline defects separately from capability probes; record versions, observed behavior and a concrete pass/fail for each dependency.

**Tech Stack:** Python/pytest, Swift App Intents, macOS signing tools; Anthropic Claude Agent SDK in a separately pinned probe environment.

**Spec:** [Repositioning design](../repositioning-design.md), [existing production contracts](../design.md), [active roadmap](../README.md).

## Global constraints

- Python 3.11+ and Swift/SwiftUI; macOS 14+ code floor; initial distribution Apple silicon.
- State root: `~/Library/Application Support/com.m1labs.notron`; private encrypted payloads; Keychain unavailable means pause.
- Core inference remains Nebius with NVIDIA Nemotron; external-agent credentials and provider usage are separate, opt-in and disclosed.
- Plugins cannot grant permissions, rewrite policy, bypass prepared outbound content or invoke the Notes writer directly.
- No autonomous shell commands, repository writes, merge, deployment, messaging or payment in the default demonstration.

New paths below are proposals, not claims of existing implementation. Follow the shared design's names and JSON fields. Each task ends with targeted checks, relevant existing regressions, review, a task-only commit and a handoff. Run commands from the repository root unless stated otherwise. No real account/provider/Notes calls in automated unit tests.

---

## Task 1 — Repair and pin the baseline

**Files:** Modify `notron/cli.py`, `tests/conftest.py`, `pyproject.toml`; Create `.python-version`, `tests/test_runtime_compatibility.py`, `docs/production/evidence/R00-baseline.md`. Modify only additional test fixtures proven to escape their temporary root; name each in the handoff.

**Observed problem:** The September 10 worktree used CPython 3.11.14. Full-suite exit was 1. `cli.py:140` contains a backslash escape inside an f-string expression, invalid on the declared 3.11 floor. Independently, `tests/test_managed_transport.py` attempts to chmod the real Application Support directory through `transport._identity`. The sandbox stopped it. Do not rerun the suite with broad home-directory access as a substitute for test isolation.

**Interfaces:** Preserve `cli.main(argv=None)` and production state/security semantics. Test fixtures redirect application state through module/path injection, not by repurposing the shell's HOME. Keep Python >=3.11 supported; pin the development interpreter to 3.11.14 initially, then use the same supported version in P06's runtime manifest.

- [ ] Add this compatibility regression; run it with Python 3.11 and confirm the current syntax failure:

```python
from pathlib import Path

def test_cli_compiles_on_supported_interpreter():
    source = Path('notron/cli.py').read_text()
    compile(source, 'notron/cli.py', 'exec')
```

- [ ] Move conditional glyph selection out of f-string expressions, preserving output. Example implementation pattern:

```python
mark = '✓' if result.ok else '✗'
print(f'  {mark} {workspace.CARE} — {result.reason}\n')
```

Audit all modules for the same 3.11 incompatibility; do not raise the declared minimum to hide the failure.
- [ ] Extend the existing synthetic credential/state fixture to redirect every persistent path used by managed transport and recovery stores into `tmp_path`. Add a guard fixture that fails on application-state opens/chmod outside that root, and a regression exercising `ManagedTransport._identity` through the existing public transport test. Fake network remains mandatory; production permission checks stay enabled.
- [ ] Run `.venv/bin/python -m pytest tests/test_runtime_compatibility.py tests/test_managed_transport.py -q`, then `.venv/bin/python -m pytest tests -q`. Record counts, interpreter and residual failures; resolve baseline regressions before feature work. Verify `python -m notron --help` in the isolated test harness has no network/native side effect.
- [ ] Commit the baseline repair and evidence separately (`fix: restore isolated Python 3.11 baseline`). Record exact next task R00 T2. Dependency installation alone is not a fix.

## Task 2 — Qualify Siri and Claude on the intended Mac

**Files:** Create `scripts/probes/claude_session_probe.py`, `scripts/probes/claude/pyproject.toml`, `scripts/probes/claude/uv.lock`, `tests/test_claude_probe_contract.py`, `docs/production/evidence/R00-integration-matrix.md`; inspect existing `mac/Sources/Notron/AskNotronIntent.swift`, `Core.swift`, `mac/Package.swift` and P06 before changing their architecture.

**Interfaces:** Probe emits sanitized JSON: `sdk_version`, `provider`, `model`, `owned_session_resume`, `selected_history`, `cancel_acknowledged`, `permission_hook`, `budget_enforced`, `runtime_requirements`, `result`. Values must come from observed probes; unsupported features are false. Never store session contents, tokens or real project paths in evidence.

- [ ] Recheck official [Claude sessions](https://code.claude.com/docs/en/agent-sdk/sessions) and linked SDK permission/authentication docs. Pin the compatible Python SDK and all runtime dependencies in the isolated probe. Record exact model and supported authentication; do not assume a consumer subscription can be reused for third-party automation.
- [ ] Create a fake probe implementation for lifecycle tests, including a call log and a resume ID supplied by the first response. Expose `run_probe(client) -> dict`; `client.start`, `client.resume`, `client.cancel` are injected functions returning dictionaries. Test the failure case explicitly:

```python
from scripts.probes.claude_session_probe import run_probe

class RefusingClient:
    def start(self): return {'session_id': 'owned-1'}
    def resume(self, session_id):
        assert session_id == 'owned-1'
        return {'resumed': True}
    def cancel(self, session_id): return {'acknowledged': False}

def test_no_cancellation_claim_without_acknowledgement():
    result = run_probe(RefusingClient())
    assert result['owned_session_resume'] is True
    assert result['cancel_acknowledged'] is False
```

Keep SDK-specific credential and permission tests separate from this three-call lifecycle fixture.
- [ ] On an explicitly selected synthetic project, exercise real start → capture ID → resume → result and cancellation. Deny Bash, edits, writes and unselected projects; request each in a hostile fixture to verify denial. Prove budget/turn/time limits and behavior when connectivity dies after submission. Inspect whether SDK process/session persistence enables reliable status without replay. A process kill alone does not prove remote billing stopped.
- [ ] Inventory exact macOS/Xcode/device, Siri language/account availability and signing access. Invoke the current branded Ask shortcut, measure completion/timeout and discoverability, then record whether typed AppEntity/status intents and newer Siri features are actually available. Do not modify the secure-startup gate to make a probe pass. If P06's bridge is required to complete a probe, mark that measurement pending P06 T2; the fallback design remains asynchronous branded intents.
- [ ] Record a decision matrix: required SDK path supported or blocked; rich Siri enhancements optional; phone relay excluded. Choose the reviewed adapter/runtime packaging from evidence. If SDK permissions cannot confine read access, use only a copied synthetic project containing approved context and no ambient credentials; do not advertise access isolation for arbitrary local projects. If no supported real delegation path remains, stop only that integration and present a concrete alternative for owner decision.
- [ ] Run `.venv/bin/python -m pytest tests/test_claude_probe_contract.py -q`; commit probes and sanitized evidence (`docs: qualify Siri and Claude integration boundaries`). Evidence must include remaining live checks and required credentials/signing inputs, not filled-in successes.
