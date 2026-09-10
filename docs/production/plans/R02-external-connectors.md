# R02 — External agents and API connectors Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkboxes; do not mark a device/provider gate complete with mocks.

**Goal:** Deliver a real developer workflow through two adapters using the same host contract.

**Architecture:** Bundle reviewed Python adapter processes for Claude sessions and GitHub reads. A bounded Notron workflow selects approved tools and joins their results; no arbitrary tool code comes from model output.

**Tech Stack:** Python plugin protocol v1, Anthropic Claude Agent SDK qualified in R00, GitHub REST API, Nebius Nemotron via existing Brain.

**Spec:** [Repositioning design](../repositioning-design.md), [existing production contracts](../design.md), [active roadmap](../README.md).

## Global constraints

- Python 3.11+ and Swift/SwiftUI; macOS 14+ code floor; initial distribution Apple silicon.
- State root: `~/Library/Application Support/com.m1labs.notron`; private encrypted payloads; Keychain unavailable means pause.
- Core inference remains Nebius with NVIDIA Nemotron; external-agent credentials and provider usage are separate, opt-in and disclosed.
- Plugins cannot grant permissions, rewrite policy, bypass prepared outbound content or invoke the Notes writer directly.
- No autonomous shell commands, repository writes, merge, deployment, messaging or payment in the default demonstration.

New paths below are proposals, not claims of existing implementation. Follow the shared design's names and JSON fields. Each task ends with targeted checks, relevant existing regressions, review, a task-only commit and a handoff. Run commands from the repository root unless stated otherwise. No real account/provider/Notes calls in automated unit tests.

---

## Task 1 — Claude session adapter

**Files:** Create `plugins/claude-session/manifest.json`, `plugins/claude-session/pyproject.toml`, `plugins/claude-session/uv.lock`, `plugins/claude-session/notron_claude/__init__.py`, `plugins/claude-session/notron_claude/adapter.py`, `plugins/claude-session/README.md`, `tests/test_claude_connector.py`; Modify P06 runtime manifest/build scripts when they exist to include the qualified runtime.

**Interfaces:** Tool `session.investigate` accepts `{project_id, instruction, context}`; `context` is host-prepared structured text, never arbitrary file paths. Lifecycle uses protocol `invoke/status/cancel` and `PluginReply`. `ClaudeAdapter(client, store)` exposes `invoke(task_id: str, arguments: dict) -> PluginReply`, `status(task_id: str) -> PluginReply`, `cancel(task_id: str) -> PluginReply`. Client wraps the pinned R00 SDK; store maps host task IDs to encrypted session IDs and results. The host owns grants and task state.

- [ ] Build a fake SDK client with `start` call recording and deterministic session ID, plus a temporary encrypted mapping store. Assert duplicate invoke uses the same persisted session mapping and calls SDK start once; changed arguments fail. Inject failure immediately before/after SDK start and verify unresolved acceptance is `unknown`, not another start. Add resume-with-explicit-ID and wrong-project rejection tests.
- [ ] Implement the pinned SDK's supported query/resume flow and permission callback; no generic “latest session” lookup, terminal keystrokes or `.claude` scraping. Supply the explicit project and allow only qualified read tools; disable imported project hooks, settings, auto-discovered plugins and remote MCP endpoints unless separately reviewed. If the SDK cannot disable these, confine the preview to an approved clean project copy as decided in R00.
- [ ] Return accepted promptly after durable admission; execute the SDK query in an adapter-owned background job while the resident protocol loop continues to serve status/cancel. Persist external ID, events and final artifacts encrypted. Return diagnosis, cited file references and proposed patch text; preserve “not applied / not tested” status. Enforce the qualified turn/time/spend settings and expose actual provider/model and usage. Permission escalation pauses for core approval or refuses unsupported actions; never run a shell as an automatic fallback.
- [ ] Test auth expiry, unavailable model, permission denial, cancellation acknowledgment, late completion and SDK version mismatch. Run `.venv/bin/python -m pytest tests/test_claude_connector.py tests/test_external_recovery.py -q`; execute the R00 real synthetic-project lifecycle again through the actual host/plugin bridge. Commit (`feat: connect owned Claude sessions`) with exact SDK/runtime/model evidence.

## Task 2 — GitHub read-only API adapter

