# Notron product and production roadmap

Updated **September 10, 2026**. Direction approved; new R-series work is **planned, not implemented**.

**Build the personal execution layer between Siri and the outside world.** Notron keeps context, connects tools and agents, tracks work, and returns useful results to Siri and Apple Notes. It remains useful directly through Notes and the Mac app. The first audience for this repositioning is developers using Apple devices.

## Delivery dates

| Target | Deliverable | Exit evidence |
|---|---|---|
| Sept 10–13 | R00: reliable baseline and integration feasibility | Runtime/test failures resolved; actual Siri, SDK and permission results recorded |
| Sept 14–20 | R01 and P06 Tasks 1–3: execution foundation | Durable tasks, approved connections, process supervision and working secure native bridge |
| Sept 21–27 | R02 and R03: complete product workflow | Claude session + GitHub connections, Siri handoff, task status and Notes result |
| Sept 28–Oct 1 | Internal demonstration milestone | Repeatable real Siri → Notron → external agent → result on the selected Mac |
| Oct 2–9 | R04 and remaining P06: extensibility and installation | Independent example plugin; signed install without development tools |
| Oct 10–16 | R05 qualification and small developer pilot | Reliability, setup and cost evidence; failure cases visibly handled |
| Oct 17–23 | Submission candidate | Frozen tested build, source/setup instructions, video and submission text ready |
| Oct 24–29 | Buffer and approved submission | Only release fixes; verify judge access and submit before deadline |
| **Oct 30, 1pm EDT** | **Official deadline** | 10am PDT / 17:00 UTC; October 1 is our internal milestone |

These are target windows, not measured engineering estimates. Work advances through dependencies below; no extra staffing or parallel agents is assumed. Review scope each Friday. Missing a gate moves or cuts dependent scope; it never converts an unverified capability into a completion claim.

