# Next build — Reminders and Calendar

Two integrations that turn JUNO from something that writes notes into something
that runs a life. Both apps are fully scriptable and were verified responsive on
this Mac.

## Why Reminders first

It fixes a real flaw rather than adding a feature. Apple Notes **cannot** be given
tappable checkboxes by script — JUNO currently writes `☐` and `✅` as plain text and
the user has to tell her when something is done.

Reminders gives all of it for free: real checkboxes you tap, real notifications,
real due dates, iPhone sync. "Juno, remind me Thursday" becoming a reminder that
actually buzzes is the most demonstrable thing we could ship, and it is the demo
moment for the hackathon video.

## Shape of the work

A new `juno/reminders.py` and `juno/calendar.py`, each mirroring `notes.py`: bulk
AppleScript queries only, everything through `applescript.run` so it takes the same
lock. **Read the Performance section of CLAUDE.md before writing a single query** —
the same one-at-a-time trap that cost 106 seconds on Notes applies here.

Then two new intents in the router (`remind` and `schedule`), and the Guard extended
so it governs these writes the way it governs notes.

## What she should be able to do

**Reminders**
- Create one from anything said in Notes: "remind me to call the pharmacy Thursday".
- Read open reminders so `☀️ Today` reflects what is actually outstanding.
- Tick one off when the user says it is done.
- Never delete a reminder. Completing is not deleting.

**Calendar**
- Read today's and this week's events, so the daily plan is built around real
  commitments instead of guesses. This alone changes the quality of `☀️ Today`
  more than anything else on the roadmap.
- Create an event when asked, with an explicit confirmation written back.
- Never move or delete an existing event. The user's `📌 About Me` already says
  "never move anything already in my calendar without asking" — the Guard should
  enforce that in code, not leave it to the model.

## Guard rules to add

1. Calendar events are **create-only**. No edits, no deletions.
2. Reminders may be created and completed. Never deleted.
3. Anything scheduled must respect the standing instructions in `📌 About Me`
   (the user's "nothing before 9am" rule is the test case).
4. Every calendar and reminder write lands in `📊 Log` like every other write.

## Where it plugs into the graph

`watcher → router → retriever → researcher → planner → writer → executor`

- **Router** gains `remind` and `schedule` intents.
- **Retriever** unchanged.
- **Planner** reads the calendar before drafting `☀️ Today` or `🗓️ This Week`.
- **Executor** gains reminder and event writes, each behind the Guard.

## Watch out for

- Both apps need their own macOS Automation approval, separate from Notes. The
  launchd listener will hang on its first request until the user grants it — same
  trap as Notes. Warm up and log clearly.
- Reminders and Calendar are their own single-threaded script targets. Do not
  interleave a sweep of one with a query to another inside the same poll.
- Recurring events read strangely over AppleScript. Read a date range, not a rule.

## Rough size

Reminders about a day; Calendar about a day; Guard rules and tests half a day.
The demo win — a real reminder buzzing on the phone after being typed into Notes —
arrives at the end of day one.