**Files:** Create `plugins/github/manifest.json`, `plugins/github/pyproject.toml`, `plugins/github/uv.lock`, `plugins/github/notron_github/__init__.py`, `plugins/github/notron_github/adapter.py`, `plugins/github/README.md`, `tests/test_github_connector.py`.

**Interfaces:** Tools `github.get_issue` with `{repository: 'owner/name', number: int}` and `github.list_pull_requests` with `{repository: 'owner/name'}`. `GitHubAdapter(transport, grant)` exposes `invoke(task_id: str, arguments: dict, *, tool: str) -> PluginReply`. Transport is injected; production permits only reviewed GET paths on `api.github.com`, selected repositories and qualified REST API version. Outputs are structured text/resource IDs, never instructions or permission grants.

- [ ] Verify current official GitHub REST docs and required token permissions during execution; record version/scopes in plugin README. Support public demo repositories without a token where feasible, or narrowly scoped fine-grained token via Keychain. No private-repository blanket access.
- [ ] Create a fake recording transport in `tests/test_github_connector.py`; test repository mismatch raises `PolicyError` before any HTTP call. Test `../`, encoded slash/host injection, off-host redirect, pagination beyond 3 pages, rate limit, malformed JSON and response over 1 MiB. The adapter must make zero POST/PATCH/DELETE requests.
- [ ] Implement exact path construction from validated owner/name/positive issue number. Allow max 30 pull requests, bounded response content and deadlines. Return provider errors with retry-after guidance; host does not auto-replay delegation because a read failed. Mark issue/PR bodies as external provenance bound to their repository grant.
- [ ] Run `.venv/bin/python -m pytest tests/test_github_connector.py tests/test_external_context.py -q`; perform a real read against the chosen public demo repository and record sanitized provenance. Commit (`feat: add scoped GitHub reads`). This must install through the same registry/runner as Claude, without a plugin-ID switch inside core dispatch.

## Task 3 — Bounded cross-tool workflow and reusable skill

**Files:** Create `notron/tasks/planner.py`, `notron/tasks/workflows.py`, `skills/investigate-issue/skill.json`, `tests/test_task_planner.py`, `tests/test_investigate_workflow.py`; Modify `notron/cli.py`, `notron/nodes.py` routing only, `notron/tasks/controller.py`.

**Interfaces:** `enqueue_request(request: RequestEnvelope, *, ingress: str) -> TaskView` in `workflows.py` captures the request and calls `TaskStore.admit_workflow` before any model call; a dispatcher stage performs planning asynchronously. `plan_request(request: RequestEnvelope, *, brain) -> dict` returns a validated plan with `workflow`, `project_id`, `connection_ids`, `issue_number`, `context_note_ids`; unresolved/ambiguous inputs return a clarification object and no dispatch. `start_workflow(request: RequestEnvelope, *, plan: dict) -> TaskView` loads the already admitted parent task, derives stable child request IDs and executes the registered `investigate-issue` workflow. `skill.json` has version=1, id, input schema, required tool IDs and declarative ordered steps; it contains no executable code or credentials.

- [ ] Add deterministic Brain fixtures proving invented tools and unapproved repositories never reach a connector; a second matching project triggers clarification. Use existing `RequestEnvelope.create` helper through `requests.create`, not a new request identity format. Test that hostile GitHub text cannot change the selected project or request extra tools.
- [ ] Route explicit external-work requests into this bounded planner, preserving existing Notes routing. Keep core planning on existing `Brain.ask_json` with prepared passages and fixed schema. Implement read issue → select allowed context → propose delegation → receive approval if needed → invoke Claude → summarize result. Each child stage is persisted; a restart after a successful read/delegation resumes from stored state, not from the first step.
- [ ] Bind the delegate approval to the actual prepared context and project. Source or plugin changes invalidate it. Generate result summaries with Nebius only from authorized passages; model output cannot announce success inconsistent with adapter/ledger state. Parent task contains child task IDs and last verified outcome; cancellation requests all active children and reports any unresolved ones.
- [ ] Run `.venv/bin/python -m pytest tests/test_task_planner.py tests/test_investigate_workflow.py tests/test_requests.py -q`; test a full workflow with fake adapters including a crash between every stage. Then demonstrate a real chosen issue and Claude result through CLI with distinct Nebius and external-provider usage evidence. Commit (`feat: orchestrate issue investigation across connections`). R03 provides the Siri entry and guarded Notes result next.
