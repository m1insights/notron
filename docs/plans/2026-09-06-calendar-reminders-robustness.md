# Calendar & Reminders — outage fix and robustness plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the background listener actually able to read and write the user's
Calendar and Reminders, say so honestly when it cannot, and close the five
correctness gaps found in the 2026-09-06 audit — plus stop macOS autocorrecting
"Notron" into "Norton".

**Architecture:** Three layers, in this order. (A) *Access* — nothing in the
codebase has ever asked macOS for EventKit permission, so the launchd listener
has none: reads come back empty with no error and writes fail. (B) *Honesty* —
the real `NSError` is captured in a variable and thrown away; a blind calendar
is currently indistinguishable from a free day. (C) *Correctness* — locale-bound
date formatters, retry-driven duplicates, all-day events, and the "today" window.
The name fix is a one-line regex in the core plus a `learnWord` call in the Mac
app's onboarding.

**Tech Stack:** Python 3.11, JXA/`osascript` over EventKit, Swift/SwiftUI (mac/),
pytest with fake callers (no real EventKit in tests, ever).

---

## What we actually measured on 2026-09-06

Evidence behind Phase A, so nobody re-derives it:

- `EKEventStore.authorizationStatusForEntityType` from the Claude Code shell:
  `{"events": 3, "reminders": 3}` — full access. From that shell
  `reminders.summary()` returns dozens of real reminders.
- `.notron/listen.log`: **every** `agenda:` line, on every run since the feature
  shipped, reads `agenda: 125 chars of real commitments`. 125 is exactly the
  length of `"## In your calendar today\nNothing in the calendar today.\n\n##
  Still outstanding in Reminders\nNothing outstanding in Reminders."` The
  listener has never once seen a calendar event or a reminder.
- Her exact `calendar._CREATE` script, run from the same shell with
  `commit:false` so nothing persisted, returned
  `{"id": "5B3E020E-…", "calendar": "MRS"}`. **The script is correct.**
- `grep -rn "requestAccess\|requestFullAccess" notron/ mac/Sources/` → no hits.
  We never ask. macOS never prompts. Status stays `notDetermined` for any
  identity that was not separately approved, and `calendarsForEntityType`
  then returns an empty array with no error — which is why a save has no
  calendar to write to and fails.
- `mac/Info.plist` has `NSSiriUsageDescription` and nothing else — no
  `NSCalendarsFullAccessUsageDescription`, `NSRemindersFullAccessUsageDescription`
  or `NSAppleEventsUsageDescription`.
- macOS 26.2. `requestFullAccessToEventsWithCompletion`,
  `requestFullAccessToRemindersWithCompletion` and the legacy
  `requestAccessToEntityTypeCompletion` all resolve as functions under JXA.

---

## Phase A — the outage

### Task 1: Spike — does an access request work under `osascript`?

This is a 20-minute experiment, not a feature. It decides Task 2's shape.
CLAUDE.md records the Speech precedent: under `osascript` the Speech
authorization callback *never fires*, because `osascript`'s bundle carries no
usage string. EventKit may behave the same way. **Find out before building on it.**

**Files:**
- Create: `docs/spikes/2026-09-06-eventkit-request-under-osascript.md` (findings only)

**Step 1: Write the probe**

```bash
cat > /tmp/req.js <<'EOF'
ObjC.import('EventKit');
ObjC.import('Foundation');
var store = $.EKEventStore.alloc.init;
var got = null;
store.requestFullAccessToEventsWithCompletion(function (ok, err) { got = !!ok; });
var deadline = $.NSDate.dateWithTimeIntervalSinceNow(10);
while (got === null && $.NSDate.date.compare(deadline) < 0) {
  $.NSRunLoop.currentRunLoop.runModeBeforeDate(
    $.NSDefaultRunLoopMode, $.NSDate.dateWithTimeIntervalSinceNow(0.02));
}
JSON.stringify({called_back: got !== null, granted: got});
EOF
osascript -l JavaScript /tmp/req.js
```

