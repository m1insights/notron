# Local PostgreSQL test runner

Run `service/scripts/test-postgres.sh` from the repository checkout. It creates a
private temporary PostgreSQL cluster with a Unix socket, runs the complete service
suite, stops the cluster and removes its synthetic data. It never reads the
service deployment database setting. PostgreSQL binaries and `uv` must already be
installed; it does not install or start a system service.

`NOTRON_TEST_PG_BIN` optionally selects the directory containing `initdb`, `pg_ctl`,
`pg_dump` and `pg_restore`. For a relocated PostgreSQL distribution,
`NOTRON_TEST_PG_SHARE` can select its directory containing `postgres.bki`.
Additional arguments select pytest tests, for example:

```sh
service/scripts/test-postgres.sh service/tests/test_store.py
```

The runner uses trust authentication only on its private, mode-0700 temporary
socket directory, with TCP listening disabled. Do not reuse this configuration
for deployment. Run as a normal user: PostgreSQL refuses to initialize as root.

## Dependency audit

`service/scripts/audit-dependencies.sh` exports the complete pinned runtime dependency
set and checks public package advisory data with pinned pip-audit. It does not
upgrade packages or read deployment credentials. Network access to package/advisory
services is required. The audit uses Python 3.14 by default; set
`NOTRON_AUDIT_PYTHON` to audit another supported runtime. A clean report covers
known advisories at scan time, not all possible vulnerabilities.

Audit reference: [PyPA pip-audit](https://github.com/pypa/pip-audit). The runner
uses the exported hashes and disables dependency resolution; it checks the exact
locked package inventory without installing deployment packages into the audit tool.


## Secret and ingress checks

`service/scripts/audit-secrets.sh` runs pinned detect-secrets 1.5.0 against real
runtime service/desktop source and deployment/CI scripts. It prints counts, never
matched secret values, and exits nonzero on findings. One inline allowlist marks
the pre-existing synthetic password example in privacy.py; no test fixture or real
secret is accepted by a broad baseline. Static scanning does not prove absence of
all secrets or inspect external secret managers.

`service/scripts/test-ingress.sh` requires nginx, openssl and curl. It launches a
temporary loopback TLS proxy with a disposable certificate, validates actual Nginx
configuration, and checks changing arbitrary forwarded-IP headers cannot evade
login rate limiting or change its fixed JSON error. It never calls a provider or
deployment host. CI installs Nginx and runs this alongside the PostgreSQL runner.
