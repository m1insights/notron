# Managed service operations

Status: locally implemented and tested; external qualification pending. This is a deployment runbook, not
staging evidence or authorization to provision resources. Keep production traffic
and live billing disabled until P05 external gates and P06/P07 release gates pass.

## Deployment inputs and isolation

Use [deployment-inputs.md](deployment-inputs.md) for the setting contract. Host,
region, spend ceiling, issuer, domain, Stripe account and provider retention must
be approved and recorded before staging. Do not use personal Notes in verification.

Run the service with a non-root user and private PostgreSQL networking. Give the
runtime role only required table DML and schema-read rights. A separate migration
role owns DDL. Do not expose PostgreSQL publicly. Terminate TLS at the reviewed
proxy; use verified TLS to the database. Restrict outbound destinations to the
configured issuer, Stripe, Nebius and Tavily. No wildcard or client-selected proxy.

Build from the repository root with `docker build -f service/Dockerfile .` so
the service packages the same privacy rules used by the desktop. Do not build
from a service-only context that omits those shared rules.

The ingress template requires concrete domain and certificate paths plus an actual
configuration test. Request/response disk buffering stays disabled. Access logs
contain status and duration only; no query strings, request/response bodies,
authorization headers, payment signatures or credentials. Application exceptions
must remain fixed messages; do not enable debug tracebacks to diagnose user data.

## Test and release evidence

`service/scripts/test-postgres.sh` runs all service tests in a new private local
PostgreSQL cluster and removes synthetic data afterward. It does not read the
production database setting. See [scripts/README.md](scripts/README.md).

Before release, run service tests, desktop synthetic regressions, Swift session
tests and app build, dependency audit, installed-wheel migration check, container
startup and TLS ingress checks. CI jobs reproduce the local service/native tests;
a checked-in workflow is not evidence of a successful remote run.

Record a signed Mac browser-login → account status → managed inference round trip
with the approved issuer. Test cancellation, expired access, key rotation, revoked
device and sign-out while offline. Never substitute mocked tests for that evidence.

## Database backup and restoration

Take an encrypted database backup before migration and verify its restore into a
separate private disposable database. Keep secret material in the secret manager,
not in a shell command or backup filename. Restore the matching application version
and encryption-key generation; check readiness and account ownership tests before
routing traffic. Never restore over a running production database as a test.

Migration is explicit; workers do not perform DDL on startup. Apply migrations with
the migration role before admitting workers. Checksums and schema fingerprints must
match. A rollback marker disables readiness while retaining data; it does not undo
schema changes. For a real downgrade, restore a compatible backup into a separate
database, validate it, then deliberately switch traffic. Stop workers before any
schema rollback or routing change. Preserve uncertain operation identities.

## Privacy and retention

Operational metadata has a 30-day limit; successful encrypted response retry
content has a 24-hour limit. Expiring retry content must not authorize a duplicate
paid call for its old request ID. Account deletion revokes access immediately and
purges retry content; separately retained billing/security records need a written
purpose and retention decision before paid launch. Notes, Reminders and Calendar
records remain local and are never removed by service account deletion.

Provider-side retention is separately controlled by Nebius/Tavily. Record the
actual account settings and disclosures before accepting real-user content. Do not
imply that our local retention policy controls provider retention.

## External checks still required

- Approved infrastructure, domain/TLS, issuer/client/callback profile and secrets.
- Stripe test-account lifecycle and real test-clock runs; production price and
  included allowance/top-up terms supported by cost evidence.
- Signed native login, Keychain and protected helper identity validation.
- Container/TLS staging, egress restrictions, restore rehearsal, alert delivery,
  independent security review and explicit paid-release approval.

No real payment, cloud deployment or live user-data validation has been performed.

## Executable operations and authority

Python/FastAPI/PostgreSQL service; no AI runs in operations. Migration 007 adds
shared account rate windows, operator evidence digests and cache key generations.
No automatic migration occurs in the web factory. With the approved secret-manager
configuration and a separate DDL role, run:

```sh
python -m notron_service.operations migrate
```

Apply `deploy/permissions.sql` using the migration owner, supplying the provisioned
`runtime_role` and `operations_role` psql identifiers. Roles must be non-superuser,
no CREATEDB/CREATEROLE/BYPASSRLS, with no schema CREATE or object ownership. Runtime
cannot modify operator evidence, key generation or schema history. Database host
firewall/TLS configuration and actual grants require staging verification.

Launch one scheduler job at least every minute, using the operations role and
approved environment (the CLI does not read a private production dotenv):

```sh
python -m notron_service.operations periodic --limit 100
python -m notron_service.operations status
```

