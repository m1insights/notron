# P05 deployment inputs

Status: foundation implemented locally; no host, region, issuer, domain or Stripe
account has been selected or provisioned by this work. These are owner-supplied
inputs, not sample production values. P04's iPhone experiment is deferred and does
not block this service foundation.

| Input / setting | Owner or provider role | Deployment validation |
|---|---|---|
| Approved hosting account and region (`REGION`) | Owner selects host and data region | Check account ownership, private database networking and TLS ingress |
| `MONTHLY_SPEND_CEILING` | Owner sets infrastructure/provider budget | Positive finite amount; operational spend enforcement and usage rates are Task4/6 |
| `PUBLIC_URL` and `CALLBACK_URL` | Owner controls domain and callback registration | HTTPS, same approved origin; certificate and callback registration require staging evidence |
| `OIDC_ISSUER`, `OIDC_AUDIENCE` | Managed identity administrator | HTTPS fixed issuer; validate discovery/JWKS and native client registration in Task2 |
| `DATABASE_URL` | Database operator | PostgreSQL URI, certificate verification (`sslmode=verify-full`), separate migration/runtime credentials |
| `ENCRYPTION_KEY` | Secret manager / operator | Base64-encoded 32-byte random key; dedicated to retry content encryption in Task4 |
| `SIGNING_KEY` | Secret manager / operator | Independent base64-encoded 32-byte key; not an OIDC signing key or client secret |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Owner's Stripe test account | Test-mode credentials only; actual webhook/test-clock integration is Task3 |
| Provider credentials and rate tables | Owner's Nebius/Tavily accounts | Tasks4/6 inputs; never embedded in the app or public configuration |

All environment names above use the `NOTRON_SERVICE_` prefix. `ENVIRONMENT`
defaults to `production`; tests must explicitly select `test`. Test/development
permits a loopback HTTP fake issuer. Remote HTTP issuers, debug/prototype auth,
arbitrary provider destinations, malformed/reused keys and live Stripe credentials
are rejected. The current company service mounts no account, billing or inference
routes: those require subsequent authentication, entitlement and quota work.

`MAX_BODY_BYTES` defaults to 1 MiB and cannot exceed it. The application counts
actual bytes, including requests without Content-Length. `deploy/nginx.conf` is
an ingress template, **not deployed configuration**: the operator must replace its
explicit domain/certificate variables before validation. It caps bodies to 1 MiB,
requires TLS and logs only status/duration. Request/response buffering is disabled
to avoid plaintext temporary body files. Application launch disables access logs;
validation/unexpected errors do not echo request bodies or propagate content-bearing
exceptions to the server logger. The template's lower custom application limit, if
set, remains enforced by the app.

## Local validation

Dependencies are pinned in `pyproject.toml` and fully locked in `uv.lock`.

```sh
uv sync --project service --frozen
uv run --project service pytest service/tests/test_config.py service/tests/test_providers.py -q
```

Database tests require **a disposable PostgreSQL instance only**. Set
`NOTRON_TEST_DATABASE_URL` to its DSN and `NOTRON_TEST_PG_BIN` to matching
`pg_dump`/`pg_restore` binaries, then run:

```sh
uv run --project service pytest service/tests/test_store.py -q
```

Those tests create unique disposable schemas, exercise account boundaries and
migration failure/replay, then perform a custom-format backup/restore of synthetic
data. Never point the test DSN at a personal or production database.

`PostgresStore.migrate()` is an explicit deployment action. The application checks
schema readiness and never migrates on startup. Use DDL-capable migration credentials
for that action; use a separate role restricted to required DML and schema reads for
runtime. A missing/drifted/inactive schema yields 503 at `/health/ready`.
`/health/live` reports process liveness only.

The rollback SQL marks the foundation inactive while retaining tables and data;
forward migration verifies checksums/catalog structure before reactivation. For an
actual database downgrade, restore a matched backup into a separate database and
validate it before routing traffic. Do not drop production tables as a rollback.

## Outstanding deployment gates

No Docker runtime was available here, so image build/start and the TLS proxy template
require validation before deployment. No cloud resource, issuer, Stripe test account
or live provider credential was used. Public deployment, charges and real-user data
remain unauthorized; later tasks must record their own staging evidence. In particular,
a configured spend ceiling is not yet metering or a paid-service guarantee.