The [official rules](https://nebiusglobalaihackathon.devpost.com/rules) require Nebius runtime use and an NVIDIA open-source model, public licensed source, setup instructions, a working demo/test build and a public YouTube demonstration under three minutes. Explain significant updates to this existing project. Keep judge access working through **December 15, 2026, noon Pacific**. Prepare submission by October 23; verify the live form and rules again before submission.

## Read and execute

1. [Repositioning design and shared contracts](repositioning-design.md) — current product scope and new interfaces.
2. [Existing production contracts](design.md) — implemented privacy, Notes actions, requests and service guarantees remain binding.
3. The selected task plan below, then the [latest roadmap handoff](handoffs/2026-09-10-repositioning-roadmap.md).

| ID | Plan | Dependency | Completion means |
|---|---|---|---|
| R00 | [Baseline and feasibility](plans/R00-baseline-feasibility.md) | Existing main | Reliable synthetic baseline; supported Siri/Claude paths and explicit fallbacks |
| R01 | [Connections and durable tasks](plans/R01-connections-tasks.md) | R00 | Stable task/connection contracts; approvals, recovery, cancellation and provenance |
| R02 | [External agents and APIs](plans/R02-external-connectors.md) | R01; R00 SDK qualification | Two working adapters: Claude Code sessions and GitHub repository data |
| R03 | [Siri and task experience](plans/R03-siri-experience.md) | R01; P06 T2; R02 for end-to-end proof | Siri starts work, checks status, cancels; Mac approvals and Notes delivery |
| R04 | [Open-source plugin developer kit](plans/R04-plugin-kit.md) | R01 protocol and R02 lessons | Documented SDK, fixture harness, independent plugin without changing core |
| R05 | [Demo, pilot and submission](plans/R05-hackathon-release.md) | R00–R04; applicable P06/P07 gates | Verified installable workflow and complete reviewable submission packet |

R01 owns shared contracts before R02/R03 consume them. P06 owns the subprocess runner and signed identity; R03 uses them, not a second bridge. R04 publishes the proven protocol rather than inventing another plugin format. Each task includes files, interfaces and acceptance checks. Do not start the entire roadmap in one coding session.

## Preserve the production investment

| Existing phase | Verified status inherited from handoffs | Repositioning decision |
|---|---|---|
| [P01 privacy](plans/01-privacy-permissions.md) | Tasks 1–5 locally implemented | Keep; R01 extends boundaries to external content and connections |
| [P02 reliability](plans/02-reliable-execution.md) | Tasks 1–6 locally implemented | Keep Notes ledger/worker; R01 adds correlated external-task state |
| [P03 conversation](plans/03-ask-conversation.md) | Tasks 1–4 locally implemented | Keep; R03 adds task-aware follow-ups without weakening permissions |
| [P04 mobile experiment](plans/04-shortcut-prototype.md) | Real-iPhone test deferred | Optional later track; no phone→Mac, iOS Notes automation or new iOS app promise |
| [P05 accounts/service](plans/05-accounts-service.md) | Tasks 1–6 locally implemented; staging/native gates open | Preserve; use BYO developer pilot first, finish managed paid readiness after hackathon if needed |
| [P06 Mac distribution](plans/06-mac-distribution.md) | Portable signed runtime/startup still pending | Critical path: native bridge early, full install before external pilot/submission |
| [P07 pilot/release](plans/07-pilot-release.md) | Planned | Reuse security, reliability and release gates; R05 replaces the initial Becky pilot with developer evidence |

“Locally implemented” is not a new passing-test claim. The September 10 fresh Python 3.11 baseline failed: CLI syntax compatibility and tests accessing the real Application Support directory are recorded in R00 T1. P05 staging, signed-device qualification and billing evidence remain open.

## What must ship

- **One complete useful workflow:** “Ask Notron to investigate issue 42 in the demo project.” Notron resolves a connected GitHub issue, uses selected project context and delegates to its connected Claude session. Siri acknowledges promptly. Later, the user gets a supported diagnosis and proposed patch artifact, with evidence, in Notron/Notes.
- **Two genuine adapters:** Claude Code/Agent SDK sessions and a read-only GitHub API connector. Their tools appear through one versioned plugin contract.
- **Personal continuity:** follow-ups use the right project/task; explicit durable memory remains under existing permission rules. A short reusable “investigate issue” skill selects these tools through the normal core controls.
- **Control users can understand:** approved project/repository access, provider disclosure, bounded runs, cancellation, honest partial/error status, and no duplicate side effects after restart.
- **Developer extensibility:** an independently authored example can be installed deliberately, tested, enabled and removed without modifying Notron core. MIT core remains useful with BYO credentials.
- **Installable evidence:** signed/notarized Mac build, reproducible demo setup and working access for judges. The movie alone is insufficient.

## Scope cuts, in order

1. Defer richer Siri schemas, semantic indexing and result snippets; retain working branded App Shortcuts/App Intents.
2. Defer phone entry, remote Mac relay and cloud execution; disclose that the selected Mac must be awake.
3. Defer public marketplace, automatic plugin downloads/updates, third-party executable sandbox claims and generic MCP bridge. Ship two reviewed adapters plus developer SDK.
4. Defer autonomous shell/test execution and repository edits; retain useful investigation and proposed patch artifacts. Do not advertise a fix as applied or tested.
5. Defer managed billing launch and large consumer pilot; retain secure BYO setup and small developer validation.

Never cut permissions, encrypted task content, recovery, accurate outcome reporting, real external integration, Nebius/NVIDIA runtime use or installability. If Claude integration cannot be qualified by September 13, record it as blocked and bring the specific substitute adapter/scope decision to the owner; do not silently replace the promised agent workflow with a mock. If the minimal real workflow misses October 1, freeze expansion until it works.

## Release boundaries and success measures

The hackathon build is a developer preview, not a paid production launch. P07 still owns the later public paid decision, service/billing evidence and updater requirements. Preparing artifacts is authorized; publishing, contacting testers, spending money and submitting remain separate owner actions unless already authorized in that session.

R05 records at least 20 complete synthetic/demo-task runs: no duplicate external starts, no wrong-project data, all failures disclosed; at least 18 useful completions with each remaining failure understood. On the qualified Mac, target acknowledgement within 5 seconds in at least 9 of 10 trials; report actual timing separately from external-task duration. Three developers should complete setup and one useful task using the documentation; outreach needs authorization. These are project targets, not present performance claims.

## Immediate next task

**R00 Task 1 — establish a trustworthy baseline and runtime contract.** Then qualify the real Siri/Claude path in R00 Task 2 before building the plugin protocol. Record each result in the [handoff template](handoff-template.md); update the [coverage map](coverage.md) and relevant checkbox only with fresh evidence. Work in an isolated branch and preserve unrelated local changes.
