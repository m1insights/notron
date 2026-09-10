# September 10 — Repositioning roadmap handoff

## Outcome and scope

Documentation-only rewrite approved by the owner: Siri → Notron → external
agents/APIs, with Notes for context/results and an open-source plugin path.
Existing P01/P02/P03/P05 implementation is retained; P04 is optional, P06 remains
critical, and R05 owns the initial developer preview instead of the old consumer
pilot. No new runtime capability was implemented by this change.

## Work identity

- Branch: `docs/repositioning-roadmap`, based on main `533dca2`.
- Isolated worktree: `.worktrees/repositioning-roadmap`.
- Main already contained unrelated egg-info changes, `.claude/`, `notrontask.md`,
  `output/` and `tmp/`; none belongs to this change.
- The new worktree's dependency install also regenerated three egg-info files;
  only those worktree-generated files were restored to the branch base before staging.
- Active entry: [roadmap](../README.md); design: [contracts](../repositioning-design.md).
- Plans: R00 baseline/probes, R01 connections/tasks, R02 adapters/workflow,
  R03 Siri/UX/results, R04 developer kit, R05 qualification/submission.

## Evidence and limitations

| Check | Result |
|---|---|
| `git fetch origin --prune` | Succeeded; main base retained because it was ahead of origin |
| `uv sync --extra dev` in isolated worktree | Succeeded; CPython 3.11.14 selected |
| `.venv/bin/python -m pytest tests -q` before documentation edits | Exit 1; baseline failures, not a passing suite |
| `.venv/bin/python -m pytest tests/test_managed_transport.py -x -q --tb=short` | Exit 1; first failure chmods the real Application Support root and is denied by sandbox |
| Python syntax evidence | `notron/cli.py:140` has a backslash in an f-string expression, invalid on declared Python 3.11 floor |
| Installed interpreter inventory | 3.11.14, 3.12.12 and 3.14.2 available; project has no pinned `.python-version` yet |
| Documentation checks | Passed for 17 changed/new Markdown files: 67 local links, code fences, whitespace, required plan headers and placeholder scan; 15 R-series tasks |

No real Notes reads/writes, Claude sessions, provider inference, signing,
deployment, billing, external messages or submissions ran. Web research checked
official hackathon rules and provider documentation. Existing startup validation
flags remain unchanged. Broader real-home permission was not used to make tests
pass; R00 T1 must isolate the fixture and repair compatibility first.

## Decisions and gates

- October 1 internal real-workflow demonstration; October 23 submission candidate;
  October 30, 2026 at 10am PDT / 1pm EDT official deadline.
- Preserve judge access through December 15 noon Pacific.
- Core models remain the exact Nebius/NVIDIA defaults recorded in the design.
  External Claude model/authentication is unconfigured until R00 qualification.
- First Claude scope is owned sessions and investigation/proposed patch results,
  not arbitrary terminal takeover or autonomous repository changes.
- Process-separated developer plugins are trusted code; no sandbox guarantee.
- Async admission precedes model work; task completion and Notes delivery have
  separate states. Plugin processes are supervised across calls.
- Retain production gates for encryption, grants, signed startup, accounts and
  public paid release. A hackathon preview does not satisfy them automatically.

## Next session

Start **[R00 Task 1](../plans/R00-baseline-feasibility.md)**. Inspect the current
diff and interpreter, reproduce the focused syntax/state-isolation failures, then
fix them in an implementation worktree with regression coverage. Do not execute
all R-series tasks or bypass the protected startup gate.

No owner decision is required to begin baseline repair. Live SDK credentials,
synthetic project choice and signed-device inputs can block only their dependent
R00/P06 measurements; record those precisely when reached. Prepare publication
and outreach materials before seeking approval for those external actions.
