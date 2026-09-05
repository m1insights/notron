# P01 — Privacy and permissions implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Parallel agents are optional only when explicitly authorized; do not parallelize edits to the same policy/transport modules.

**Goal:** Enforce the user's access choices and prevent avoidable secret disclosure on every outbound path.
**Architecture:** Central policy and outbound preparation, explicit state validity, Keychain-backed sensitive storage, and constrained network requests. Keep existing per-node behavior while moving shared enforcement to boundaries.
**Tech Stack:** Python 3.11+, pytest, SQLite metadata in P02, Swift/macOS Security framework for Keychain, authenticated encryption for sensitive payloads.
**Spec:** [Shared design §§1–2, 6](../design.md).
**Dependencies:** None beyond the shared design. P06 later packages the signed Keychain bridge; until then, use the injected secret store in automated tests and stop real-data processing if secure storage is unavailable.

## Global constraints

- Nebius remains the inference provider. No real note bodies/provider keys in tests or test output.
- Home permits filing; Read only permits retrieval and an explicitly requested tagged reply, never automatic filing; Ignore prohibits AI reads and writes. Zero homes is not a fallback. A tagged reply is a one-request exception, not standing permission.
- Missing/corrupt policy pauses AI processing pending setup/recovery. User-requested local preview is separately authorized.
- No regex is a guarantee against every secret. Explicit exclusion and data minimization precede redaction.
- No production `.env` credentials, plaintext fallback or automatic credential migration.

## Task 1 — Explicit policy state and zero-home semantics

**Status:** Complete, 2026-09-04. Implementation commit `2b3914d`; [verification and handoff](../handoffs/2026-09-04-P01-task-1.md). Required boundary integrations also touch the executor, watcher, graph, setup, CLI, Brain/search readiness gates, system-note readers, and Mac save transport; Task 1 itself did not implement Task 2 passage preparation.

**Files:** Modify `notron/library.py`, `notron/filer.py`, `notron/index.py`, `notron/mentions.py`, `notron/rewrite.py`, `tests/test_library.py`, `tests/test_filer.py`; Create `notron/policy.py`, `notron/persistence.py`, `tests/test_policy.py`, `tests/test_persistence.py`.
**Consumes:** Existing Library JSON with homes/ignore/decided/chosen_at.
**Produces:** `load_policy(path: Path) -> PolicySnapshot`, `PolicySnapshot.status` in `unconfigured|ready|corrupt`, `can_read(note_id: str) -> bool`, `can_file(note_id: str) -> bool`, `can_reply(note_id: str, explicit_request_id: str | None) -> bool`, `atomic_write_json(path, payload) -> None`. Missing and malformed files remain distinguishable. `can_reply` checks that the request belongs to that readable note; arbitrary model-produced IDs are not authorization.

- [x] Add explicit regression tests. Seed current valid legacy JSON, zero-home configured JSON, malformed/truncated JSON, and a note newly discovered after setup. Default newly discovered notes to read-only only when the user's validated policy explicitly enables it; default that setting off for the pilot.

```python
import json
from notron.policy import load_policy

def test_configured_zero_homes_never_allows_filing(tmp_path):
    p = tmp_path / 'library.json'
    p.write_text(json.dumps({'homes': [], 'ignore': [], 'decided': ['n1'],
                             'chosen_at': '2026-09-04T12:00'}))
    policy = load_policy(p)
    assert policy.status == 'ready'
    assert policy.can_read('n1')
    assert not policy.can_file('n1')

def test_corrupt_policy_is_not_a_fresh_install(tmp_path):
    p = tmp_path / 'library.json'
    p.write_text('{')
    policy = load_policy(p)
    assert policy.status == 'corrupt'
    assert not policy.can_read('n1')
    assert not policy.can_file('n1')
```

- [x] Run `.venv/bin/python -m pytest tests/test_policy.py tests/test_library.py tests/test_filer.py -q`; confirm the new cases fail for the intended policy behavior.
- [x] Implement a versioned policy wrapper while preserving recognized legacy choices. Empty homes never widens scope. Unknown notes are denied unless the explicit new-note setting applies. Sensitive-title exclusions remain additional restrictions. Have readers and the final executor call the same policy object. Test a direct tagged reply in a read-only note, refused automatic filing into the same note, and refused tagged access in an ignored note. Until P02 supplies durable request records, inject an explicit request capability from the current watcher call; never accept a model-supplied capability.
- [x] Implement atomic persistence: write a user-only temporary file in the same directory, flush/fsync, `os.replace`, fsync directory; retain a last validated backup. A restore from backup requires a visible recovery operation. Never silently restore a more permissive policy.
- [x] Add interrupted-write and invalid-version tests; run targeted suite. Update comments/copy describing the previous all-readable fallback. Commit task-owned files and record evidence.

