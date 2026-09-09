# Billing and allowance contract — P05 Task 3

Python 3.11+, FastAPI, PostgreSQL, official `stripe==15.6.1`, Stripe API
`2026-08-26.dahlia`. No AI model runs in billing. Live Stripe mode remains rejected
by service configuration. Nothing in this change provisions an account, creates a
real price, deploys, or authorizes payment-provider calls.

## Configuration and HTTP interface

`NOTRON_SERVICE_BILLING_PRICES` is an owner-provided JSON object mapping Stripe
price IDs to `{ "kind": "subscription", "units": positive_integer }` or
`{ "kind": "topup", "units": positive_integer, "ttl_seconds": positive_integer }`.
`NOTRON_SERVICE_BILLING_RETURN_URLS` is an exact JSON allowlist of HTTPS return URLs.
Both must be set together; absent policy disables billing routes with HTTP 503 and
reports zero available allowance. Partial/invalid configuration stops startup.
Stripe credentials remain protected deployment inputs, never browser parameters.
No price, included allowance, top-up size, or production price is supplied by code.

Units are **dimensionless integers**, not dollars or micro-USD. Downstream provider
cost accounting must supply a reviewed server-side cost-to-unit conversion and a
separate monetary spend-ceiling comparison. Never equate one unit with currency.
P07 costs and owner approval determine the conversion and selling allowance.

- `POST /v1/billing/checkout`: authenticated body `price_id`, `return_url`, and UUID
  `request_id`; returns `{ "url": ... }`. A top-up price selects explicit one-time
  checkout. No automatic overage, customer, account ID, quantity, or amount input.
- `POST /v1/billing/portal`: authenticated `return_url`, returns `{ "url": ... }`.
- `POST /v1/webhooks/stripe`: raw Stripe-signed bytes; commits an identifier-only
  durable inbox row before HTTP 202. Webhook processing is performed by the repair
  worker, not by the browser redirect. Invalid signatures/mode return HTTP 400.
- `GET /v1/me`: account/device/status plus `subscription_status` list and
  `entitlement: { status, access_until, allowance, reason }`. Allowance is available
  spendable units after existing reservations/charges, not the plan's original cap.

Checkout persists its intent before creating a remote session. Session retries use
an account/order-scoped Stripe idempotency key. After 23 hours an unbound intent
fails `checkout_repair_required` rather than risk replay past Stripe's key window.
Repair searches the customer's sessions for the server-set order metadata to
recover a successful remote Checkout whose local response/commit was lost.

## Grants, refunds, and access

Each authoritative invoice grants once (`invoice:<invoice_id>`); each top-up
PaymentIntent grants once (`topup:<payment_intent_id>`), independently of delivery
IDs. Grants map one-to-one to existing `entitlements` rows. Paid subscription
allowance starts at the invoice period start and expires at its end. Trial grants
are capped once per subscription; extending a trial never creates a second cap.
Trial units cease being eligible when the subscription leaves trialing.
Top-ups expire `ttl_seconds` after the authoritative Checkout creation timestamp
(a conservative fixed expiry even when payment completes later). Top-ups never
create subscription access on their own. Expired/revoked grants cannot be restored
by a reservation release, settlement retry, payment retry, or webhook replay.

Partial **or** full refunds revoke the entire corresponding grant conservatively.
No cash refund API is called. A dispute on a reconciled payment suspends the account;
reconciliation never automatically reinstates a suspended/deleting/deleted account.
Only active/trialing or an already-paid unexpired period can admit new paid calls.
Incomplete/unpaid statuses cannot. Cancellation/failed renewal preserve only the
already-paid period; expiry pauses new paid calls. Local completion receipts and
local retry recovery do not depend on billing entitlement.

Zero-value invoices, proration lines, multiple eligible invoice lines, and non-
PaymentIntent invoice payments do not mint new allowance. Unsupported invoice
payment records fail reconciliation closed for operator review. Coupon/credit,
out-of-band payment, arbitrary plan changes and trial-extension commercial policy
need owner qualification before enabling such Stripe features.

Administrative Python-only APIs (no client route):
`Billing.grant_pilot(account_id, approval_id, units, access_until)` creates a unique,
time-limited capped pilot grant; approval ID reuse cannot expand it.
`Billing.suspend(account_id)` immediately revokes new account work and records fixed
audit metadata. Grant/administrative access must use separately controlled service
operator credentials, not desktop credentials. There is no automatic unsuspend.

## Downstream usage and deletion integration

All account/grant/subscription/usage mutations serialize on `accounts FOR UPDATE`.
The order is account, then billing/grants/usage; deletion must use the same order.
The following methods accept a psycopg connection using `dict_row` and share its
transaction. The caller must hold the account lock and reauthorize the active
device/worker fence; none of these signatures accepts an app-supplied account ID.
Time must be trusted server UTC, not client time.

