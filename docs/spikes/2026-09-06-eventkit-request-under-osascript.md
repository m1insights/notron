# Spike: does an EventKit access request call back under `osascript`?

**Asked:** 2026-09-06. **Answered:** 2026-09-07, macOS 26.2.
**Verdict: no. Branch B.** A script cannot ask macOS for Calendar or Reminders
access. The grant has to come from somewhere with a real bundle identity.

## Why it was worth asking

CLAUDE.md already records the Speech precedent: under `osascript`,
`SFSpeechRecognizer.requestAuthorization`'s callback never fires, because
`osascript`'s bundle carries no usage string, so a design that waits for a grant
hangs for ever. EventKit might or might not behave the same way. Task 2 of
`docs/plans/2026-09-06-calendar-reminders-robustness.md` was written both ways
and this spike picked the branch.

## What was run

Two probes, run twice each — once as a child of the terminal, once from a
one-shot launchd job, which is the identity the listener actually runs under
(`launchd → .venv/bin/python → osascript`, see `watch.plist`).

1. **Status** — `EKEventStore.authorizationStatusForEntityType` for events and
   reminders.
2. **Request** — all three request selectors, each on its own store, each
   pumping the same `NSRunLoop` the existing `eventkit.AWAIT` pumps, with an
   8–10s deadline:
   `requestFullAccessToEventsWithCompletion`,
   `requestFullAccessToRemindersWithCompletion`,
   `requestAccessToEntityTypeCompletion` (the pre-14 API).
3. **Read** — `calendarsForEntityType` plus a seven-day
   `predicateForEventsWithStartDateEndDateCalendars`.

All three selectors resolve as `function` under JXA in both identities. None of
them throws in either identity.

## Results

| | from the terminal | from launchd |
|---|---|---|
| `authorizationStatus` (events / reminders) | **3 / 3** (full access) | **0 / 0** (not determined) |
| `requestFullAccessToEvents…` called back | **no** | **no** |
| `requestFullAccessToReminders…` called back | **no** | **no** |
| `requestAccessToEntityType…` called back | yes → granted | **no** |
| calendars visible | 5 | **0** |
| events in the next 7 days | 3 | **0** |

Three things fall out of that table.

**The listener is at `notDetermined`, not denied.** The status the terminal sees
— the status `notron permissions` reports, because it runs from the terminal —
is a different process's status. There was never a decision to reverse; nobody
was ever asked.

**Asking does not work from a script.** Not with the modern API, and not with
the legacy one. The legacy call *appears* to work from the terminal, and that is
a trap: it returns `granted: true` instantly there because the status is already
3 and it has nothing to ask. From launchd, at `notDetermined` — the only state
where a request would mean anything — it hangs like the other two and the
deadline expires. Exactly the Speech failure mode.

**A blind read is indistinguishable from a free week.** Zero calendars, zero
events, no exception, no error. This is the mechanism behind the four
`agenda: 125 chars` lines in `.notron/listen.log` — 125 is the exact length of
"Nothing in the calendar today" plus "Nothing outstanding in Reminders". She has
never once seen a real event or reminder from the background listener, and had
no way to know it.

## What this decides

- **Task 2 (`eventkit.ensure_access`) is dead.** Do not build it. A request that
  cannot call back is a 10-second stall added to the first read of every
  process, buying nothing. Building it would repeat the Speech mistake with the
  spike doc that warns against it sitting in the same repo.
- **Tasks 3 and 4 carry Phase A on their own,** and their value goes up, not
  down: if the grant has to be made by hand, the software's whole job is to say
  loudly and exactly which grant is missing. A silent empty read is now the
  only bug that matters here.
- `mac/Info.plist` gains the usage strings (Task 4) so the Mac app can ask in
  Swift, where a bundle identity exists and the prompt actually appears.
- Until the listener has a signed, stable identity of its own
  (`docs/production/plans/06-mac-distribution.md`, Task 2), its grant is manual:
  System Settings → Privacy & Security → Calendars / Reminders. Note what the
  table above implies about that: at `notDetermined` with no prompt ever fired,
  there may be no row to switch on, so the practical workaround today is to run
  the listener in the foreground from an already-approved terminal, or to give
  it an identity the user can approve. That is P06's problem, and this spike is
  the reason P06 is on the critical path rather than being packaging polish.