## Task 2 — All model/search inputs carry provenance

**Status:** Complete, 2026-09-04. Implementation commit `e37bd3d`; [caller map](../evidence/P01-outbound-map.md) and [verification/handoff](../handoffs/2026-09-04-P01-task-2.md). Final full suite: 453 passed. No Task 3 work started.

**Files:** Create `notron/outbound.py`, `tests/test_outbound.py`; Modify `notron/brain.py`, `notron/nodes.py`, `notron/index.py`, `notron/retrieval.py`, `notron/research.py`, `notron/filer.py`, `notron/reflect.py`, `notron/care.py`, `notron/watch.py`, `tests/test_privacy.py`, `tests/conftest.py`.
**Consumes:** `Passage`, `Purpose`, `PolicyError`, validated policy from Task 1.
**Produces:** `prepare_outbound(purpose, passages)` as specified in the design; Brain accepts provenance-tagged user passages and composes text only after preparation. `research.search` prepares the query before HTTP.

- [x] Reproduce the observed leak with a mocked embedding transport and a synthetic `password: synthetic-example-only` in an ordinary approved note. Assert the secret is absent from both transport arguments and saved cache, not merely absent from the final answer.
- [x] Add this boundary test, with policy setup and network adapter fixtures shared by `tests/test_outbound.py`:

```python
from notron.outbound import Passage, prepare_outbound

def test_direct_requests_are_filtered_too():
    safe = prepare_outbound('route', [Passage(
        text='remember password: synthetic-example-only', origin='user_request')])
    assert 'synthetic-example-only' not in '\n'.join(safe)
```

- [x] Run `.venv/bin/python -m pytest tests/test_outbound.py tests/test_privacy.py -q` and inspect the failing transport-level assertions.
- [x] Change Brain's public API to consume tagged passages; `_call` remains the only inference transport. Separate static system instructions from user-derived About Me, lessons, memory, history and agenda. Convert every caller; no raw-string escape hatch for production callers. Apply the same preparation to direct routing, organizer, filer candidate titles/glimpses, reflection, search and embeddings.
- [x] Reject note-origin passages without IDs and ignored IDs even if the caller supplies their text. Strip unsafe data before formatting; reject unknown origin/purpose. Keep selection locally authoritative in managed Mac mode; the server cannot infer a full local policy from raw prompts.
- [x] Add malicious retrieved-text fixtures requesting a secret, new tool or permission change. Assert allowed operation types and policy are unchanged, independently of whether the model obeys the text.
- [x] Use `rg -n 'brain\.(ask|ask_json|embed)|research.search|chat.completions|embeddings.create' notron` to enumerate and account for every caller in `docs/production/evidence/P01-outbound-map.md`. Run all privacy/research/graph tests, then the full Python suite. Commit.

## Task 3 — Sensitive cache migration and retention

**Status:** Implementation complete, 2026-09-04. Commit `c5801df`;
[storage evidence](../evidence/P01-secure-storage.md) and
[verification/handoff](../handoffs/2026-09-04-P01-task-3.md). Full Python suite:
**489 passed**. Swift helper type-checks; signed/native Keychain and startup
integration remain explicitly gated on P06. Default real-data processing pauses.
Task 4 has not started. Additional task-owned integrations cover filing request
state, search credentials, startup/executor/fallback gates, cleanup, diagnostics,
Mac mood path, and explicit offline storage CLI commands.

**Files:** Create `notron/securestore.py`, `notron/credentials.py`, `tests/test_securestore.py`, `tests/test_credentials.py`, `mac/Sources/Notron/KeychainStore.swift`; Modify `notron/index.py`, `notron/undo.py`, `notron/brain.py`, `notron/reflect.py`, `notron/care.py`, `pyproject.toml`, `uv.lock`.
**Consumes:** Atomic persistence and policy; a Keychain-backed credential provider injected at process startup.
**Produces:** `CredentialStore.get(name) -> bytes | None`, `put(name, value)`, `delete(name)`; `EncryptedStore(root, key).write(name, data)` / `.read(name) -> bytes`. Choose a maintained AEAD implementation (`cryptography` AESGCM), lock its version, use fresh 96-bit nonces and bind the logical filename/schema version as associated data.

- [x] Add encryption/tamper and missing-Keychain tests before implementation:

