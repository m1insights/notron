# A divergent write no longer freezes a note forever — design

Task brief: `2026-09-23-log-frozen-task.md`.

## What was measured (live, 2026-09-23, read-only)

Two staged backups have been pending since 2026-09-22 20:45 UTC, not one:

| Note | Write | Live body | Proof it landed |
|---|---|---|---|
| `📊 Log` | receipt append, `post_write_divergence` | visible text equals the intended body exactly | yes |
| `📥 Ask Notron` | reply append ("Hello! I'm Notron…"), `post_write_divergence` | user has since deleted the question and that reply | no |

So `📥 Ask Notron` has refused every reply for the same day. Of the 83 queued
receipts: 46 never attempted, 36 refused (`write_failed`, because of the held
backup — nothing written), 1 the divergent write itself.

## Answers to the brief's questions

1. **Re-verify: yes, for additive writes.** A held append/insert is settled
   (operation → APPLIED, backup promoted) when the text it added occurs in the
   live note more often than in the saved pre-write body. That is proof our
   write put it there, and it survives later user edits elsewhere in the note.
   Promotion keeps the pre-write body as the undo copy, so nothing is lost;
   a later restore still needs the exact post-write revision or a recovery copy.
   `replace`/`mark`/`restore` are never settled automatically.
2. **Explicit clear: yes.** `notron review` lists held writes beside held
   requests; `review dismiss <id>` closes the operation (CANCELLED) and keeps
   its backup as the note's undo copy. Dismissing a request also releases the
   backups of its own writes.
3. **No lenient rule for `📊 Log`.** The same proof applies to every note. The
   Log case passes it on its merits; there is no Log-specific branch.
4. **Yes, one user-facing note** (`📥 Ask Notron`) — handled by 2.

## Where settlement runs

- `Executor._locked_write`, just before the backup is staged, when the target
  holds one: uses the body it already read, so it costs no Notes call.
- `audit.drain`, when the Log holds one: one Log read, at most every ten
  minutes while it stays unproven.

## Receipts refused because a backup was held

The executor now records that refusal as `backup_held` instead of
`write_failed`. `audit.drain` treats it as retryable once the Log is free:
the cancelled child write is removed and a fresh one prepared. The 36 historic
`write_failed` refusals are cleared once by hand after merge (evidence: all
dated inside the freeze window).

The one-off clear, run with the listener off, used exactly this:
`DELETE FROM operations WHERE operation_id LIKE 'audit:%:write' AND status='cancelled'
AND failure_code='write_failed' AND updated_at>='2026-09-22T20:45'` (36 rows).