1. Call `billing.entitlement(principal)` before admission to reconcile a stale
   subscription (default freshness 300 seconds; upstream failure fails closed).
2. Open transaction, `billing.lock_account(conn, account_id)` then
   `store._authorize(conn, principal)` and verify the current worker lease/fence.
3. `billing.entitlement_locked(conn, account_id, now)` returns the pure entitlement.
   `billing.balance_locked(conn, account_id, now)` returns available integer units.
   `billing.funding_grants_locked(conn, account_id, now)` returns rows
   `{id: entitlement_uuid, available: integer}` ordered by expiry then UUID.
4. Insert the existing `usage_reservations` row first (unique account/request ID),
   using a positive-available funding grant ID for its required `entitlement_id`.
   Then `billing.reserve_locked(conn, account_id, reservation_id, units, now)`
   allocates across grants atomically. Insufficient balance or `billing_stale`
   must roll back the entire transaction, including the reservation insert.
   A durable retry must look up/validate its existing usage reservation; allocation
   cannot be replayed into another spend (`reservation_already_allocated`).
5. `billing.settle_locked(conn, account_id, reservation_id, actual_units)` consumes
   only that allocation, releasing unused reserved capacity. Equal settlement
   retries are idempotent; differing totals reject `settlement_conflict`.
   `billing.release_locked(conn, account_id, reservation_id)` is settlement at zero.
   These methods do not update `usage_reservations.status/actual_units`; the usage
   service must update those in this same locked transaction.
6. Actual cost above reserved units rejects `reservation_exceeded` and preserves
   the full reservation. The usage layer must mark it uncertain, pause further
   affected work, and reconcile conservatively; never charge unreserved capacity
   or silently truncate actual monetary costs. Reserve a hard upper bound before
   provider work. Settlement/release must not be used to create a second call.

Accounting tombstones and source identities must survive retention/deletion long
 enough to prevent replay. Never delete grants/allocations independently to free
balance. Account erasure policy/retention and downstream usage ledger are Task 5/6.

## Scheduled operation and remaining staging gates

After approved environment provisioning and migration, run once per minute using
the same approved configuration/credentials as the service:

```sh
python -m notron_service.billing_repair --limit 100
```

This replays the durable inbox and independently fetches current subscriptions,
paid invoices/payments and owned top-up sessions, including missing webhook repair.
Multiple workers serialize by account/event and grants remain unique. Failed
accounts/events rotate behind unattempted work instead of starving the queue.
The command prints only counters, exits 1 on incomplete repair, and does not
perform migration, account provisioning, or a deployment. Schedule capacity must
keep all account snapshots within the 300-second freshness limit; monitor failures
and oldest unreconciled account/inbox age. History scans are paginated and complete;
large-account performance needs staging measurement before scaling.

Migration `004_billing.sql` is additive and fingerprinted. Its non-destructive
rollback marks revision 4 inactive, retains financial identities, and fails
readiness. Forward `PostgresStore.migrate()` reactivates after verification; a real
downgrade requires the matched backup/old release procedure.

Still required before release: owner pricing/allowance/conversion and expiry;
approved test Stripe account/webhook API version and events; real Stripe test-clock
trial end, first payment, renewal failure/retry recovery, cancellation at period
end and expiry; duplicate/reordered/replayed webhooks and temporarily unavailable
worker repair; asynchronous top-up success/failure, partial/full refund and dispute;
crash after remote Checkout success before local persistence; account-isolation
review; scheduler freshness/latency and crash/concurrency load; P07 cost qualification.
No real Stripe test clock or payment call has been made by this implementation.

Primary contract references checked during implementation:
[Stripe webhooks](https://docs.stripe.com/webhooks),
[subscription webhooks](https://docs.stripe.com/billing/subscriptions/webhooks),
[test clocks](https://docs.stripe.com/billing/testing/test-clocks),
[official Python SDK](https://github.com/stripe/stripe-python), and
[SDK release](https://pypi.org/project/stripe/15.6.1/).

## Account deletion integration (Task 5)

See [DEVICES.md](DEVICES.md) and [RETENTION.md](RETENTION.md). Billing configuration
now additionally requires explicit `NOTRON_SERVICE_FINANCIAL_RETENTION_DAYS`.
The existing repair scheduler processes durable deletion cancellation, adding
`deletion_completed` and including cancellation failures in `failed`. Deleted
accounts never regain access; late webhooks reopen cancellation instead of
reconciling entitlements. No new invoice, proration or refund is requested by this
cleanup path. Current-month and uncertain monetary rows cannot be erased to reset
the spending cap, even when an operator chooses a short retention period.
