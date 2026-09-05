# P02 Task 5 review evidence

Final code `2b7176a` is approved; root independently verified **967 passed in 31.77s**, exit 0. All pending full-suite qualifications in the historical review reports below are closed. Three Important findings were corrected; no blocking findings remain. Native P06 and downstream-pause freshness limitations remain.

## Initial task review

### Spec Compliance

- ❌ Issues found: unknown-capture relative requests expressed with words can bypass the required date clarification (`notron/when.py:81`); a street address can falsely establish event-end authorization and admit an assumed duration (`notron/when.py:124`, `notron/nodes.py:334`, `notron/executor.py:571`). Both need fixes before Task 5 is accepted.
- ✅ Every production/test file explicitly listed in the task brief has a corresponding change. Additional executor, guard, state, graph and operation-code changes support the requested durable scheduling behavior.
- ⚠️ Cannot verify native Apple behavior from this diff or synthetic evidence: EventKit numeric authorization results, JXA/Objective-C bridging, timezone-aware reminder components/alarms, and real identifier behavior require the planned P06 platform verification (`notron/calendar.py:33`, `notron/reminders.py:25`, `notron/reminders.py:81`). No real apps, providers, network, or user data were accessed.
- ⚠️ Freshness scope: the query refresh is at `notron/index.py:201`; unchanged `notron/recovery.py:65` restores already-selected context and `notron/graph.py:132` resumes after the saved node. A long pause after retrieval therefore has no second live-context validation. The implementation report explicitly discloses this limit; the controller should retain it as a limitation rather than claim freshness at arbitrary later receipt time.

### Strengths

- Stable target IDs are persisted before external effects and copied back into graph actions; ambiguous completion and destination names produce questions instead of first-match/default selection (`notron/executor.py:545`, `notron/executor.py:565`, `notron/executor.py:586`).
- Verified and uncertain recovery branches precede fresh creation checks, preserving receipt repair without replaying saves (`notron/executor.py:507`, `notron/executor.py:511`). Existing reconciliation retains unknown outcomes as review-required (`notron/executor.py:627`).
- Conflict authorization binds the proposed event and actual conflicting identities/times; fresh overlap checks run before creation (`notron/calendar.py:244`, `notron/calendar.py:258`, `notron/executor.py:577`).
- Changed selected notes receive live metadata/permission checks, redaction and a bounded refreshed excerpt; missing or unavailable context is omitted and explicitly disclosed (`notron/index.py:201`, `notron/index.py:212`, `notron/nodes.py:849`).
- Known pre-effect refusals cancel PREPARED intent before delivering the question. The distinct `?` marker reaches the model-free scheduling writer without claiming an action succeeded (`notron/executor.py:459`, `notron/nodes.py:388`, `notron/nodes.py:820`).
- UTC offsets survive parsing; ambiguous/nonexistent local times are rejected using zoneinfo round trips, and unknown journal capture dates are labeled (`notron/when.py:27`, `notron/when.py:88`, `notron/layout.py:75`).

### Issues

#### Critical (Must Fix)

- None found.

#### Important (Should Fix)

1. **Common relative dates expressed with words bypass unknown-capture clarification.** `notron/when.py:81` recognizes `in` followed only by digits. An observed-only, resumed request such as “Remind me in two weeks to call Sam” or “Remind me in a week to call Sam” returns `needs_confirmation=False`, while “in 2 weeks” correctly returns True. The scheduler then receives the observation timestamp as its capture reference, although the original capture date is unknown. A future inferred date can pass the ordinary past-date guard and create an incorrectly shifted reminder. This violates the required unknown-capture clarification and the report's claim that all observed-only relative scheduling asks for an exact date. Recognize ordinary word-number/article relative expressions, and fail closed when a scheduling date depends on an unknown reference; add regressions exercising both scheduler and resumed pre-effect validation.
   - Focused pure-Python probe: with `captured_at=None`, observed Friday 2026-09-04, processing Monday 2026-09-07 and `resumed=True`, `resolve_time_context` returned False for both worded examples and True for the numeric control. No app/store/provider calls were involved.