**Step 2: Run it from the listener's identity**

Temporarily add a `notron eventkit-request` debug command (or run the same JS
from a launchd-spawned one-shot job) so the probe runs as
`launchd → .venv/bin/python → osascript`, not as a child of the terminal.
Expected: this is the case that matters.

**Step 3: Record the answer in the spike doc, then branch**

- `called_back: true` → **Branch A.** Task 2 as written below.
- `called_back: false` (the Speech failure mode) → **Branch B.** The request must
  come from a real bundle. Skip to Task 3, and add: the Mac app requests EventKit
  access in Swift (`EKEventStore().requestFullAccessToEvents`) with the usage
  strings from Task 4, and the listener is documented as needing a manual grant in
  System Settings → Privacy & Security → Calendars / Reminders until P06's signed
  helper identity lands (`docs/production/plans/06-mac-distribution.md`, Task 2).

**Step 4: Commit the spike doc**

```bash
git add docs/spikes/2026-09-06-eventkit-request-under-osascript.md
git commit -m "docs: whether EventKit's access request calls back under osascript"
```

---

### Task 2 (Branch A): `eventkit.ensure_access()`

**Files:**
- Modify: `notron/eventkit.py`
- Test: `tests/test_eventkit.py`

**Step 1: Write the failing tests**

```python
def test_access_is_requested_once_per_process():
    calls = []
    eventkit.ensure_access(runner=lambda s, t: calls.append(s) or '{"events": true, "reminders": true}')
    eventkit.ensure_access(runner=lambda s, t: calls.append(s) or '{"events": true, "reminders": true}')
    assert len(calls) == 1, "asking macOS on every read would prompt in a loop"


def test_a_request_that_never_calls_back_is_an_error_not_a_silent_no():
    with pytest.raises(eventkit.EventKitError):
        eventkit.ensure_access(runner=lambda s, t: "")
```

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_eventkit.py -k access -v`
Expected: FAIL, `module 'notron.eventkit' has no attribute 'ensure_access'`

**Step 3: Implement**

Add a `_REQUEST` JXA snippet that calls both
`requestFullAccessToEventsWithCompletion` and
`requestFullAccessToRemindersWithCompletion`, pumping the existing `awaitDone`
run loop (this is exactly the trap `AWAIT` already exists for), and a
module-level `_asked` flag so it runs at most once per process. Call it from
`run()` before the first body executes — one place, so every read and write
inherits it.

**Step 4: Verify**

Run: `.venv/bin/python -m pytest tests/test_eventkit.py -v` → PASS
Run: `.venv/bin/python -m pytest tests -q` → 361+ pass

**Step 5: Commit**

```bash
git add notron/eventkit.py tests/test_eventkit.py
git commit -m "fix(eventkit): ask macOS for access instead of assuming it"
```

---

### Task 3: Say the real reason, not "app said no"

Two separate losses of information, both in the write path.

**Files:**
- Modify: `notron/calendar.py:65-85` (`_CREATE`), `notron/reminders.py:56-100`
  (`_CREATE`, `_COMPLETE`)
- Modify: `notron/executor.py:160-166` (the `except Exception` message)
- Test: `tests/test_calendar.py`, `tests/test_reminders.py`, `tests/test_actions.py`

**Step 1: Write the failing test**

```python
def test_a_failed_save_says_what_macos_actually_said():
    def caller(_body):
        return {"error": "save failed", "why": "Calendar access denied"}
    with pytest.raises(eventkit.EventKitError, match="Calendar access denied"):
        calendar.create("x", start_iso="2026-09-07T11:00", caller=caller)
