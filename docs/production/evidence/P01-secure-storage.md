# P01 Task 3 secure storage evidence

Implementation scope: sensitive cache encryption, injected credentials/Keychain
bridge source, explicit offline migration, cleanup and local diagnostic retention.
No Task 4 URL/provider destination changes. No live Apple/Keychain/provider tests.

## Data inventory

| Data / caller | Active handling | Legacy / retention |
|---|---|---|
| `index.build`, `search`, `glimpses`, `exists`, `_load`, `_save` | AES-GCM `index.enc`, prepared text + in-memory vectors | No JSON/NPY/mmap reuse; explicit migrate and approved-note rebuild |
| `undo.save`, `pop`, executor pre-write snapshot | AES-GCM `undo.enc`; exact original body | One per note, consumed on restore; revoke/delete purge |
| `filer._state`, `_save` | AES-GCM `filer.enc`; judgments/proposals/source items/shapes | Invalidate mixed-source cache on policy/inventory removal; legacy invalidated |
| `reflect._state`, `_record` | AES-GCM `reflect.enc` | Bounded existing run history; policy/inventory removal invalidates; legacy invalidated |
| Readiness policy signature / inventory | AES-GCM `retention.enc`, `inventory.enc` | Current policy and selected live note IDs only |
| Storage initialization / migration | Authenticated `key-check`, manifest and interruption marker | No replacement key on loss; no use before acceptance |
| Recovery copies | AES-GCM exact original bytes in `migration-backup` | Retained until separate acceptance; plaintext originals user-only until then; both retired on acceptance |
| Listener pending requests / retries / scanner state | Pending text stays in process memory; excluded IDs cleared | Metadata-only `seen.json`, private atomic writes and purge |
| Brain/care usage | Numeric day/tier/call/token fields only, private JSON | Seven calendar days; unknown fields dropped |
| Care/Mac mood | Presentation status only, private atomic JSON | Single current status |
| Diagnostics / launchd logs | Fixed code counters, private daily JSON; stdout/stderr `/dev/null` | Seven calendar days; legacy logs handled only by offline migration |
| Policy / rewrite / onboarding | Existing Task 1 policy/rewrite authority and paths retained | Python state writers harden parent to 0700; not credential storage |
| Nebius/Tavily credentials | Keychain provider interface, dedicated process pipe | No environment or `.env` fallback; explicit put/delete only |

All new managed content/metadata paths use Application Support. Python inference
and search recheck credentials at each actual transport; readiness also covers
manual entry points and final executor operations. Broad model fallback handlers
rethrow credential/storage/policy failures. Task 1/2 scope/provenance remains local.

## Failing regressions observed before fixes

- Initial securestore/credentials/migration tests: **16 failed**. Missing interfaces
  plus existing plaintext undo/request state reproduced the required failures.
- Locked Apple access and old repository-cache bypass: **2 failed, 13 passed**.
- Interrupted manifest publication and deleted status-sidecar bypass: **2 failed**.
- Malformed encrypted index schema, existing directory mode and usage retention:
  **3 failed, 1 passed** in selected tests.
- Direct cache access before acceptance and interrupted backup cleanup: **2 failed**.
- Renamed sensitive note and listener in-memory excluded content: **2 failed**.
- Initialize→migrate lifecycle and corrupted active output during acceptance:
  **2 failed**. Both independent review findings now have regressions.

Additional validation covers nonce freshness, filename/schema authentication,
truncation/tampering, wrong/rotated/lost key, unavailable/locked provider, pipe-only
secrets, fixed errors, offline JSON/NPY/.unprepared migration, unchanged source and
backup validation, accepted cleanup, ignored/deleted cache removal, index vector
round-trip/reuse/search without `numpy.load`, and all existing privacy protections.

## Native and dependency evidence

- `swiftc -typecheck -D NOTRON_KEYCHAIN_HELPER -parse-as-library
  mac/Sources/Notron/KeychainStore.swift`: exit 0, no warnings after replacing the
  deprecated authentication-UI option with noninteractive `LAContext`.
- Swift code was type-checked only. The helper was not launched and no Keychain
  access occurred. Signed/native get/put/delete, lock/unlock, identity, entitlements,
  installation and credential setup/removal UX remain **P06 gates**.
- `cryptography==50.0.1` is pinned in `pyproject.toml` and `uv.lock`; installed into
  the explicitly requested root venv for tests. The published
  [AESGCM API](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM)
  specifies authenticated associated data and unique nonces. No vulnerability audit
  or release approval is claimed; the pinned audit belongs to P01 Task 5.
- Independent review and focused re-review covered migration gates, recovery
  retention, manual deletion, startup provisioning and interrupted acceptance.
  Final focused review reported no actionable issues; independently reran **36
  securestore/credentials/migration tests**, all passed.

Final required suite commands/results are recorded in the
[Task 3 handoff](../handoffs/2026-09-04-P01-task-3.md).
