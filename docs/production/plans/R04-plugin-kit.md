# R04 — Open-source plugin developer kit Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkboxes; do not mark a device/provider gate complete with mocks.

**Goal:** Prove that another developer can extend Notron without changing its core.

**Architecture:** Publish the proven v1 process protocol and conformance fixtures, then exercise them with an independent adapter and declarative workflow. No marketplace or generic framework migration is required.

**Tech Stack:** Python reference SDK/CLI, language-neutral JSON protocol, MIT examples and existing plugin registry.

**Spec:** [Repositioning design](../repositioning-design.md), [existing production contracts](../design.md), [active roadmap](../README.md).

## Global constraints

- Python 3.11+ and Swift/SwiftUI; macOS 14+ code floor; initial distribution Apple silicon.
- State root: `~/Library/Application Support/com.m1labs.notron`; private encrypted payloads; Keychain unavailable means pause.
- Core inference remains Nebius with NVIDIA Nemotron; external-agent credentials and provider usage are separate, opt-in and disclosed.
- Plugins cannot grant permissions, rewrite policy, bypass prepared outbound content or invoke the Notes writer directly.
- No autonomous shell commands, repository writes, merge, deployment, messaging or payment in the default demonstration.

New paths below are proposals, not claims of existing implementation. Follow the shared design's names and JSON fields. Each task ends with targeted checks, relevant existing regressions, review, a task-only commit and a handoff. Run commands from the repository root unless stated otherwise. No real account/provider/Notes calls in automated unit tests.

---

## Task 1 — Reference SDK, template and conformance runner

**Files:** Create `notron/plugins/sdk.py`, `notron/plugins/conformance.py`, `plugins/example/manifest.json`, `plugins/example/main.py`, `tests/test_plugin_conformance.py`, `docs/plugins/getting-started.md`; Modify `docs/plugins/protocol-v1.md`, `notron/cli.py`, `README.md`.

**Interfaces:** `serve(handler) -> None` reads protocol-v1 messages and writes matching call-ID replies; handler receives a validated request dict and returns a reply dict. `check_plugin(path: Path) -> list[dict]` runs local synthetic conformance cases, returning `{case, passed, error_code}` per case. `notron plugins check PATH --json` exits nonzero on any required failure. `notron plugins install PATH` is an explicit developer action with digest/capability review; no auto-install based on a prompt or remote description.

- [ ] Write a template plugin that provides `example.word_count` with strict `{text: string}` input and `{count: integer}` output, no credentials/network. Its core calculation is deliberately small:

```python
def word_count(arguments: dict) -> dict:
    return {'count': len(arguments['text'].split())}
```

The surrounding handler must implement describe/invoke/status/cancel/idempotency per v1; the helper alone is not a conforming plugin.
- [ ] Create conformance tests for a good plugin and separate deliberately broken fixtures: mismatched call ID, no describe, duplicate invocation, extra stdout, invalid schema, hangs and secret-like stderr. Verify check returns failure codes and never prints sensitive fixture values.
- [ ] Implement local validation/installation with explicit code trust, code hash, version and capability display, disable/remove and no automatic executable dependency downloads. Ship reviewed first-party adapters in P06; document a developer-owned environment for additional executable plugins. A new version needs revalidation/reapproval; registration cannot widen an existing grant.
- [ ] Document hello-world installation, invocation, lifecycle, supported schema subset, synthetic tests, provider setup, retention and upgrade rules. Include protocol examples independent of Python so a future TypeScript adapter can interoperate. State accurately that developer code can have the local process user's powers; do not market the manifest as a sandbox.
- [ ] Run `.venv/bin/python -m pytest tests/test_plugin_conformance.py tests/test_plugin_contracts.py tests/test_plugin_runner.py -q`; run conformance against both real adapter builds with injected providers. Commit (`feat: publish plugin developer kit`).

## Task 2 — Independent extension and contribution path

**Files:** Create `docs/plugins/architecture.md`, `docs/plugins/contributing.md`, `docs/production/evidence/R04-independent-plugin.md`; Modify `skills/investigate-issue/skill.json`, `docs/plugins/getting-started.md`, `README.md`, `CONTRIBUTING.md` if present (otherwise Create).

**Interfaces:** Declarative skill v1 from R02 contains input schema, required tool IDs and ordered steps resolved by core. Missing/disabled/incompatible tools fail before a workflow starts. Core permission/credential APIs are intentionally not replaceable plugin hooks.

- [ ] Add skill-loader regressions in new `tests/test_skill_contracts.py`: unknown tool, circular step reference, unbounded loop and requested permission grant are rejected; valid `investigate-issue` dispatches through the normal registry. Implementation in new `notron/plugins/skills.py` exposes `load_skill(path: Path) -> dict` and never evals code or template-generated shell strings.
- [ ] Have a developer who did not implement the registry follow the docs to build a small local-data adapter, install it, use it in a permitted skill and remove it. Until such a person is available, a clean-room internal exercise is useful but is not labeled independent validation. Record time to first useful result, core changes required, failure messages and fixes. Contacting a person needs owner authorization; prepare the brief first.
- [ ] Explain the extension sequence: tools/agent adapters and skills now; later vetted MCP bridge, provider adapters, then optional UI/storage/loop extension points only when a concrete use case warrants them. Preserve the [DeepSeek-inspired](https://www.deepseek.com/harness/en/) modularity goal while keeping policy and recovery enforceable. Public marketplace/security review, discovery/ranking, package signing, revocation and automatic updates are post-hackathon work.
- [ ] Publish an accurate MIT contribution path, example license/dependency notices, capability review checklist and private security reporting route. Do not advertise a support commitment or approved community plugin catalog that does not exist.
- [ ] Run `.venv/bin/python -m pytest tests/test_skill_contracts.py tests/test_investigate_workflow.py -q`; commit docs, fixtures and loader (`docs: establish independent plugin contribution path`). Record any uncompleted external validation as open evidence, not a passing task.