```

**Step 2: Run it** → FAIL (the `why` key is ignored today).

**Step 3: Implement**

In each JXA snippet, read the `Ref()` after the save and put
`err[0].localizedDescription` into the JSON as `why` (it is already captured and
never looked at). In Python, append `why` to the raised message. In
`executor.do`, replace `f"{action.kind} app said no ({type(e).__name__})"` with
the exception's own text, truncated — the user should read
"Calendar access denied", not a Python class name.

**Step 4: Verify** → `.venv/bin/python -m pytest tests -q`

**Step 5: Commit**

```bash
git add notron/calendar.py notron/reminders.py notron/executor.py tests/
git commit -m "fix: a failed calendar or reminder write says what macOS said"
```

---

### Task 4: A blind calendar must not look like a free day

Today `permissions.check()` runs from the terminal (`notron permissions`, the Mac
app) — the one process that is *not* the one doing the work. It reports full
access while the listener sees nothing.

**Files:**
- Modify: `notron/nodes.py:262-280` (`agenda`), `notron/watch.py` (startup)
- Modify: `mac/Info.plist` (usage strings)
- Test: `tests/test_nodes.py`, `tests/test_permissions.py`

**Step 1: Write the failing test**

```python
def test_an_unreadable_calendar_is_not_reported_as_a_free_day():
    state = State(intent="schedule")
    nodes.agenda(state, checker=lambda: [Check("Calendar", False, "is denied", "")])
    assert "Nothing in the calendar" not in state.agenda
    assert "cannot read" in state.agenda.lower()
```

**Step 2: Run it** → FAIL.

**Step 3: Implement**

- `agenda` asks `permissions.check()` (cached for the life of the process) before
  believing an empty read. If Calendar or Reminders is not `ok`, the agenda text
  says so in words the model is given, so she never plans around a day she could
  not see.
- The listener runs the same check once at startup and prints the failing app and
  its fix into `.notron/listen.log`, so the log answers "why is she blind"
  without a second command.
- Add `NSCalendarsFullAccessUsageDescription`,
  `NSRemindersFullAccessUsageDescription` and `NSAppleEventsUsageDescription` to
  `mac/Info.plist` — required for Branch B, harmless in Branch A.

**Step 4: Verify** → full suite.

**Step 5: Commit**

```bash
git add notron/nodes.py notron/watch.py mac/Info.plist tests/
git commit -m "fix: she says her calendar is unreadable instead of planning an empty day"
```

---

## Phase B — the five robustness gaps

### Task 5: Pin the date formatters (wrong-date risk)

`NSDateFormatter` with a fixed `dateFormat` and no locale uses the Mac's region.
On a non-Gregorian region setting `yyyy` is not the year we mean, and
`dateFromString` returns nil — a wrong date, or a silent save failure. This
machine happens to be `en_US`/`gregorian`; the next machine is not our call.
`NSCalendar.currentCalendar` in `reminders._CREATE` has the same problem.

**Files:**
- Modify: `notron/calendar.py:32-85`, `notron/reminders.py:24-100`
- Test: `tests/test_calendar.py`, `tests/test_reminders.py`

**Step 1: Write the failing test** — assert every script that builds an
`NSDateFormatter` also sets `locale` to `en_US_POSIX` and a Gregorian calendar,
the same shape as the existing
`test_a_read_is_always_a_bounded_window` guard-rail test:

```python
def test_every_date_formatter_is_pinned_to_a_fixed_locale():
    for script in (calendar._WINDOW, calendar._CREATE, reminders._OPEN, reminders._CREATE):
        assert "NSDateFormatter" not in script or "en_US_POSIX" in script
