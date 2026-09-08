# P05 — Accounts and managed AI service implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** A nontechnical subscriber signs in and receives managed AI access without handling provider keys.
**Architecture:** Managed OIDC login, server-side account/entitlement checks, durable billing reconciliation, usage reservation, revocable devices, and a restricted gateway to Nebius/Tavily. Keep the desktop graph local; change its transport in managed mode.
**Tech Stack:** Python 3.11+, FastAPI/Pydantic 2, PostgreSQL, managed OIDC, Stripe test mode then approved production configuration, Swift browser login/Keychain. Retain Nebius inference.
**Spec:** [Shared design §§2, 3, 5–6](../design.md).
**Dependencies:** P01 privacy/storage, P02 request/health contracts. Reuse P04 service modules if they exist. If P04 stops at device feasibility, create the service foundation through Task 1 using the same paths/contracts; do not require successful mobile filing to build Mac subscriptions.

## Global constraints

- Never ship a company provider key, client secret, signing secret or webhook secret in the app/Shortcut.
- Server authenticates identity and enforces entitlement/quota on every paid call. The MIT client is not trusted to report payment status.
- Provider destinations/model IDs are server-controlled. User text never configures transports or tools.
- Prototype tokens are not accepted on production inference/account/admin routes.
- No note bodies in logs; retention and payload encryption follow P01/shared design.
- No account purchase, live charge, public deployment, or tester invitation is implicit in implementing tests/code.

## Task 1 — Service foundation and deployment input validation

**Files:** Create or extend P04's `service/pyproject.toml`, `service/notron_service/app.py`, `config.py`, `store.py`, `providers.py`, `principals.py`, `service/Dockerfile`; Create `service/tests/test_config.py`, `service/deployment-inputs.md`, `service/migrations/002_accounts.sql`.
**Consumes:** Common `Principal(account_id, device_id, scopes, kind)`, P01 prepared input schema.
**Produces:** `Settings.from_env()` rejecting missing/invalid production configuration; `create_app(settings, services)` with injected dependencies for tests; PostgreSQL migrations and readiness endpoint without secret/config dumps.

- [ ] Record deployment inputs by role, not fake values: owner provides approved host/region/spend ceiling, OIDC issuer/audience, callback/domain control and Stripe test account. Validate each at deployment; local tests use a loopback fake issuer and generated test keys. This task does not select/purchase an account on the owner's behalf.
- [ ] Implement a production configuration check that rejects HTTP issuers/provider URLs, debug mode, missing encryption/signing keys and prototype-auth enablement on commercial routes. Limit request body sizes at proxy and app; disable body logging in validation middleware.
- [ ] Create account, external-identity, device, entitlement, webhook-event, usage-reservation, worker-lease and audit-metadata tables. Minimum identity constraint:

```sql
CREATE TABLE accounts (
    id uuid PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('active', 'suspended', 'deleting', 'deleted')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE identities (
    issuer text NOT NULL,
    subject text NOT NULL,
    account_id uuid NOT NULL REFERENCES accounts(id),
    PRIMARY KEY (issuer, subject)
);
```

- [ ] Keep email as contact information, not identity/ownership key. All user-owned queries require authenticated account ID and composite ownership constraints. No account ID from a request body overrides Principal.
- [ ] Lock dependencies, run `uv run --project service pytest service/tests/test_config.py -q`, apply migrations to disposable PostgreSQL, test rollback/forward restoration, commit.

## Task 2 — Native sign-in and account verification

**Files:** Create `service/notron_service/auth.py`, `service/tests/test_auth.py`, `mac/Sources/Notron/AccountSession.swift`, `mac/Tests/NotronCoreTests/AccountSessionTests.swift`; Modify `mac/Package.swift` to extract testable non-UI core target as needed, and Keychain bridge from P01.
**Consumes:** Managed OIDC discovery/JWKS, Settings issuer/audience, P01 credential store.
**Produces:** `authenticate(bearer: str) -> Principal`; short-lived access session, revocable device/session record and refresh flow through the identity provider; browser Authorization Code + PKCE (S256), state and nonce checks.

- [ ] Build tests with locally generated signed JWTs for correct issuer/audience, expired token, wrong audience, missing subject, unverified email for email-based signup, algorithm substitution, unknown key rotation and revoked device. Reject ID tokens presented where an API access token is required.

```python
def test_wrong_audience_never_reaches_inference(client, jwt_factory, provider, infer_payload):
    token = jwt_factory(aud='another-service')
    response = client.post('/v1/infer', json=infer_payload,
                           headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401
    assert provider.calls == []
```