`periodic` processes durable Stripe inbox, stale billing snapshots and deletion
cancellation outbox; it purges expired encrypted retry content, 30-day audit
metadata, expired 60-second abuse windows and retention-eligible account financial records. Batches are bounded.
Missing billing configuration does not invent a gateway or claim remote completion.
Multiple jobs remain idempotent; scheduler timeout must accommodate provider
pagination and measured backlog. Alert on missed scheduler execution for 2 minutes,
nonzero CLI exit, any failed repair, oldest pending event >300 seconds, stale
billing snapshots, held usage and pending remote cleanup. Counters contain no
account IDs, request bodies, email or evidence references. Alert delivery and
capacity measurements are still external gates; no monitoring account is configured.

`status` is CLI-only, not a public debug endpoint. Exit 1 indicates held usage,
pending billing/identity cleanup, pending inbox events or stale billing accounts.
A zero status result is not a production-readiness assertion. The legacy
`billing_repair` CLI still works but does not replace the complete periodic job.

For provider-confirmed actual cost, obtain a private evidence reference from the
reviewed billing source; preserve the record in the restricted operator system.
The command reads the reference from a private file and stores only its SHA256:

```sh
python -m notron_service.operations reconcile-cost --account ACCOUNT_UUID --reservation RESERVATION_UUID --actual-micro-usd INTEGER --evidence-file PRIVATE_REFERENCE_FILE
python -m notron_service.operations confirm-identity-erasure --account ACCOUNT_UUID --evidence-file PRIVATE_REFERENCE_FILE
```

These commands are never client routes. Reconciliation requires explicit actual
billing evidence, including explicit proof for zero cost; missing evidence stays
held. Identical evidence/amount retries are idempotent; conflicting evidence fails.
Within the reservation, the original cost-to-unit conversion settles its allocation.
An evidenced overrun records the full provider cost in the global monetary meter,
but keeps the reservation uncertain and its allocation held for separate review;
it never silently caps cost, charges unreserved allowance, retries work or refunds.
Late workers cannot overwrite operator evidence. Deleted accounts remain deleted,
devices stay revoked and response caches remain empty. Current-month meter rows
still block financial erasure. Evidence is removed with eventual eligible account
financial erasure; permanent identity tombstones remain pseudonymous security data.
Identity confirmation is an attestation after vendor-confirmed erasure, never a
way to bypass a missing issuer integration or retention hold.

## Abuse and key controls

The default factory enforces **120 authenticated requests/account per 60 seconds**
across workers, using a PostgreSQL account lock/window. Account/billing/inference,
login verification and lease routes share this limit. Limiting happens before
provider admission or billing side effects; HTTP 429 returns `rate_limited` with
`Retry-After: 60`. Desktop keeps that distinct reason and request ID, pauses pending
work and does not automatically buy capacity or retry a paid call. User-facing
wording for P06: “Too many requests. Wait a minute and try again.”

`deploy/nginx.conf` additionally enforces 5 requests/second/peer IP (burst 20),
and 10 login verifications/minute/peer IP (burst 5). It ignores and strips arbitrary
Forwarded/X-Forwarded-For/X-Real-IP values; direct socket peer is the identity.
Behind another load balancer this conservatively aggregates its peer IP until an
explicit trusted-proxy topology is reviewed. Never expose the app port around the
proxy. TLS-only ingress, 1 MiB bodies, no debug endpoints, no proxy header trust
and metadata-only access logs remain mandatory. The local TLS harness uses a
synthetic self-signed certificate and loopback traffic; actual hostname/TLS/cloud
routing, distributed edge capacity and egress allowlisting remain deployment gates.

To rotate response-cache encryption, stop new traffic, load a newly generated
32-byte encryption key into the operator secret-manager environment, then run:

```sh
python -m notron_service.operations rotate-cache-key
```

The transaction purges ciphertext and activates the new SHA256 key fingerprint
under a global cache-write lock. Old workers cannot write old-key ciphertext after
activation, even when a provider returns late. Restart every worker with the new
key, check readiness and synthetic inference, then reopen traffic. The command
never prints keys. Existing request/reservation tombstones remain: an old completed
request whose cache was purged returns outcome_uncertain rather than executing
again. Do not use a new request ID to hide an unresolved paid result. Backup restore
must preserve/reapply the latest key generation, deletion tombstones and revocations
before opening traffic; a stale backup can otherwise resurrect deleted access/data.

OIDC signing keys belong to the issuer: publish new JWKS key before issuer rollover,
validate unknown-kid refresh, retain overlap for the <=15-minute token profile, then
retire old keys per issuer policy. Current JWKS cache is 300 seconds; compromise
requires restarting workers to clear caches and revoking affected sessions. The
service `SIGNING_KEY` setting is reserved and does not sign managed access tokens;
changing it does not revoke OIDC tokens. Rotate Stripe/webhook/provider credentials
in their vendor dashboards plus secret manager, validate test-mode requests, then
revoke prior keys. Vendor calls/real rotations remain owner-provisioned gates.

Official contracts rechecked during Task 6: [PyJWT 2.13 usage](https://pyjwt.readthedocs.io/en/stable/usage.html),
[Nginx rate-limit module](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html),
[FastAPI deployment](https://fastapi.tiangolo.com/deployment/concepts/).
