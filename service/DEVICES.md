# Managed Mac leases and account cleanup

Python/FastAPI/PostgreSQL service, Python desktop worker, Swift account controls.
No AI provider/model changes. No live provider, Apple or cloud calls were made.

## Authenticated lease API

All endpoints use the existing verified access token. Account/device IDs come
only from its principal; unknown body fields reject. `account:read` is required.
`POST /v1/worker/lease/acquire` and `/transfer` accept `{}`. Transfer destination
is always the authenticated Mac. `/renew`, `/check` and `/release` accept
`{"fence": positive_integer}`. All use the account lock then recheck account/device
revocation. A foreign device cannot release/check/renew another account's grant.

Grant responses contain `fence`, `lease_seconds:60`, `renew_seconds:20`,
`wait_seconds` (0–60), and `valid_seconds` (remaining server validity). Release
returns `{"status":"released"}`. A released grant keeps its fence and expiry;
subsequent grants monotonically increase the fence. Acquire never steals a live
grant. Transfer immediately replaces the old fence, but new admission waits until
the old lease expiry. Repeated pending transfers retain the original drain barrier.
A response-loss retry by the same current destination returns the same grant.

Usage authorization, both reservation and final provider admission, checks active
owned device, exact fence, not-before, expiry and release state in the account-
locked transaction. Default service app mounts every endpoint. GET `/v1/devices`
and POST `/v1/devices/{id}/revoke` remain account-scoped; revocation rechecks auth
after obtaining the account lock. Revocation does not delete Apple records.

## Desktop integration and limits

The existing protected native FD bootstrap constructs ManagedLease, acquires the
grant, installs `configure_lease_fence`, and runs a daemon renewal every 20 seconds.
Expiry is measured conservatively from HTTP send using a monotonic clock; a pending
transfer's wait starts at response receipt. Any renewal/check/network loss stops
new managed effects. There is no silent reacquisition in a stopped worker; restart
or explicit transfer creates a new worker session. Normal stop attempts release.
Swift sign-out synchronously cuts token supply and closes/stops native children;
refresh credentials remain solely in Keychain. A revoked token's failed refresh
clears native credentials. Transfer keeps the account signed in but replaces the
local child bridge; setup and explicit resume still apply.

Watcher checks lease before scheduling. Executor checks the service immediately
before each NEW Notes or EventKit mutation, including after provider waits. A
failed check records a known-not-issued operation for review, with the existing
managed health code. This is not a distributed Apple transaction: a transfer can
race the last check, and Apple writes already issued can finish even beyond the
60-second drain. Existing receipt/reconciliation rules still apply. Transfer UI
warns about that limitation and asks for unfinished-operation review.

Verified local undo/recovery copies still require exact encrypted snapshot proof
and all existing policy/source/revision validation. Fixed audit receipts require
an APPLIED/RECEIPTED primary operation and exact label/destination; action receipts
require the writer checkpoint, exact persisted APPLIED action, and a regenerated
code-only confirmation. No model flag grants an exemption. Watcher can repair
these verified action receipts before a cloud probe. Read-only reconciliation and
already-applied outcomes retain their existing paths. Legacy audit records without
a recoverable primary link are retained but cannot claim this offline exemption.

BYO mode retains the one-active-Mac limitation and local process lock; it does not
claim cloud lease protection. P06 native identity/protected startup gates remain
closed. No production listener or live Notes test was started.

## Deletion and operations

DELETE `/v1/account` immediately revokes service access and purges response content,
then returns local `status:deleted`, `remote_cleanup_pending:true`, financial
retention configuration/pending state, and `identity_tombstone:indefinite`.
POST `/v1/me/deletion` remains a 202 compatibility alias with the same erasure.
See [RETENTION.md](RETENTION.md) for the precise retained records and operator gates.

`Deletion(store, financial_retention_days=None).purge_expired(limit=100)` completes
legacy `deleting` rows, purges expired response/audit data, and erases eligible
financial records. `None` explicitly means retention policy pending, not a guessed
retention duration. Pass deployment settings when completing legacy deletion work.
Limits are 1–1000. It returns content-free counters `accounts`, `cache`, `security`,
`pending_completed`. Task6 owns scheduled cleanup commands, alerts and CI.

`Deletion.repair_remote(gateway,limit=100)` processes the durable Stripe cancellation
outbox and returns `completed`/`failed`. The existing `Billing.repair()` scheduler
already calls it and adds `deletion_completed` to its counters; failures contribute
to `failed`. Owned late Stripe webhooks reopen cleanup. It lists all customer
Checkout sessions (including remote success before local persistence), expires open
sessions, then cancels customer-owned subscriptions with `invoice_now=false` and
`prorate=false`. No refunds or new invoices are created. Ownership mismatch,
async subscription creation or uncertain HTTP outcomes leave work pending. Retries
read authoritative state first; stable account/object idempotency keys are used.
An already-authorized payment may settle; this is not an automatic refund flow.

`Deletion.confirm_identity_erasure(account_id,evidence_reference)` is an operator-
only attestation seam after provider-confirmed erasure; no public HTTP route and no
invented OIDC vendor API. It retains only a digest of the private evidence reference.
Provider-specific identity erasure integration and a verified operator procedure
remain release gates. It must never be used merely to unblock retention cleanup.

Migration 006 is additive; earlier SQL is unchanged. Non-destructive rollback
retains fences, outbox and accounting data while failing readiness. Forward migration
verifies fingerprints and restores readiness; use the matched backup/release for
actual downgrade. Production policy, Stripe test clocks/cancellation races, backup
restoration, operator erasure evidence, and signed Mac lifecycle require staging.

Stripe semantics checked against the official [cancellation API](https://docs.stripe.com/api/subscriptions/cancel)
and [Checkout expiry API](https://docs.stripe.com/api/checkout/sessions/expire);
SDK 15.6.1 actual HTTP serialization is covered by synthetic HTTP tests.

An unresolved durable Checkout intent (no confirmed remote session yet) keeps
remote cleanup pending even after known subscriptions are canceled. This covers
a request still completing after local timeout; operator/provider confirmation
is required if it never resolves. It cannot silently unblock financial erasure.