- [ ] Use a maintained JWT/OIDC library, pinned in the service lock; enforce issuer, audience, allowed signing algorithms, expiry/not-before with bounded skew and key rotation. Do not implement custom cryptography or accept an arbitrary discovery URL from clients.
- [ ] Open system-browser auth from Swift with ASWebAuthenticationSession; use PKCE/state/nonce, validate callback, support user cancellation and replay rejection. Store refresh material in Keychain only; do not put it in preferences, URL query logs or process arguments.
- [ ] Create accounts from verified issuer+subject. Permit email login initially through the managed issuer; enable Sign in with Apple only when the selected issuer/team configuration is validated. This is a second configured connection, not a custom Apple password flow.
- [ ] Add visible sign-out, device revocation and account-deletion entry points. Test sign-out clears credentials and stops managed processing without deleting Notes. API access checks revocation before a billable call even if JWT expiry has not arrived.
- [ ] Run server auth tests plus Swift account-session unit tests. Record live issuer/browser login as a staging gate requiring real provisioning, not a mocked-test success. Commit.

## Task 3 — Subscription entitlements and lifecycle

**Files:** Create `service/notron_service/billing.py`, `entitlements.py`, `service/migrations/003_billing.sql`, `service/tests/test_billing.py`, `service/tests/test_entitlements.py`; Modify app/config.
**Consumes:** Authenticated account, Stripe customer/subscription IDs and signed webhook events.
**Produces:** Authenticated `POST /v1/billing/checkout`, `POST /v1/billing/portal`, `POST /v1/webhooks/stripe`, `GET /v1/me`; `Entitlement(status, access_until, allowance, reason)`.

- [ ] Write tests for forged signature, duplicate delivery, unordered old/new events, initial incomplete checkout, cancellation at period end, expiry, failed renewal and subsequent recovery. A browser checkout-success redirect never grants service access.

```python
def test_duplicate_invoice_event_is_applied_once(billing_harness):
    event = billing_harness.signed_invoice_paid('evt_one', account='a1')
    billing_harness.deliver(event)
    billing_harness.deliver(event)
    assert billing_harness.processed_count('evt_one') == 1
    assert billing_harness.allowance_grants('a1') == 1
```

- [ ] Verify Stripe signature against the raw request body; store event ID uniquely before background processing. Retrieve authoritative subscription state for reconciliation instead of trusting delivery order. Reconcile on scheduled runs and when status is stale, with authenticated account binding.
- [ ] Bind checkout/portal to the authenticated account's stored Stripe customer; validate a server allowlist of price IDs/return URLs. Never accept an arbitrary customer ID or price amount from the app.
- [ ] Pilot grants are explicit admin-created, time-limited entitlements with a usage cap. Production allows active/trialing subscriptions until `access_until`; initial incomplete/unpaid has no service. Cancellation at period end preserves access until that date. A renewal failure pauses new paid calls after the already-paid period ends; retries/receipts for completed local actions still work. Fraud/revocation suspends immediately.
- [ ] Configure $12 only in test mode as the current hypothesis if needed for checkout tests; production price/allowance are owner-set release inputs backed by P07 costs. No unlimited plan.
- [ ] Use Stripe test clocks for trial end, renewal, cancellation and recovery; test webhook replay and reconciliation repair. Run billing/entitlement suite; commit.

## Task 4 — Metered inference and desktop transport

**Files:** Create `service/notron_service/inference.py`, `service/notron_service/search.py`, `service/notron_service/usage.py` if absent, `service/tests/test_inference.py`, `service/tests/test_usage_concurrency.py`, `notron/transport.py`, `tests/test_managed_transport.py`; Modify `notron/brain.py`, `notron/research.py`, `service/providers.py`, app.
**Consumes:** Prepared P01 inputs, authenticated Principal, entitlement and request/operation IDs.
**Produces:** `/v1/infer`, `/v1/embed`, `/v1/search`; `DirectTransport` and `ManagedTransport` behind the same Brain-facing methods; `reserve(account_id, request_id, ceiling)`, `settle(reservation_id, actual_usage)`.

- [ ] Test expired access, quota exhaustion, concurrent requests at the quota boundary, unknown model IDs, arbitrary provider URL injection, cross-account request-cache retrieval and prototype-token rejection.

```python
def test_exhausted_allowance_blocks_before_provider(client, subscriber, provider, infer_payload):
    subscriber.remaining = 0
    response = client.post('/v1/infer', json=infer_payload, headers=subscriber.headers)
    assert response.status_code == 429
    assert response.json()['code'] == 'allowance_exhausted'
    assert provider.calls == []
```