```python
import pytest
from notron.securestore import EncryptedStore, IntegrityError

def test_payload_not_plaintext_and_tampering_fails(tmp_path):
    store = EncryptedStore(tmp_path, bytes(range(32)))
    store.write('undo', b'synthetic-private-note')
    p = tmp_path / 'undo.enc'
    assert b'synthetic-private-note' not in p.read_bytes()
    p.write_bytes(p.read_bytes()[:-1] + bytes([p.read_bytes()[-1] ^ 1]))
    with pytest.raises(IntegrityError):
        store.read('undo')
```

- [x] Implement Swift Security-framework Keychain access using service `com.m1labs.notron`; return secrets only over the dedicated process pipe, never command arguments/stdout logs. The Python credential interface uses the helper; tests inject an in-memory provider. P06 provides signed identity/build integration. No production data processing until that integration passes.
- [x] Change index loading to an encrypted payload format; decrypt vectors into process memory rather than retaining a persistent plaintext NumPy mmap. Keep old JSON/NPY imports offline behind a validated migration and backup; invalidate stale raw caches before cloud processing. Rebuild only selected notes through Task 2.
- [x] Encrypt undo and request-content payloads. Write files with 0600 under a 0700 directory; suppress payloads in logs/exceptions. Retain seven days of local sanitized diagnostics. Purge ignored/deleted notes from index, undo, and caches. Credentials are cleared by explicit account/key removal.
- [x] Validate round-trip migration, interrupted migration, corrupt ciphertext, rotated/lost key, ignore-after-index, deleted note, and inability to access Keychain while locked. Preserve original backups locally until the user accepts migration; never delete their Notes.
- [x] Run securestore/credentials/privacy tests and the full suite; commit. Record native Keychain tests as pending P06 until actually run.

## Task 4 — Restrict outbound URL validation and provider destinations

**Files:** Create `notron/network.py`, `tests/test_network.py`; Modify `notron/research.py`, `notron/brain.py`, `notron/nodes.py`.
**Consumes:** Prepared search strings and fixed provider settings.
**Produces:** `public_https_url(url: str, addresses: list[str]) -> bool`; a provider endpoint validator; no automatic HTTP requests to model-invented citation URLs.

- [ ] Add fixtures for loopback, RFC1918, link-local, IPv6 local/mapped addresses, metadata IPs, URL credentials, redirects and mixed public/private DNS results. Use only fake resolvers/transports.

```python
from notron.network import public_https_url

def test_private_or_mixed_destinations_are_rejected():
    assert not public_https_url('https://127.0.0.1/x', ['127.0.0.1'])
    assert not public_https_url('https://example.org/x', ['93.184.216.34', '10.0.0.2'])
    assert not public_https_url('http://example.org/x', ['93.184.216.34'])
```

- [ ] Remove the HEAD request for citations invented by the writer. Keep only URLs present in prepared trusted-source results/user-supplied context; mark unsupported citations unverifiable in the response. A reachable URL never established factual correctness anyway.
- [ ] Restrict managed inference/search destinations to configured HTTPS service domains; block redirects carrying authorization to a different host. For BYO development endpoint overrides, require an explicit development setting and no production credential fallback. Any remaining generic fetch must enforce address checks at connection time, not resolve-then-fetch vulnerable to DNS rebinding.
- [ ] Run network/citation/research tests; assert no network calls for untrusted model URLs. Commit.

## Task 5 — Security inventory and release-facing assertions

**Files:** Create `SECURITY.md`, `docs/production/evidence/P01-security-boundaries.md`, `tests/test_security_boundaries.py`; Modify `README.md` and inaccurate privacy promises in `CLAUDE.md` only where superseded by verified behavior.
**Consumes:** Tasks 1–4 evidence.
**Produces:** Threat model covering malicious content, malicious local software, supply-chain/update compromise, cloud tenant isolation, accidental leakage, and user edit loss; a documented private vulnerability reporting route chosen by the owner before public release.

- [ ] Add CI tests proving model text cannot create arbitrary operations or script source. Audit subprocess invocations for argv-safe construction and no `shell=True` on user inputs.
- [ ] Document limitations: local compromised user account, imperfect redaction, untrusted model instructions, Apple Notes non-atomic writes and provider retention. Do not advertise sandbox isolation unless implemented and verified.
- [ ] Run `.venv/bin/python -m pytest tests -q`; collect dependency vulnerability results using a pinned audit tool in a disposable environment. Findings are triaged; merely running a scanner is not approval.
- [ ] Produce the outbound-path map and security report with actual commands/results; create the handoff and update P01 roadmap status. Commit only P01-owned changes.

**Exit gate:** No known route sends excluded content; corruption never grants access; secret storage has no plaintext fallback; arbitrary model URL checks are gone. Native signing/permission security remains a P06/P07 gate. No new feature can bypass these boundaries.