```

**Step 2: Run it** → FAIL (four scripts, none pinned).

**Step 3: Implement** — in each snippet, after `f.dateFormat = …`:
`f.locale = $.NSLocale.localeWithLocaleIdentifier('en_US_POSIX');` and
`f.calendar = $.NSCalendar.calendarWithIdentifier($.NSCalendarIdentifierGregorian);`
Use the same pinned calendar for `dueDateComponents` instead of
`NSCalendar.currentCalendar`.

**Step 4: Verify** — full suite, then a live round trip from the shell:
create an event for tomorrow with `commit:false` and confirm the returned start
date matches what was asked.

**Step 5: Commit**

```bash
git add notron/calendar.py notron/reminders.py tests/
git commit -m "fix: a date crossing into EventKit no longer depends on the Mac's region"
```

---

### Task 6: A retry must not book the same thing twice

`doer` runs before `writer` on purpose. But if the note write then fails ("the
note changed while she was writing", `notron/executor.py:122`), the watcher
retries the whole graph (`notron/watch.py:187`, `MAX_TRIES = 2`) — scheduler
again, doer again, **second identical reminder**. Nothing dedupes.

**Files:**
- Create: `tests/test_watch.py::test_a_retried_question_does_not_book_it_twice`
- Modify: `notron/executor.py`, `notron/state.py`, `notron/watch.py`

**Step 1: Write the failing test** — run the graph twice for the same question
text with a fake EventKit caller that counts creates; assert the count is 1.

**Step 2: Run it** → FAIL (count is 2).

**Step 3: Implement** — the Executor records applied actions in
`.notron/actions.json` keyed by a fingerprint of `(kind, op, title, when)` with a
timestamp, and refuses a duplicate inside a short window (say 10 minutes),
returning the original reference and "already set". Plain code, no model, same
shape as `filer.json` and `undo.py`. This also protects against the user asking
twice by accident, which is the more common real-world case.

**Step 4: Verify** → full suite.

**Step 5: Commit**

```bash
git add notron/executor.py notron/state.py notron/watch.py tests/
git commit -m "fix: a retried question can no longer book the same thing twice"
```

---

### Task 7: All-day events

`calendar._WINDOW` never reads `isAllDay`, so an all-day event renders as
`- 00:00 Team offsite` and a multi-day one appears only on its first day.

**Files:**
- Modify: `notron/calendar.py` (`_WINDOW`, `Event`, `brief`, `week`)
- Test: `tests/test_calendar.py`

**Step 1: Write the failing tests** — an all-day event renders as
`- All day — Team offsite` with no time, and a three-day event appears on all
three days of `week()`.

**Step 2: Run** → FAIL.

**Step 3: Implement** — add `all_day: bool` and an `end` that is honoured when
grouping by day.

**Step 4: Verify.** **Step 5: Commit.**

```bash
git commit -m "fix(calendar): an all-day event is not an event at midnight"
```

---

### Task 8: "Today" should mean today, not "from now on"

`calendar.brief()` calls `window(days=2)`, whose predicate starts at *now*. Run
at 10:00, a 09:00 meeting is gone — so `notron agenda` and the morning routine
under-report the day.

**Files:**
- Modify: `notron/calendar.py` (`brief`, `window` gains a start-of-day option)
- Test: `tests/test_calendar.py`

Same five steps. Test: with a fake caller and a fixed `on=`, an event earlier the
same day still appears in `brief()`.

```bash
git commit -m "fix(calendar): today's brief still shows this morning's meetings"
```

---

## Phase C — she answers to "Norton"

macOS autocorrects "Notron" to "Norton" as you type. Two halves: teach the Mac
the word (Mac only), and accept the misspelling everywhere (works on iPhone too).

### Task 9: Accept the autocorrected name

**Files:**
- Modify: `notron/conversation.py:26`
- Test: `tests/test_conversation.py`

**Step 1: Write the failing test**

```python
def test_she_answers_to_what_autocorrect_makes_of_her_name():
    for tag in ("@notron", "@Norton", "#norton", "@nortron", "@notrn"):
        assert conversation.TAG.search(f"hey {tag} what's on today")
