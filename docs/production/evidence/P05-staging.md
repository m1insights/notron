# P05 local qualification and pending staging gates

Date: 2026-09-09. **Local coding evidence only; no hosted staging deployment.**
The owner authorized coding while away, with real-device/account checks deferred.

## Observed local validation

- Desktop: 1,343 synthetic Python tests passed (baseline 1,264).
- Service: 238 tests passed, zero skipped, against disposable PostgreSQL 18.6.
- Swift: 17 session/IPC/lease tests passed; app build passed.
- Installed service wheel imports successfully with shared privacy source and all
  migration/rollback resources. No repository-working-directory import fallback.
- Runtime dependency audit reports no known advisories; secret scan reports no
  unreviewed findings. Existing synthetic example annotation is narrowly scoped.
- Real temporary loopback TLS/Nginx harness returns rate-limit JSON and ignores
  spoofed forwarded addresses for its peer-IP limit. No host was provisioned.
- Database permissions harness verifies the runtime cannot perform schema or key-
  generation/evidence administration; operator role can rotate key generation.
- End-to-end synthetic default factory path: signed identity/discovery, capped
  pilot grant, lease, real provider HTTP adapter with fake HTTP, encrypted replay,
  account deletion. External TCP/DNS/libpq calls are blocked by test isolation.

## Implemented failure coverage

Wrong/expired/ID tokens, key rotation and issuer outage; account/device isolation;
Keychain/local sign-out cleanup failures; duplicate/out-of-order/refunded Stripe
payments; interrupted checkout/cancellation and late webhooks; concurrent allowance
and global spend boundaries; unknown provider cost; changed request payload and
expired encrypted cache; revoked source during token refresh; malformed responses
and usage; lease transfer/expiry/loss before effects; exact local recovery;
deleted-account cost retention; fair retention batches; old-key write fencing;
rate limiting and privilege separation. Tests use synthetic data only.

Independent task reviews and the final whole-branch integration review passed;
no critical or important finding remained open for local integration.

## Final branch verification

On the final code tree (through `93cfd98`), all commands exited 0:

```sh
.venv/bin/python -m pytest tests -q -o addopts=''
service/scripts/test-postgres.sh
swift test --package-path mac
swift build --package-path mac
```

The service runner used the local PostgreSQL 18.6 binary/share overrides documented
below. Desktop: 1,343 passed in 27.66s; service: 238 passed in 14.09s, zero skipped;
Swift: 17 passed and build completed. One upstream Starlette/AnyIO deprecation
warning remains; it did not fail the service suite.

## Commands and reproducibility

See [operations](../../../service/OPERATIONS.md),
[test/audit scripts](../../../service/scripts/README.md), and
[managed handoff](../handoffs/2026-09-09-P05-managed-service.md).
Local scripts create and remove their own private PostgreSQL cluster. No live
Notes adapter, credential, Stripe account or paid provider call is needed.

## Required external qualification — still open

- Approved host/region/domain, actual Docker image startup/deployment and production
  TLS/egress monitoring. Checked-in CI is not an observed remote CI run.
- Chosen identity provider, client/callback profile, verified-email flow, real
  refresh/JWKS rotation and signed Mac/browser/Keychain/helper round trip.
- Real Stripe test clocks, delayed payment and subscription-cancellation behavior,
  reviewed selling prices/allowances/top-up terms, financial retention period.
- Reviewed Nebius/Tavily rates, cost conversion, maximum image/token accounting,
  provider retention settings and privacy disclosures.
- Identity-provider erasure/evidence procedure; disclosure of permanent pseudonymous
  non-resurrection hashes and purpose-limited retained financial/security records.
- Hosted restore rehearsal, alert delivery, independent security review and P07
  release approval. P06 secure startup stays closed; real processing stays paused.

Passing local tests does not establish these external gates. No deployment, live
billing, automatic overage, push, real Notes access or release activation occurred.
