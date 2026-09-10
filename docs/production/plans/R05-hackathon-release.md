# R05 — Qualification, pilot and hackathon submission Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkboxes; do not mark a device/provider gate complete with mocks.

**Goal:** Ship a credible developer preview and prepare a complete hackathon submission without confusing it with paid production readiness.

**Architecture:** Use the actual signed P06 build, controlled demo repositories and the two real adapters. Reuse P07 reliability/security practices with developer-specific scenarios and a separate submission gate.

**Tech Stack:** Existing Python/Swift test suites, signed/notarized macOS arm64 package, Nebius/NVIDIA runtime, qualified external-agent runtime.

**Spec:** [Repositioning design](../repositioning-design.md), [existing production contracts](../design.md), [active roadmap](../README.md).

## Global constraints

- Python 3.11+ and Swift/SwiftUI; macOS 14+ code floor; initial distribution Apple silicon.
- State root: `~/Library/Application Support/com.m1labs.notron`; private encrypted payloads; Keychain unavailable means pause.
- Core inference remains Nebius with NVIDIA Nemotron; external-agent credentials and provider usage are separate, opt-in and disclosed.
- Plugins cannot grant permissions, rewrite policy, bypass prepared outbound content or invoke the Notes writer directly.
- No autonomous shell commands, repository writes, merge, deployment, messaging or payment in the default demonstration.

New paths below are proposals, not claims of existing implementation. Follow the shared design's names and JSON fields. Each task ends with targeted checks, relevant existing regressions, review, a task-only commit and a handoff. Run commands from the repository root unless stated otherwise. No real account/provider/Notes calls in automated unit tests.

---

## Task 1 — Freeze the workflow and qualify the installable build

**Dependencies:** R00–R03 required for the first working demo; R04 developer kit and P06 signed packaging required for the submission candidate. P05 live billing is not a prerequisite for a clearly disclosed BYO developer preview.

**Files:** Create `docs/production/evidence/R05-scenarios.md`, `docs/production/evidence/R05-pilot.md`, `scripts/qualify_external_tasks.py`, `tests/test_external_acceptance.py`; Modify `mac/RELEASE.md`, P06 runtime manifest and `docs/production/README.md` as evidence changes.

**Interfaces:** Qualification runner emits JSON `{build, os, python, plugins, models, cases, timings, usage}` to an explicit output path; each case includes expected/actual/pass and identifies synthetic vs real. Record exact adapter code digest, SDK/API versions and Nebius/Claude model identifiers. Never include credentials or personal content.

- [ ] Build a deterministic scenario harness from the real controller, encrypted store and fake adapters. Cover duplicate Siri invocation, ambiguous project, plugin-disabled, source revoked, malicious issue/output, expired credentials, provider rate limit, spend limit, offline admission, wake/restart, cancellation and delivery failure. Assert zero duplicate starts and zero unauthorized outbound calls, including on recovery.
- [ ] Exercise P06's signed/notarized downloaded build in a fresh macOS account with no repo, developer Python or Terminal grants. Include both adapter runtimes in the build inventory; install must not depend on the owner's Claude/npm/uv setup. If external provider login is required, show the documented supported flow and cost responsibility. Record startup-gate proof; do not set the validation flag solely to pass the demo.
- [ ] Complete at least 20 actual controlled end-to-end runs against the chosen demo resources; target >=18 useful completions, all failures honestly surfaced, no duplicate starts or wrong-project disclosures. Measure Siri acknowledgement separately (target <=5 seconds in >=9/10 trials) and record task latency/cost distributions. No guaranteed user-facing latency without evidence.
- [ ] Prepare a three-developer pilot brief with setup→connect→Siri request→approval→result→follow-up→disconnect. Record completion without live coaching, time to first result, concrete value and confusion. Seek authorized participants; no unsolicited outreach. Findings drive fixes, not inflated adoption claims.
- [ ] Reuse P07 security review and restore/stop drills; unresolved high-impact access/data-integrity bugs block the build. Verify Keychain loss, revoked connection, update/rollback and uninstall behavior. Manual signed full-installer updates suffice for this preview; paid public release still requires P06/P07 updater gates.
- [ ] Run full synthetic Python suite, `swift test --package-path mac` and package verification against the exact build. Commit (`test: qualify the Notron developer preview`) with evidence. Freeze features October 16; target submission candidate October 23. A local unsigned binary is not a passing release artifact.

## Task 2 — Reviewable submission packet and ongoing judge access

**Files:** Create `docs/hackathon/submission.md`, `docs/hackathon/demo-script.md`, `docs/hackathon/judge-setup.md`, `docs/hackathon/release-checklist.md`; Modify `README.md`, `docs/production/README.md`, `SECURITY.md` only with the selected reporting route. Preserve MIT `LICENSE`; validate all bundled third-party notices.

**Interfaces:** Submission packet references exact build hash, source commit, public repository/demo URLs once published, supported OS/hardware, setup mode, provider/model usage and known limitations. An unpublished candidate uses its local artifact/commit reference and remains marked “not submitted”; never fabricate live URLs.

- [ ] Draft a video under three minutes: brief problem/context → real Siri request → scoped approval → real external work → result/follow-up → extensibility and architecture. Show at least one minute of functioning application modules; edit elapsed waits transparently. Do not present mock responses or sped-up latency as live real-time performance.
- [ ] Write setup instructions for a judge without the developer's environment, clarify awake-Mac requirements and provider credentials, and provide a controlled usable test route. A BYO source install is useful but does not by itself prove judges can access a working demo. Prepare a free accessible test build/demo and estimate provider/support cost through December 15 noon Pacific; obtain approval for any actual provisioned resources/spend.
- [ ] Describe significant post-August-26 work, Personal AI track fit, exact NVIDIA model and Nebius runtime use, separate external-agent provider and constructive tool/model feedback. Recheck the [official rules and live submission form](https://nebiusglobalaihackathon.devpost.com/rules), eligibility and required fields; do not copy stale repository claims that all inference must use Nebius.
- [ ] Inspect the candidate repository and history for secrets/personal data, license/source completeness, reproducible lockfiles and accurate shipped/planned feature labeling. Verify links, artifact hashes, install instructions and a clean judge walkthrough. Publishing requires a clean reviewable result; prepare every artifact before the final approval step.
- [ ] Present the concrete release/submission packet for owner authorization to publish/upload/submit if it has not already been given. Target October 24–29; hard deadline **October 30, 2026, 10am PDT / 1pm EDT / 17:00 UTC**. Record the actual submission confirmation and immutable source/build references after submission.
- [ ] Name an owner for access monitoring, usage limits and dependency/provider outages through **December 15, 2026, noon Pacific**. Keep paid launch as a separate P05/P07 decision after evidence; do not enable subscriptions, public marketing or automatic plugin distribution as part of this task.
- [ ] Commit the packet (`docs: prepare hackathon submission and judge access`). The task is “packet ready” until publication/submission evidence exists; distinguish each checkbox accurately.