- [ ] Reserve a conservative upper bound transactionally before provider execution. Rate table is versioned and covers model input, reasoning/output, embedding and search costs; deployment requires configured rates rather than hardcoded guesses. Reject/queue when reserved budget would exceed allowance. Settle actual usage; uncertain provider cost retains reservation until reconciliation, never refunds optimistically into unlimited retries.
- [ ] Server chooses fixed tiers/models, caps input/output, concurrency and deadlines; validates schemas again and applies P01 supported secret redaction as defense in depth. API is not a generic arbitrary-URL proxy. Avoid retaining content except encrypted retry cache with expiry.
- [ ] ManagedTransport receives short-lived access tokens from the app/helper using protected IPC; a bounded refresh attempt handles expired access. It never loads a company key. BYO retains DirectTransport using user Keychain credentials. Both use identical input preparation and response validation.
- [ ] Define visible codes `signin_required`, `subscription_required`, `allowance_exhausted`, `provider_unavailable`, `permission_required`, `outcome_uncertain`; preserve local pending request rather than treating errors as empty answers. Receipt repair is local and not paywalled.
- [ ] Verify no duplicate provider call on same completed request ID; changed payload gets 409. Run service inference/usage tests and desktop graph/transport tests. Commit.

## Task 5 — One managed Mac, device transfer and account cleanup

**Files:** Create `service/notron_service/devices.py`, `service/notron_service/deletion.py`, `service/tests/test_devices.py`, `service/tests/test_deletion.py`; Modify `notron/watch.py`, `notron/health.py`, Swift AccountSession.
**Consumes:** Account/device credentials, P02 health and operation lifecycle.
**Produces:** Worker lease endpoints (acquire, renew, release, transfer), `DELETE /v1/account`, scoped device listing/revocation.

- [ ] Add concurrent acquisition and old-worker-after-transfer tests. One lease per account; worker includes a monotonically increasing fencing token in managed calls. A stale token cannot acquire new work after transfer.

```python
def test_transfer_revokes_previous_worker(lease_store):
    old = lease_store.acquire('a1', 'mac1')
    new = lease_store.transfer('a1', 'mac2')
    assert not lease_store.valid('a1', 'mac1', old.fence)
    assert lease_store.valid('a1', 'mac2', new.fence)
```

- [ ] Lease lasts 60 seconds, renew every 20 seconds; validate before starting each managed operation. Network/lease loss stops new effects. An in-flight Apple write can still finish: transfer warns about/reconciles in-flight operations and waits for prior lease expiry rather than claiming instantaneous global cancellation.
- [ ] On revocation/sign-out, remove credentials and stop worker scheduling. Preserve local user notes, receipt recovery and explicitly retained backups. BYO mode does not pretend to enforce cloud account leases; show the supported one-active-Mac limitation.
- [ ] Account deletion immediately revokes service access and purges response content; remove personal service records under the published retention policy. Keep only separately justified billing/security records, disclosed before paid launch. Do not delete Apple Notes or Apple's reminders/calendar records.
- [ ] Run concurrency/revocation/deletion tests against PostgreSQL, including a second account attempting the same IDs. Commit.

## Task 6 — Operational readiness and managed onboarding handoff

**Files:** Create `service/OPERATIONS.md`, `service/tests/test_service_security.py`, `docs/production/evidence/P05-staging.md`; Modify `.github/workflows/service.yml` (Create if absent), P06 handoff and roadmap.
**Consumes:** Tasks 1–5 and approved deployment inputs.
**Produces:** Deployable service, documented backup/restore, key rotation, alerting, privacy retention and service rollback procedures.

- [ ] Set TLS-only ingress, body-size limits, per-account and IP abuse controls, no public debug endpoints, private database access, migration-before-worker startup and least-privilege service credentials. Restrict worker egress to configured providers/identity/payment endpoints where hosting supports enforcement.
- [ ] Test duplicate webhook delivery, database outage, provider timeout, key rotation, issuer outage, encrypted-cache purge, restore into staging and expired device token. Logs/monitoring may include opaque IDs and durations but no bodies/secrets. Record actual provider retention settings before real-user requests.
- [ ] Run `uv run --project service pytest service/tests -q`, dependency/secret scans, and a Swift-managed sign-in/inference round trip on a test account. Review account ownership in every endpoint.
- [ ] Deliver P06 the exact login flow, callback registration, error codes, quota/status display contract and cancellation behavior. Public onboarding must not show prototype credentials or endpoint fields.
- [ ] Write staging evidence and outstanding external-review findings. Update the roadmap only for demonstrated behaviors; commit and hand off to P06/P07.

**Exit gate:** Login, payment and usage controls work server-side under replay/concurrency/failure; a copied or patched client cannot gain another account's access or unlimited provider spend. No live billing until P07 release approval.

**Primary references:** [Native-app OAuth](https://www.rfc-editor.org/info/rfc8252/), [Stripe webhook verification/order/duplicates](https://docs.stripe.com/webhooks), [subscription events](https://docs.stripe.com/billing/subscriptions/webhooks), [FastAPI deployment](https://fastapi.tiangolo.com/deployment/concepts/). Recheck chosen SDK versions against official documentation during implementation.