2. **An address can authorize a model-invented event duration.** `notron/when.py:124` accepts any `to` followed by a digit as explicit end-time evidence. `duration_error` at `notron/when.py:127` returns no error when no numeric duration exists, so neither `notron/nodes.py:334` nor `notron/executor.py:571` closes that gap. For “Schedule a visit to 123 Main Street on September 9 at 10am,” a synthetic model response supplying an unrequested 11am end is accepted as an Action. The executor uses the same predicates and can create that guessed one-hour event when the remaining checks pass. Require recognizable temporal evidence for an end time (and validate it against the proposed end), or ask for an explicit duration; a destination number must not count. Add a regression proving no action/save for this case.
   - Focused synthetic-scheduler probe: `duration_explicit(request)` returned True, `duration_error(...)` returned `''`, and `nodes.scheduler(..., brain=FakeBrain())` returned one event with `ends='2026-09-09T11:00Z'` and no clarification. No external effect or persistence was attempted.

#### Minor (Nice to Have)

- None required for this gate.

### Checks and Evidence

- Reviewed the supplied `739d888..db9fc14` diff. The initial combined tool output was truncated; recovered the obscured diff ranges in bounded slices. Changed files were separately read only where the diff cut off functions needed to judge recovery/clarification integration (`graph.run_request`, `nodes.writer`, and the tail of `Executor.do`/reconciliation).
- Named risk — recovery checkpoint integration: checked `notron/recovery.py:46` through `notron/recovery.py:104` for Action/State reconstruction and source binding; checked graph continuation and executor recovery branches identified above.
- Named risk — source permission after refreshed context: checked `notron/policy.py:49`, `notron/outbound.py:36`, and `notron/requests.py:364`; refreshed note passages retain IDs/metadata for the existing outbound gate, and source revisions remain checked before effects.
- Named risk — actual clarification delivery: checked `notron/graph.py:142` and `notron/nodes.py:813`; only `✗` stops the graph, while scheduling replies preserve the doer's actual question without model rewriting. The new synthetic graph test also asserts a visible conflict question, no event save, and no success wording (`tests/test_target_resolution.py:198`).
- Implementer's report records 941 passing tests and a clean diff check. Inspected the final tail of `/tmp/task5-full-suite-final.log`, which reaches 100% without visible errors. Did not rerun the suite; root independently owns broad verification. The two focused probes above address uncovered concrete doubts.

### Assessment

**Task quality:** Needs fixes.

**Reasoning:** Stable targeting, durable recovery ordering, and pre-effect clarification delivery are well integrated. Two permissive natural-language evidence checks still allow precisely the unknown-date and guessed-duration behavior Task 5 is intended to prevent.


## First correction re-review

### Spec Compliance

- ✅ Spec compliant for the scoped correction: both original Important findings are addressed in `db9fc14..5784bf8`. No additional blocking issue found in the correction.
- **Finding 1 — ADDRESSED.** `notron/when.py:83` recognizes word/article quantities with temporal units following `in`, `after`, or `within`, as well as `from now` and additional relative markers. The original “in two weeks” and “in a week” cases now require confirmation when capture is unknown. The existing scheduler and executor share this resolver, so the fix also covers resumed pre-effect execution.
- **Finding 2 — ADDRESSED.** `notron/when.py:133` requires a recognizable clock, noon/midnight, or complete ISO timestamp for end evidence. “to 123 Main Street” cannot match. `notron/when.py:145` checks duration and end evidence together, and `notron/when.py:180` compares the requested end against the proposed instant instead of accepting arbitrary model-supplied ends.

### Strengths

- Regression tests cover refusal before inference and refusal of resumed PREPARED work, including zero fake saves and cancellation of the unapplied intent (`tests/test_delayed_requests.py:178`, `tests/test_delayed_requests.py:188`, `tests/test_delayed_requests.py:218`).
- End validation includes negative address/mismatch cases plus positive am/pm, 24-hour, noon, midnight rollover, and explicit-offset examples (`tests/test_delayed_requests.py:210`, `tests/test_delayed_requests.py:243`).
- The correction centralizes duration evidence in shared expressions and validation, preserving the existing scheduler/executor integration without modifying external adapters or recovery ordering (`notron/when.py:140`, `notron/when.py:145`).

### Issues

#### Critical (Must Fix)

- None.

#### Important (Should Fix)

- None remaining within this re-review scope.

#### Minor (Nice to Have)

- None required for acceptance.

### Checks and Limits

