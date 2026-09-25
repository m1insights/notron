# Email to-dos (2026-09-25, revised same day)

**Goal.** Keep the user on top of what their email asks of them. They forget an
email for days and forget the thing it asked. Drafting replies was built first and
dropped: "There's no way I can give Notron enough context to reply to my emails."

**Flow (once a day in `notron morning`, or `notron mail`).**
1. Code: newest 100 inbox headers per account → new, in window, not seen, not a
   machine sender (key people exempt).
2. Super: shortlist ≤12 from sender+subject. Key people's mail is always read.
3. Super: one verb-first to-do per email that asks something (+ when, + a stated
   deadline only).
4. Code: Guard `check_action`, claim, then a reminder in Reminders "Email" with a
   `message://` link to the email; today/deadline items get an alarm.
5. Code, no model, every pass: ticked → done; a sent "Re:" of that subject newer
   than the email → ticked for them; open ≥3 days → "Still waiting on you".
6. The list is appended to `Notron Mail` as her turn (explicit reply capability).

**Mail is read only.** No script sends, replies, saves, moves or deletes (test).

**Measured 2026-09-25.** Headers 100/inbox: ~4 s synced, ~2 min while Gmail
syncs 85k messages. Bodies ~0.5–1 s each synced (read ≤4 per request).
`whose id is` returns All Mail copies whose content fails; address by index+id.
First live pass: 44 new → 4 flagged in 5.7 min (during sync).
