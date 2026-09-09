# Service deletion and retention policy inputs

This is an implemented pre-release retention mechanism, not an approved legal
retention schedule. Billing-enabled configuration requires a positive explicit
`NOTRON_SERVICE_FINANCIAL_RETENTION_DAYS`; no default duration is invented. The
owner must approve and publish its purpose, duration, provider/backup treatment
and cancellation behavior before paid launch. Synthetic tests use declared values.

DELETE account performs one account-locked transaction: status becomes deleted;
contact email, raw identity/session mappings, worker lease, response ciphertext and
account audit metadata are erased; devices are revoked and unused device rows
removed; entitlements revoked; checkout/return URLs cleared; payload digests erased.
Late inference completion cannot restore response content. Local Notes, Reminders,
Calendar, encrypted operation receipts/recovery payloads and explicitly retained
backups are outside this service and are not deleted.

Retained separately and purpose-limited:

* One SHA-256 digest of canonical issuer+subject remains indefinitely to prevent
  accidental resurrection with the same identity. This is pseudonymous, not
  anonymous; there is no account ID, email, raw subject or session in that table.
* Account UUID, deletion timestamps/status, remote cleanup attempts and pending
  state remain until remote cleanup is confirmed and retention is eligible.
  Vendor-specific identity erasure is explicitly pending until provider-confirmed
  operator attestation. Its evidence reference is stored only as a digest.
* Customer/subscription/session/order/source IDs, price/allowance/allocation and
  monetary usage rows retain financial reconciliation/refund/dispute and duplicate-
  spend evidence. Only devices referenced by retained usage remain. Request UUIDs
  remain accounting identities; response content and payload digests do not.
  Configured duration starts at deletion. Missing policy holds records pending;
  nonfinancial records need no invented financial period.
* Financial erasure additionally requires confirmed Stripe cancellation and
  identity-provider cleanup, no reserved/uncertain costs, and no current-UTC-month
  usage. A short operator duration cannot reset the global monthly spending cap.
  Unknown costs stay held until privileged reconciliation establishes their cost.
* Live-account response ciphertext normally expires after 24 hours, and content-
  free audit metadata after 30 days. Deletion purges both immediately. Cleanup
  scheduling and monitoring belong to Task6; mere expiry does not unlink rows.

Stripe cancellation is a durable retryable outbox, separate from immediate access
revocation. Its scheduler expires open Checkout and cancels active subscriptions
without proration, invoice creation or refunds; authoritative reads reconcile
response loss. Late owned webhooks reopen work. Outstanding remote work blocks
financial erasure. No live Stripe/OIDC operation occurred during implementation.

Provider retention, backup deletion/snapshots, identity-erasure evidence collection,
financial duration and cancellation latency must be validated and disclosed before
paid launch. Database unlink cannot promise erasure from backups or provider logs.

An unresolved durable Checkout intent (no confirmed remote session yet) keeps
remote cleanup pending even after known subscriptions are canceled. This covers
a request still completing after local timeout; operator/provider confirmation
is required if it never resolves. It cannot silently unblock financial erasure.