- Read the appended implementation report and the supplied correction diff once; evaluated only the two original findings and breakage introduced by their fixes. No additional source-file reads, source changes, commits, subagents, real apps, providers, network, or user-data access.
- The implementer reports 246 passing targeted tests and clean diff whitespace. The new tests exercise behavior at the actual scheduler and resumed executor boundaries; no routine test rerun was performed. Root owns the independent full-suite gate, whose result is not asserted here.
- ⚠️ Native EventKit behavior remains unverified pending P06. The previously documented limitation for context restored after a long downstream checkpoint pause is unchanged. Neither limitation is altered or resolved by this scoped correction.

### Assessment

**Task quality:** Approved for this scoped re-review.

**Reasoning:** Both reproduced authorization gaps are closed at the shared validation layer and covered through the affected production entry points. No new blocking correctness or maintainability issue was found in the correction; final Task 5 acceptance remains subject to root's independent full-suite result.


## Final integration review

# P02 Task 5 — final integration review

**Verdict: changes required for Task 5.** The final cross-path review found one Important gap in `739d888..5784bf8`: ordinary plural-weekday recurrence can be reduced to a one-off reminder.

## Scope and evidence

- Reviewed the Task 5 brief, implementation report, progress decisions, initial review and scoped correction re-review, and the final committed production diff. Inspected unchanged graph/recovery/outbound code only for the named checkpoint, clarification delivery and refreshed-context permission risks.
- Root reports an independent full-suite result at `5784bf8`: **957 passed in 31.32s, exit 0**. This reviewer did not rerun passing suites or independently claim that execution. Read the relevant synthetic scheduling, target, freshness and recovery tests.
- The two original Important findings have an accepted scoped re-review: worded relative-date uncertainty and numeric-address/end-time evidence are corrected at the shared validation layer.
- Review was read-only except this artifact. No source edits, commits, subagents, real app access, live inference, providers, network or user-data access. Concurrent design/handoff edits were preserved. A focused pure-Python scheduler probe used a fake model response and no persistence or external effect.

## Integration checks

- **Safety before effects:** new/PREPARED actions pass shared capture-date, supported-action and guard checks before the effect boundary (`notron/executor.py:518`, `notron/executor.py:531`). Event duration evidence and fresh overlap checks precede persistence/application (`notron/executor.py:570`, `notron/executor.py:588`). Source revision and permission checks remain before `_perform` (`notron/executor.py:591`, `notron/executor.py:597`). Conflict approval binds event kind/op/title/start/end, stable destination, notes and current conflicting IDs/times (`notron/calendar.py:259`); approval comes from the current raw request (`notron/executor.py:580`).
- **Recovery preservation:** immutable action identity is checked before selecting the recovery path (`notron/executor.py:501`). APPLIED/RECEIPTED returns verified evidence, and APPLYING reconciles before any fresh creation validation (`notron/executor.py:507`, `notron/executor.py:511`). Unresolved reconciliation becomes NEEDS_REVIEW without repeating the save (`notron/executor.py:627`). Old PREPARED actions lacking bound identity pause rather than selecting a fresh default (`notron/executor.py:515`).
- **Actual clarification delivery:** known pre-effect refusals cancel existing PREPARED intent and return `needs_confirmation` (`notron/executor.py:459`). The doer emits `?` plus the actual reason (`notron/nodes.py:388`); graph continuation stops only for `✗` (`notron/graph.py:142`), and the scheduling writer preserves the question without model rewriting (`notron/nodes.py:819`). The graph regression asserts a visible conflict question, zero event saves and no success wording (`tests/test_target_resolution.py:198`). Ordinary failures/unknown outcomes retain the failure path.
- **Stable targets:** name resolution returns zero/one/many candidates; explicit IDs filter matching candidates. Chosen IDs are copied into the action and encrypted durable payload before effects (`notron/executor.py:545`, `notron/executor.py:565`, `notron/executor.py:586`). Native create calls use those IDs rather than a fresh default (`notron/calendar.py:74`, `notron/reminders.py:72`); completion uses the selected reminder ID (`notron/executor.py:637`). Existing recurring reminders are refused in selection and guarded again in the native completion script (`notron/executor.py:555`, `notron/reminders.py:98`).
- **Query-time context:** selected IDs receive bounded live metadata/permission validation; changed bodies are reread and checked again, then redacted and bounded (`notron/index.py:201`). Missing/unavailable/changed-again/budget-limited content is omitted with incomplete evidence. Provenance survives retrieval (`notron/nodes.py:175`) and the existing outbound gate (`notron/outbound.py:53`). Writer/planner append deterministic incomplete-context disclosure (`notron/nodes.py:849`, `notron/nodes.py:758`). Calendar/read failures cannot become an empty free-schedule claim (`notron/nodes.py:264`), and overlaps require event identity and usable times (`notron/calendar.py:247`).

