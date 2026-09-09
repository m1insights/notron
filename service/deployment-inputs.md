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