References checked during implementation: [FastAPI deployment concepts](https://fastapi.tiangolo.com/deployment/concepts/),
[PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html),
[Psycopg transaction management](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).

Proxy buffering reference: [nginx proxy buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_request_buffering).

## Task 2 identity profile and release gate

Set `NOTRON_SERVICE_OIDC_CLIENT_ID` to the native public client's configured ID,
different from `OIDC_AUDIENCE`. Omitting it keeps managed account APIs closed (503).
No passwords are stored or accepted by this service. Email login is hosted by the
selected managed issuer. Apple is **not enabled** until its separate managed
connection and team configuration have been validated.

The selected issuer must issue RS256 RFC 9068 `typ: at+jwt` access tokens with
exact API audience, `iss`, `sub`, `iat`, `nbf`, `exp`, `sid`, verified `email`, and
`email_verified: true`; lifetime <=900 seconds, 30-second validation skew. Scope
`account:read` is required for managed identity. The signed `sid` must be stable
across refresh and distinct for a new browser login. Tokens with a generic JWT
header (including ID tokens) cannot call account/paid routes. The native client
ID-token audience is distinct, and ID-token nonce and subject are verified by the
service before the native client accepts login. The deployment is deliberately
fail-closed for issuers lacking this exact profile; configure validated claims
at the issuer, do not weaken verification to accommodate a mocked test.

Discovery uses only configured issuer HTTPS URLs. Issuer, JWKS, authorization,
token and revocation endpoints must share the reviewed issuer origin; redirects
are refused. Native refresh tokens must rotate. Provider token/revocation bodies
are POSTed, never put in URL queries or logs. Refresh material stays in the native
Keychain (`managed-refresh`), and the P01 Python credential bridge explicitly
refuses that name.

The signed app bundle must supply `NotronOIDCIssuer`, `NotronOIDCClientID`,
`NotronOIDCAudience`, `NotronServiceURL`, and set
`NotronManagedIdentityValidated=true` **only after** live staging evidence.
The checked-in value is false. Register native callback
`com.m1labs.notron:/callback`; use a public native client without a client secret.
Never enable this gate with arbitrary client-entered issuer URLs.

Staging gates: genuine email verification, cancel and retry in the system
browser, callback delivery to signed app, JWKS rotation, short access expiry,
refresh rotation/stable sid, offline sign-out and remote revocation recovery,
Keychain accessibility and signed-helper boundaries, and separate Apple
connection approval. No live provisioning/provider calls were performed locally.

### Contracts for subsequent tasks

- `Depends(require_principal)` authenticates JWT plus current account/device
  state on every request. A `Principal` is server-generated; do not reconstruct
  it from body IDs. Before billable provider work, recheck store authorization in
  the same transaction as usage reservation/worker validation. Never cache a
  positive revocation decision across requests.
- GET `/v1/me` returns account_id, device_id, status; GET `/v1/devices` lists only
  this account's devices. POST `/v1/devices/{uuid}/revoke` returns 204 and refuses
  devices outside the account. Revocation tombstones survive refresh.
- POST `/v1/me/deletion` returns 202 `deletion_requested`, marks account deleting
  and revokes every device immediately. This is the durable **entry point**, not
  completed erasure: later deletion workflow must reconcile billing, providers,
  retention and permitted account metadata. No local Notes are deleted.
- Native `AccountSession.accessToken(forceRefresh:)` supplies a short-lived access
  token. Never export refresh tokens to Python. Task 4 may refresh once after 401;
  paid operations retain their request ID and must not blindly retry.
- Native stop notification `com.m1labs.notron.managedSessionStopped` is posted
  synchronously on sign-out/auth failure; Task 4 cancels pending managed work.
  P06 signed startup remains separately disabled. Access is cleared even if
  Keychain deletion fails, and the UI offers a retry instead of claiming cleanup.
- Schema version 3 is additive. Migration validates version-2 checksum/fingerprint
  before upgrade, then records the new entire schema fingerprint. Each newer
  migration must extend `_migrations()` and its table set; rollback is the paired
  non-destructive `003_identity_sessions.rollback.sql`, not the old v2 script.

### Persistent local sign-out state

The native session now requires `FileSessionBarrier.application`, private metadata
at `~/Library/Application Support/com.m1labs.notron/account/session-state`.
Directory mode is 0700 and file mode 0600; contents are only `active`, `signedOut`
or `cleanupPending`, never credentials or identity data. Atomic replacement and
file/directory synchronization persist `cleanupPending` before Keychain cleanup.
A recreated session refuses refresh while stopped or cleanup is pending; missing,
unreadable, malformed or insecure metadata also fails closed. Cleanup retry stays
visible after restart. Successful cleanup retains `signedOut`; only explicit,
nonce-verified browser login can replace the refresh credential and write `active`.
Legacy Keychain credentials without this metadata therefore require fresh login.
