# P05 Task 1 — service foundation evidence

Date: 2026-09-09. Scope: local foundation only; Tasks 2–6 remain open.

- Python 3.14.2, FastAPI 0.141.1, Pydantic 2.13.5, Psycopg 3.3.5 and PostgreSQL 18.6.
- `uv run --project service pytest service/tests -o addopts='' -q`: **56 passed**, including 13 PostgreSQL tests. One upstream Starlette/AnyIO deprecation warning.
- PostgreSQL ran privately over a temporary Unix socket, with synthetic data in unique disposable schemas. No production database was used.
- Tests cover invalid production settings, fixed provider destinations, body limits including chunked requests, safe validation/unexpected errors, readiness, account ownership, device revocation, identity uniqueness, composite foreign keys, concurrent migrations and schema drift.
- A real `pg_dump` / `pg_restore` round trip preserves synthetic data; migration disable/reactivate is also tested. Rollback does not drop account data.
- Built a wheel and imported the installed package from outside the checkout; packaged migration SQL is present.
- Independent Task 1 review found no remaining critical/important findings after exception-log privacy and proxy buffering fixes.

Only health endpoints are mounted. Authentication, billing and provider execution
are intentionally subsequent tasks. Settings do not themselves enforce spending.
No external account was selected, no paid API was called and no service was deployed.
Docker was unavailable: container startup and actual TLS ingress require staging
validation. Deployment inputs and commands are in
[deployment-inputs.md](../../../service/deployment-inputs.md).

Desktop regression: `uv run pytest -q` exited 0; all 1,264 tests passed.
