# Session handoff

For R-series work also record: plugin protocol/version/code digest; exact SDK,
provider/model and credential mode; qualified OS/Siri configuration; task/approval
and grant schema changes; duplicate-start/cancellation/recovery evidence; Notes
delivery status separately from external completion; remaining real-device or
provider checks; impact on October 1/23/30 gates. Do not publish identifiers or
content from personal sessions.

Copy to `docs/production/handoffs/YYYY-MM-DD-PNN-or-RNN-task-N.md` at an implementation checkpoint. Replace the instructional fields with observed facts; leave no misleading completion claim.

## Work identity

- Plan and task IDs:
- Branch/worktree and commit:
- Starting workspace changes not owned by this task:
- Implemented behavior:
- Explicitly not implemented:

## Evidence

| Command or manual case | Actual result / exit code | Artifact |
|---|---|---|

Record whether tests used synthetic data, a test Apple account, real devices or provider calls. Include OS/app versions for manual cases. Do not paste secrets, personal note text or tokens.

## State and recovery

- Schema/configuration changes and migrations exercised:
- What is running and how to stop it:
- External services touched:
- Backup / rollback procedure exercised:
- Remaining risks or failed cases:

## Next session

- Exact next task and first command:
- Inputs that block only that task:
- Decisions made, with links to updated contracts:
- Roadmap checkboxes changed and evidence supporting each:
