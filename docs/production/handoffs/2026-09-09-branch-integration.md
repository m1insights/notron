# Main / production branch reconciliation — September 9

## Outcome

Reconciled production `fd77a57` (P01 Tasks 1–5, P02 Tasks 1–6) with main `7bfb5fd`
(attachments, calendar/reminder robustness, full tagged-note context, pin guidance).
The earlier main-only roadmap audit missed the production worktree and is superseded.
P03 Task 1 remains the next feature task. No live processing or release is enabled.

## Integration choices

- Preserve fail-closed policy, provenance, fixed provider/Apple transports, encrypted
  state, durable request/operation IDs, revision-bound undo and worker lifecycle.
- Replace main's best-effort `booked.py` text/time cache with the durable operation
  ledger. A retry reuses one operation; a genuinely new identical request remains
  distinct. Legacy `actions.json` participates in explicit offline migration.
- Bring image/text/audio attachments through production privacy and storage rules.
  Binary uploads require readable note provenance; retries recheck it. Cache originals
  and extracted words encrypted. Use private temporary files for native tools and
  fixed JXA argv for Speech. Never bulk-list ignored-note attachments.
- Migrate legacy media into encrypted recovery copies only: old files lack trustworthy
  source identity. Preserve originals until explicit acceptance; test interrupted
  acceptance and changed/symlink inventory refusal. No actual migration was run.
- Carry attachments through encrypted graph checkpoints and retain note provenance
  in retrieval/writes. Keep unsupported files visibly unread.
- Bind photo replies to the registered plain Ask note before inference, retaining
  original source revision/anchor checks. Successful delivery retires the original
  occurrence through the ledger; no plaintext question list or repeat inference.
- Keep stable target IDs/fixed argv while adding pinned native date formats, all-day
  display, full-today windows and bounded redacted native error explanations.
- Adapt the root's uncommitted scheduling improvements: twelve-second typing settle,
  typed unclear-request refusal, and a narrow independent timing-clause check for
  “remind me tonight to book a table for Saturday.” Do not trust a model-supplied
  weekday in place of request/date checks. Preserve duration/capture/conflict checks.
- Preserve the root commercialization update and carry allowance/top-up/vision scope
  into P05. Original uncommitted code/docs remain in a named git stash snapshot when
  advancing main. Generated package metadata and local tool configuration stay local.

## Verification

- Production baseline: **1,051 passed in 21.02s**.
- Combined final Python suite: **1,190 passed in 22.13s**, command:
  `.venv/bin/python -m pytest tests -o addopts='' -q --tb=short` (root venv,
  run from the integration worktree). Synthetic Notes/EventKit/provider/Keychain;
  no user Apple data, live inference, launchd changes or migration.
- `swift build --package-path mac`: **build complete**, exit 0. Existing concurrency
  warnings in Onboarding.swift/Library.swift remain for Swift 6 mode; current package
  uses Swift tools 5.10. No signing/notarization or clean-install claim.
- `git diff --check`: passed. Merge markers removed.
- Separate native/media/listener checks and bounded independent review performed.
  Review found a source-anchor loss in photo fallback. A new regression failed before
  restoring source checks and passed afterward; stale photo questions cannot mark
  a redirected answer complete.
- Review also found stale source metadata could permit a second vision upload after
  deletion/private renaming/modification. Three new tests failed before live source
  revalidation at every vision attempt and passed afterward.
- Original main changes saved as stash `3a497b1a9f081494b4b9836c3b99f40c2677eedd`,
  named `pre-reconciliation-2026-09-09-original-main-drafts`. Retain for recovery;
  superseded source/doc drafts must not be blindly reapplied.

## Remaining gates and next session

P03 history/clarification is unbuilt. P04 device experiment, P05 hosted accounts/billing,
P06 signed Keychain/portable runtime/onboarding lifecycle and P07 external validation
remain open. Default protected startup still pauses until the signed secure runtime
is provisioned. In particular, merging this code does not upgrade the running listener.
No deployment, push, real model call or native permission change was performed.

Start from the reconciled main checkout and read the P03 plan. Do not repeat P01/P02.
Keep the production worktree and original-work stash until the owner no longer needs
the historical drafts. The stash contains superseded roadmap/code drafts as well as
local metadata/configuration; do not blindly apply it over the integrated source.