```

**Step 2: Run** → FAIL on everything but `@notron`.

**Step 3: Implement** — one regex:
`TAG = re.compile(r"(?:^|\s)[#@](?:notron|nortron|norton|notrn)\b", re.I)`.
Check every other place the literal name is matched (`notron/mentions.py`,
`notron/nodes.py` `FILE_WORDS`) uses `conversation.TAG` rather than its own
string, and route any stragglers through it. **Her own writing never changes** —
she still signs `**Notron:**`; `conversation.SIGNATURE` is untouched.

**Step 4: Verify** → full suite.

**Step 5: Commit**

```bash
git add notron/conversation.py tests/test_conversation.py
git commit -m "fix: she answers to Norton, because autocorrect insists"
```

---

### Task 10: Onboarding teaches macOS the word

Decision: **do it automatically, no toggle.** Learning a proper noun is not a
preference and a toggle nobody understands is worse than a spelling that just
works. It is reversible by the user in any text field (right-click → Unlearn
Spelling), and it writes one line to `~/Library/Spelling/LocalDictionary`, which
is currently empty.

**Files:**
- Modify: `mac/Sources/Notron/Onboarding.swift` (`markDone`, or the `.talk` step)
- Modify: `mac/Sources/Notron/OnboardingView.swift` (one line of copy)
- Test: `mac/Tests/` if a target exists; otherwise verify by hand (below)

**Step 1: Implement**

```swift
/// macOS autocorrects "Notron" to "Norton" the first time you type it in Notes,
/// and she is addressed by name in every note. Teaching the system speller once,
/// during onboarding, is cheaper than the user fighting it for ever. The core
/// also accepts "Norton" (conversation.TAG) because this only fixes the Mac —
/// the iPhone has its own dictionary we cannot reach.
private func teachTheSpellerHerName() {
    NSSpellChecker.shared.learnWord("Notron")
}
```

Call it from the `.talk` step (the screen that teaches how to address her), so
the word is learned before the user ever types it.

**Step 2: Copy** — one line under the "how to talk to her" screen, in the plain
warm register of `docs/design/02-screens.md`: *"We've taught your Mac her name,
so it stops changing Notron to Norton."*

**Step 3: Verify by hand**

```bash
cat ~/Library/Spelling/LocalDictionary   # expect: Notron
```

Then type "Notron" in Notes and confirm it is not corrected.

**Step 4: Commit**

```bash
git add mac/Sources/Notron/Onboarding.swift mac/Sources/Notron/OnboardingView.swift
git commit -m "feat(mac): onboarding teaches your Mac that Notron is a word"
```

**Note for the DMG:** nothing extra to ship. `NSSpellChecker` is AppKit and the
learned word lands in the user's own `~/Library/Spelling/LocalDictionary`,
outside the bundle — no entitlement, no installer step, no migration.

---

## Order and estimates

| # | Task | Why it is here | Estimate |
|---|---|---|---|
| 1 | Spike: does the access request call back? | Decides tasks 2–4 | 20 min |
| 2 | `eventkit.ensure_access()` | The outage | 45 min |
| 3 | Say the real macOS error | The outage, and every future one | 45 min |
| 4 | Blind calendar ≠ free day | Stops silent wrong plans | 1 hr |
| 5 | Pin date formatters | Wrong-date risk | 30 min |
| 6 | No double-booking on retry | Duplicate reminders | 1 hr |
| 9 | Answer to "Norton" | One line, high daily value | 20 min |
| 10 | Onboarding teaches the speller | Mac-side half of the same | 30 min |
| 7 | All-day events | Polish | 30 min |
| 8 | Today means today | Polish | 20 min |

**Total: ~6 hours.** Tasks 1–4 are the outage and should land together.

## Invariants this plan must not break

- **#6** — a calendar event may only ever be created. Nothing in Task 6's
  dedupe store may grow into an update or delete path.
- **#7** — a reminder may be created or completed, never deleted.
- **#3** — no model runs in the write path. Every task above is plain code.
- **#4** — every write, allowed or blocked, is logged to 📊 Log. Task 6's
  "already set" outcome is a blocked write and must be logged as one.
- Tests never touch the real Notes, Calendar or Reminders. Every EventKit test
  passes a fake `caller`; `conftest.FakeNotesApp` stays autouse.
