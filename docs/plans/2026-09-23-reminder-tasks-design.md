# Reminders as the task inbox — design (2026-09-23)

Two fixed surfaces, nothing else to learn:

- **A. Brain Dump** stays in Notes (unchanged).
- **B. Tasks** come in through a Reminders list called **Notron**; answers land in Notes;
  approvals and "done" come back as reminders that buzz the phone.

Why Reminders: Siri files "remind me to X in Notron" into a named list reliably,
EventKit reads it in ~0.1 s (a note lookup costs ~1 s), and a reminder can buzz a
phone and a watch — a note cannot. The new Siri answers quick questions itself;
Notron's edge is long-running, approved, contained work with a durable receipt.

## Flow

1. Listener polls the Notron list (EventKit, every few seconds). Reminders Notron
   created itself (Approve / Done) are skipped by id.
2. **Where it goes.** If project channels exist besides the Tasks channel, Nemotron
   Super picks one (`{"channel": name|"none"}`), validated against the registry in
   code; otherwise it is the Tasks channel. No model runs when there is one choice.
3. The request runs through the ordinary graph as a channel request
   (`trigger="reminder"`, request id `reminder:<id>`, so it can never run twice).
   The reply is appended at the end of the note, opening with the reminder's words.
   Nemotron's `project` node decides question vs task exactly as for a typed line.
4. When the reply lands, the inbox reminder is ticked (taken). Never deleted.
5. **Approve.** When a brief is proposed for a task that came from a reminder, code
   creates `Approve: <goal>` with an alarm now. Ticking it approves *that task id
   and digest* only (the mapping lives in the encrypted task store). Typing "go"
   in the note still works; the approve reminder is then ticked for you. Expired
   or cancelled briefs have their approve reminder ticked too.
6. **Done.** When the report lands in the note, code creates `✅ Done: <goal>`
   with an alarm now.

## Non-code jobs

A channel with `run` + an agent but **no repository** is a workspace channel.
The agent works in an empty private folder (same kernel fence, same tool limits:
Read/Edit/Write in its folder only, no shell, no web). Its files are copied to
`~/Documents/Notron/<date> <goal>/` and Nemotron reviews a preview of them
against its brief. Drafts, plans, CSV spreadsheets (open in Numbers). Nothing is
sent anywhere. Reading the user's PDFs is out of scope for v1 (the agent has no
access to files outside its folder by design).

`notron tasks setup` creates the `Notron Tasks` channel (research + run, Claude)
and checks the Reminders list exists.

## Deliberate choices

- The Approve / Done reminders are created by plain code with code-built titles,
  outside the Executor's model-proposed action pipeline. No model chooses to
  create them; idempotent via ids recorded in the task store.
- The Tasks note is a channel titled `Notron Tasks` (Siri-addressable, same grant,
  guard and executor rules as every channel) rather than a new note type.