## Findings and retained limits

- **Critical:** none identified.
- **Important — plural-weekday recurrence bypasses refusal.** `notron/when.py:115` does not recognize ordinary plural weekdays such as “Mondays.” For “remind me Mondays to call Sam,” a synthetic model response containing only a one-off reminder on September 7 is accepted by `notron/nodes.py:295` and appended as an Action. The response does not need to acknowledge recurrence because the output validator at `notron/nodes.py:314` only rejects recurrence metadata when supplied. The executor repeats the same permissive raw-request predicate at `notron/executor.py:518`; the guard also accepts the one-off action. This violates the task requirement to reject unsupported recurrence rather than partially claiming success. Add plural weekdays to the shared refusal predicate and regressions proving both pre-inference refusal and zero saves from resumed PREPARED execution. Preserve the existing APPLIED/APPLYING ordering.
  - Focused synthetic probe: request “remind me Mondays to call Sam,” observed-only envelope at `2026-09-04T18:00+00:00`, fake response `{kind: reminder, op: create, title: Call Sam, when: 2026-09-07}`. Results: `unsupported=False`, unknown-capture `needs_confirmation=False`, `actions=1`, `answer=''`, and the guard allows the one-off at that fixed observation clock. No app/store/provider/network calls occurred. This was the controller's concrete requested case, not a general NLP audit.
- **Minor:** none required for this gate.
- Native EventKit authorization/bridging, real IDs and reminder timezone/alarm behavior remain the documented **P06 platform gate**. This review makes no live-platform claim.
- Query-time validation is bounded to selected notes. Existing checkpoints can restore context after a later pause (`notron/recovery.py:66`, `notron/graph.py:130`); this is not a guarantee of freshness at arbitrary later receipt time. No atomic transaction spans local state and Apple apps, and no exactly-once claim is supported.
- The conservative supported-English intent checks and the explicit conflict-code restatement workflow retain the report's documented limitations, but the ordinary plural-weekday case above needs correction before acceptance. Default startup remains behind P06; this review does not accept Task 6 or authorize production activation.


## Final correction approval

# P02 Task 5 — final scoped re-review

**Original finding: ADDRESSED.**

**Final requested Task 5 integration verdict: approved on code review at `2b7176a`, subject to root's independent final full-suite gate.** No blocking finding remains from the Task 5 reviews. This re-review supersedes the plural-weekday change request in `final-review.md`.

## Reviewed correction

- Read the supplied `5784bf8..2b7176a` diff and appended implementation report. Scope was the original plural-weekday finding and breakage introduced by this correction only.
- `notron/when.py:115` now recognizes all seven plural weekdays using the existing `WEEKDAYS` values, case-insensitively and with word boundaries. The original “remind me Mondays to call Sam” request therefore reaches the shared unsupported-request refusal before scheduler inference and before resumed PREPARED effects.
- The scheduler regression covers Monday through Sunday and fails if inference occurs (`tests/test_delayed_requests.py:260`). The resumed PREPARED regression asserts no save, a truthful clarification and CANCELLED intent (`tests/test_delayed_requests.py:269`).
- APPLIED/APPLYING controls exercise the same request and assert recovery of the existing external ID without another save (`tests/test_delayed_requests.py:286`). Production recovery ordering is untouched by the three-line predicate addition.
- Word boundaries preserve singular weekday handling and avoid matching plural weekday text embedded inside a larger word. This is the narrow refusal requested; it does not change date parsing, target binding, context retrieval or clarification delivery.

## Findings and verification limits

- Critical: none.
- Important: none remaining in this scope.
- Minor: none required for acceptance.
- Implementer reports eight failing regressions before the fix, eight passing after it, plus two passing recovery controls; final targeted set is **256 passed**, exit 0. This reviewer inspected those assertions and did not rerun passing suites. Root's final full suite was still running when this review was written, so no final full-suite result is asserted here.
- Native EventKit behavior remains a P06 gate. Selected context is validated at query time, with the documented limitation after later checkpoint pauses. No exactly-once or live-platform claim is made.
- No source edits, commits, subagents, live app/provider/network access or user-data access. Only this review artifact was written; concurrent documentation edits were preserved.


