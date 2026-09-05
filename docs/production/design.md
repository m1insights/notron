# Shared production design and interface contract

Spec version: 1. Date: 2026-09-04. Status: approved product direction; concrete implementation contracts for the seven plans.

## 1. Product boundary

The user captures and organizes in Apple Notes. Notron is an organizer with short contextual conversation, not a general command-executing agent. It uses a Python workflow and a SwiftUI companion. On macOS it reads/writes Notes through AppleScript and Calendar/Reminders through EventKit. On iPhone, a user-invoked Shortcut may bridge selected content to hosted inference and supported Notes actions, subject to P04 evidence. There is no proposed iOS app in this program.

macOS 14+ remains the code floor; the first shipped binary is Apple silicon, matching the inspected build. Intel is out of the initial distribution scope unless P06 expands and validates the matrix. P04 initially tests the available supported iPhone and records its exact OS; it must not advertise a minimum iOS version until the action sequence has been tested there.

Inference stays on Nebius. Defaults: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` for routing/scheduling; `nvidia/nemotron-3-super-120b-a12b` for writing/planning/filing; `Qwen/Qwen3-Embedding-8B` for embeddings. Ultra is configured but not a release requirement. Validate account availability at setup and handle unavailable models explicitly.

## 2. Trust boundaries and privacy

Notes, websites, model output, shared notes, and local mutable preferences are not trusted code. No model output becomes shell/script source. Use fixed operations and schema validation. Neither prior conversation nor a retrieved note grants permissions.

Selection comes before reading note bodies or sending content. The user can inspect a selected note preview locally; preview does not grant AI access. Existing credentials/private-title heuristics remain additional defenses, not a claim to recognize every secret. No supported policy promises perfect secret detection.

Central proposed interfaces in `notron/policy.py` and `notron/outbound.py`:

```python
from dataclasses import dataclass
from typing import Literal, Sequence

Purpose = Literal['route', 'write', 'schedule', 'organize', 'reflect', 'embed', 'search']
Origin = Literal['user_request', 'note', 'standing', 'memory', 'lesson', 'web', 'history', 'agenda', 'model', 'diagnostic']

@dataclass(frozen=True)
class Passage:
    text: str
    origin: Origin
    note_id: str | None = None
    title: str = ''
    modified: str = ''

# Defined in policy.py and imported by outbound.py.
class PolicyError(RuntimeError):
    pass

def prepare_outbound(purpose: Purpose, passages: Sequence[Passage]) -> list[str]:
    """Recheck note access, redact supported secret patterns, reject unknown provenance."""
```

P01 integrates preparation at the transport boundary (Brain ask/embed and search), not only retrieval. Components pass origin metadata before formatting prompts. Static developer instructions are a separate trusted argument. Filter before any embedding or persistent index; legacy raw caches are quarantined locally and excluded until sanitized/rebuilt. Revoking a note purges its cache entries, memory-mapped vectors, and future context. Do not print rejected input in errors.

For the pilot: server request/response bodies are not logged; operational metadata retained 30 days; successful response retry cache expires after 24 hours; account/device/entitlement records live while needed for the service, with billing record obligations reviewed before paid release. Cached content is encrypted at rest. Provider-side retention is separately verified and disclosed before real user traffic; our retention promise must not imply control over Nebius/Tavily retention.

Locally: user-only Application Support directory, atomic state files, Keychain secrets, encrypted sensitive index/undo/operation payloads using a random key protected by Keychain. Keychain unavailable means pause, never plaintext fallback. Keep one undo snapshot per note; remove when invalidated or explicitly cleared. Keep local redacted diagnostics for seven days. Ignored/deleted notes trigger cache/undo cleanup. Developer `.env` compatibility must be explicit development mode; no automatic import/upload of old credentials.

Read-only and ignore permissions are enforced on execution by stable note ID, not just by the filing candidate list. An explicit tagged request in an approved read-only note may authorize its own appended answer in that note; it does not grant automatic filing or rewrite permission. Ignored notes never become readable because someone puts a tag inside them. This one-request reply exception preserves the existing tagged-note product surface and must be named in the permission help. Missing policy means setup required; corrupt policy means paused, with explicit recovery from a validated backup. Neither becomes an empty permissive library.

Store `Passage`/origin definitions in `notron/outbound.py`, importing policy decisions from `notron/policy.py`; do not create competing types. Track Notron's system notes by validated IDs from setup. A folder name or pasted signature is not sufficient to bypass policy, and About Me remains user-write-only.

### P01 Task 1 implemented policy contract (2026-09-04)

`notron.policy.load_policy(path)` returns a `PolicySnapshot` with `status` equal to
`unconfigured`, `ready`, or `corrupt`. `PolicyError` is defined in `policy.py`;
Task 2 should import/re-export it from `outbound.py`, rather than define another
exception type. Read permission does not imply filing permission.

The on-disk wrapper keeps the legacy top-level `homes`, `ignore`, `decided`,
`chosen_at`, and optional `start_from` fields. Python saves add `version: 1`,
`allow_new_notes: false` by default, and `system_notes` (system title to stable
Notes ID). Valid recognized legacy selections load without rewriting their file;
unknown notes remain denied unless `allow_new_notes` is explicitly true. A cutoff
further restricts newly discovered notes; an unreadable date cannot defeat it.
Sensitive-title restrictions and Ignore still win over other grants. Empty homes
never enables automatic filing. A new note created after an explicit proposal
approval becomes a Home even when there were zero homes beforehand.

Setup registers system IDs and refuses duplicate system titles. Running setup on
a fresh install records those IDs without enabling AI; a saved selection is still
required. Existing installations must run explicit setup to register missing system
IDs. Missing/deleted system notes cannot self-authorize a replacement by title.

The watcher calls `policy.explicit_reply(note_id)` around the current graph call.
Its opaque request ID exists only in that Python call context; `can_reply` requires
the same readable note and the current unconsumed capability. A successful read-only
reply consumes it; exiting the call invalidates it. Source filing ticks use the same
observed-note context. Reply and filing destinations are checked by stable ID at
execution. No capability is accepted from model output or policy JSON. P02 replaces
this temporary request identity with durable request records.

`atomic_write_json` uses a same-directory 0600 temporary file, flush/fsync,
`os.replace`, and directory fsync. Policy writers keep the previous validated
version at `.bak`, never restore it on load, and propagate failures. `notron library
recover` explicitly restores a validated backup and preserves the displaced file at
`.corrupt`; `notron library --reset` explicitly clears all note grants. Rewrite
permissions use the same atomic writer and explicit `notron rewrite --recover`.
The Mac selection screen sends JSON over stdin to `notron library save`, preserving
system IDs and settings outside that screen. These changes do not implement cache
encryption, outbound passage preparation, durable operations, or a cross-app
transaction; those remain in their assigned tasks.

### P01 Task 2 implemented outbound contract (2026-09-04)

`outbound.Passage`, `Origin` and `Purpose` are implemented in Python. The existing
`PolicySnapshot` and `PolicyError` remain owned by `policy.py`. Brain requires
`ask(*, system, user: Sequence[Passage], purpose, ...)`, equivalent `ask_json`, and
`embed(passages: Sequence[Passage])`; search requires `search(passages, ...)`.
The runtime accepts lists/tuples of passages and rejects raw strings. `_call`
prepares before every inference call/retry; embedding validates the entire batch
and rechecks each transport batch; search prepares before HTTP. Static system
instructions are separate from user-derived context.

Note-backed passages carry stable IDs, original titles and modification dates.
Missing IDs, ignored/unknown IDs, sensitive titles, failed cutoffs, unsupported
provenance/purpose, and missing/corrupt policy are refused. Standing/memory/lesson
origins must match registered system-note roles. The `model` origin identifies
reflection proposals; `diagnostic` identifies locally measured facts, date and
structural headings. These are data classifications, never permission grants.
Current requests, retrieved passages, system context and history retain their
sources through callers, including watcher and keyword retrieval.

Full note bodies are redacted before chunking and persistent indexing. The index
metadata wrapper is `{"outbound_version": 1, "notes": {...}}`. Unmarked legacy
indexes cannot supply search results, glimpses or reused vectors. A rebuild keeps
old files locally as `.unprepared` and writes sanitized metadata/vectors. This is
exclusion from processing, not Task 3's encrypted migration or retention cleanup.
Current policy still filters index results, and persistence rechecks read access.

Filing preserves explicit line groups and maps unambiguous sanitized candidate
names back to locally known destinations; ambiguous names cannot select another
home through prefix matching. Scheduler accepts only supported operation pairs
and receives only the current request/date. Model routing cannot initiate filing,
undo, or organization. Retrieved text, lessons, and model replies cannot change
policy or mint the watcher's reply capability. Guard/executor remain the final
write authorities. See the [complete caller map](evidence/P01-outbound-map.md)
and [Task 2 handoff](handoffs/2026-09-04-P01-task-2.md).

## 3. Request identity, time and operation safety

Proposed `notron/requests.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

@dataclass(frozen=True)
class RequestEnvelope:
    version: int
    request_id: str
    source: Literal['ask', 'mention', 'shortcut', 'cli', 'morning']
    text: str
    captured_at: datetime | None
    observed_at: datetime
    timezone: str
    capture_confidence: Literal['explicit', 'observed_only']
    note_id: str | None = None
    source_revision: str | None = None
    thread_id: str | None = None
```

Timestamps are timezone-aware ISO 8601; timezone is an IANA identifier. Notes expose modification time, not a reliable original timestamp per line. Never manufacture a capture timestamp from sync time. A Shortcut supplies explicit capture time. Legacy Notes requests have `captured_at=None` and `observed_only`; relative scheduling after sleep/restart/backlog requires confirmation with an explicit resolved date. Absolute past requests are rejected/clarified, never silently shifted to tomorrow. Journal headings use explicit capture date when available, otherwise label the filing date instead of claiming it is the event date.

Request identity is persisted before inference. Do not use text alone as the identity: identical wording on two days may be two valid actions. P02 stores the observed occurrence and source anchor/revision and preserves identity through retries. User-visible receipts may contain a short operation reference; metadata in copied text alone does not authorize replay.

Proposed `notron/operations.py`:

```python
from enum import StrEnum

class OperationStatus(StrEnum):
    PREPARED = 'prepared'
    APPLYING = 'applying'
    APPLIED = 'applied'
    RECEIPTED = 'receipted'
    NEEDS_REVIEW = 'needs_review'
    CANCELLED = 'cancelled'
```

Use local SQLite transactions for operation metadata and a single-process lock; encrypt content payloads through P01. The transition to APPLYING is committed before the external action. Record APPLIED and the actual external ID before writing the Notes receipt. Retry an APPLIED operation by repairing its receipt only. If a crash leaves APPLYING, reconcile the external operation reference where possible; otherwise NEEDS_REVIEW, never automatic re-creation. Prefer a visible opaque reference in created event/reminder notes. Do not claim exactly-once behavior from a SQLite transaction around a nontransactional Apple API.

For filing, model copy and source-tick as separate suboperations under one request. A missing source tick must not repeat an already verified destination copy. Before Notes writes, compare expected revision from the original model input and re-resolve target by ID. Apple Notes has no atomic compare-and-swap; serialize local operations, minimize the final read/write gap, verify after writing, retain recovery data and disclose residual multi-device races. If supported-content validation cannot prove rich object preservation, refuse replacement and offer a separate plain-text result.

Undo peeks the saved snapshot, validates the live note against Notron's last successful post-write revision, restores, verifies, then consumes the snapshot. Do not pop it before success. An intervening user edit requires a separate recovery copy, not overwriting live text.

## 4. Conversation

P03 adds `Turn(role, text, request_id=None)` and `ConversationContext(thread_id, turns, action_refs)` in `notron/conversation.py`. Select at most the three preceding complete exchanges and 8,000 total characters; retain whole turns, not severed sentences. This is a conservative input-size cap, not an exact token guarantee. The literal standalone line `New topic` is the initial boundary. Only history before the current question is eligible; all selected history passes P01.

Router and writer receive the same bounded history. A transformation of a previous answer does not trigger a new world search. A new factual question can search using a resolved query. Missing/ambiguous antecedents ask a short question. Model-generated reply markers do not count as cryptographic authorship; operation records establish action provenance. No implicit promotion of history to permanent Memory. Action follow-ups inherit no new powers.

## 5. Hosted service and mobile protocol

Proposed service lives in `service/`, Python 3.11+, FastAPI/Pydantic 2, PostgreSQL for durable account/operation/usage metadata. Pin exact dependencies at implementation and keep service dependencies separate from the desktop package. Use a managed OIDC issuer with system-browser Authorization Code + PKCE for native sign-in, and Stripe for billing. Vendor provisioning is an external input; the code targets standard discovery/JWKS endpoints with configured issuer/audience and does not implement passwords.

`POST /v1/capture` accepts a RequestEnvelope plus up to two explicitly selected destination aliases and safe titles. It returns validated data, never code:

```json
{
  "request_id": "6b0f5a2e-0e25-4b38-9e4d-64f75c6a0a02",
  "status": "proposal",
  "operation": "append_note",
  "destination": "ideas",
  "text": "Try a Saturday pottery class.",
  "requires_confirmation": true
}
```

P04 allows only `append_note` or `clarify`; no arbitrary destination URL, script, note creation, Brain Dump rewrite or calendar action. The phone maps `ideas`/`shopping` to notes selected locally. The service cannot read the user's Notes. Append original user text, with an optional explicit capture date, rather than an invented model rewrite. Require one local confirmation before writing.

P04 uses one revocable, expiring prototype token per tester stored only in their installed Shortcut. A Shortcut token is inspectable, unlike a protected app Keychain credential: limited scope, seven-day lifetime, 20 requests/day, no billing administration, never a shared provider key. Public shared Shortcut artifacts contain no token. Use a dedicated prototype audience/route policy and disable these tokens on commercial endpoints. Maximum capture text: 4,000 characters; body limit: 16 KiB; 30-second provider deadline; at most one provider attempt per request ID in the prototype. If outcome is uncertain, show review guidance rather than silently rerun.

P05 expands the service with `/v1/infer`, `/v1/embed`, `/v1/search`, `/v1/me`, worker registration/revocation and billing endpoints. Managed mode retains the local graph but routes its prepared inputs through authenticated service transport; server selects allowed model IDs and output budgets, verifies entitlements and reserves usage before each call. Client cannot supply provider keys, arbitrary provider URLs or unrestricted model IDs. BYO mode remains local credentials + direct Nebius, behind identical P01 input preparation.

Payments are verified by signed webhook events, processed idempotently and reconciled to authoritative subscription state. Authentication identifies a person; entitlement decides whether they may use paid resources. A patched MIT client cannot obtain service access without valid credentials and server checks. Signed-in does not mean paid. Capture ownership/record retrieval is always scoped to the authenticated account, never a client-supplied account ID.

One managed Mac holds a renewable worker lease. A replacement requires explicit transfer; the previous device's grant is revoked. Free BYO installations cannot enforce an account-wide lease without a server: explain single-executor support and avoid promising coordination across independent free installs.

## 6. Distribution and user experience

P06 moves mutable state to `~/Library/Application Support/Notron`; code and bundled Python remain read-only inside the app. Explicit development overrides are supported; no developer absolute paths ship. Migration copies validated legacy state with the listener stopped and a backup; it never uploads notes as a migration side effect.

First run: welcome/privacy → Notes permission → choose accessible notes and filing homes → choose managed/BYO AI and validate → optional Reminders/Calendar → seed Notes and prepare index with progress → start listener → verify one user-approved round trip. A local preview does not enable inference. Skipping optional grants disables corresponding functionality and says why. The Help flow describes sleep and pending work.

Menu state comes from a heartbeat and successful probes, not launchd registration or mood. Show starting, ready, paused, offline, permission-needed and error; distinguish last checked from last completed request. Pause/Resume are explicit. `Quit Notron` stops the worker then closes the app; simply closing a settings window does not stop it. During unavailable Keychain/network conditions, preserve pending operations without repeatedly charging retries.

P06 uses a stable signed identity for GUI, helper and agent permission paths; prove which identity owns each grant on a fresh account. It may reuse JXA/EventKit only if the signed deployment test proves correct identity/prompt behavior. Failing that gate requires a signed Swift helper design with the same adapter contract, not reliance on historical Terminal grants.

Production updates are signed, verified, delivered over HTTPS, installed with worker coordination and tested rollback. A signed/notarized full-installer manual update path is acceptable for the assisted pilot; an in-app signed update mechanism is required before public release. Use Sparkle 2 for the proposed public updater, validated against current upstream instructions during P06 implementation.

## 7. Evidence and sources

- [Apple: Notes API limitations and user-created Shortcuts](https://developer.apple.com/forums/thread/813810).
- [Apple: native background work is scheduled by the system](https://developer.apple.com/documentation/backgroundtasks/choosing-background-strategies-for-your-app).
- [Apple: Keychain secret storage](https://developer.apple.com/documentation/security/using-the-keychain-to-manage-user-secrets).
- [Apple: distribution and Developer ID](https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases).
- [OAuth native applications / PKCE](https://www.rfc-editor.org/info/rfc8252/).
- [Stripe: signed webhooks, duplicates and unordered delivery](https://docs.stripe.com/webhooks).
- [FastAPI deployment concepts](https://fastapi.tiangolo.com/deployment/concepts/).

These establish constraints; they are not proof of this app's implementation or certification. The existing 371 passing Python tests and successful Swift build were observed in the assessment, not rerun as part of writing this design. New plans add the missing production scenarios.

### P01 Task 3 implemented secure storage contract (2026-09-04)

`credentials.CredentialStore` owns `get(name) -> bytes | None`,
`put(name, value: bytes) -> None`, and `delete(name) -> None`. Approved account
names are `storage-key`, `nebius-api-key`, and `tavily-api-key`, under Keychain
service `com.m1labs.notron`. `credentials.configure(provider)` injects access;
there is no `.env` import, credential environment fallback, or automatic key
replacement. `Brain.from_credentials()` replaces `from_env()`. Non-secret
model/endpoint environment settings remain supported; Nebius and every model
identifier are unchanged. An absent optional search account disables search;
unavailable Keychain access or missing/invalid storage key pauses protected work.

`credentials.KeychainStore(helper: Path)` exchanges a bounded JSON request over
an inherited socket pair dedicated to that helper invocation. Only the helper
path, FD flag and FD number appear in argv. Standard streams are discarded;
credentials are never added to the child environment. The Swift
`KeychainStore.swift` uses Security plus a noninteractive `LAContext`, with
`kSecAttrAccessibleWhenUnlockedThisDeviceOnly` for new accounts. It includes a
conditional `NOTRON_KEYCHAIN_HELPER` entry point. **P06 must package, sign, verify
identity, and connect this helper.** `credentials.startup()` intentionally pauses
until that integration exists; there is no environment switch to bypass it.
Type-checking and mocked pipe tests do not verify signed/native Keychain behavior.

`securestore.EncryptedStore(root, key)` implements `.write(name, data: bytes)` and
`.read(name) -> bytes`. Payloads use `cryptography==50.0.1` AESGCM with a 256-bit
key, a fresh 96-bit nonce on each write, full authentication tags, and authenticated
logical filename plus version header (`NOTRON-AESGCM` + version byte 1). Files
are `<name>.enc`, 0600, below a 0700 directory. Writes use Task 1 atomic
replacement/fsync; migration marker deletions also fsync their directories.
Invalid paths/symlinks are refused. Wrong keys, tampering, truncated files, unknown
versions, and malformed payload schemas pause processing with content-free errors.

Production JSON consumers use `read_json(path)` / `write_json(path, dict)`, which
resolve `path.stem + '.enc'`, check credentials and migration readiness, and refuse
legacy plaintext. Missing payloads are empty only after those checks. The index
retains `outbound_version: 1`, with vectors inside the authenticated per-note rows.
No persistent NPY file or memory map is used. Rebuilds still use Task 2 provenance
and redact complete notes before chunking, embedding, and persistence. Undo keeps
one **exact encrypted original body** per note; it is deliberately not redacted,
so restoration remains lossless. Filing proposals/judgments/shapes and reflection
run content are encrypted too. No P02 durable request/operation store is invented.

Sensitive content, scanner metadata, usage counters, mood metadata and diagnostics
live in `~/Library/Application Support/com.m1labs.notron`. The Mac mood reader
uses that same path. Existing policy/rewrite/onboarding paths remain unchanged;
Python atomic metadata writes now harden existing parent permissions. Mood and
usage contain presentation state/numeric counters, not request bodies. Background
launchd stdout/stderr go to `/dev/null`; the listener emits only fixed diagnostic
event counters. Diagnostics and validated day/tier/token usage retain seven calendar
days and are pruned on processing. No provider/request/response bodies are logged.

`retention.require_ready()` validates credentials, active storage, migration gates,
legacy-cache exclusion and policy before protected use. `retention.reconcile()`
uses a successful metadata-only Notes inventory and current title/date-aware policy
before manual graph, filing, reflection, care, daily, build, semantic search and
listener sweeps. It purges index/undo and invalidates mixed-source request/reflection
caches on deletion/revocation. Permission saves purge before future reuse; locked
storage leaves the restrictive policy saved and processing paused until cleanup can
finish. Listener pending text/failure/scanner memory is also cleared for excluded
IDs. A failed inventory cannot be treated as an empty library. Atomic per-file
updates are not a cross-process transaction; P02 still owns concurrency/recovery.

Offline migration is explicit: `storage initialize`, `storage migrate --source …
--target …`, then **separately** `storage accept`. The native startup gate applies
to these commands too; only injected synthetic providers were used in P01 tests.
Initialization generates a random key only for an empty destination with no existing
key. Migration accepts an empty target or its sole authenticated initialization
marker. It recognizes legacy JSON/NPY, Task 2 `.unprepared` index files, undo,
filing/reflection/scanner/usage/mood files and old background logs; it never scans
credential files. Recognized files are validated and backed up byte-for-byte in
**encrypted** recovery copies; plaintext originals remain user-only until acceptance.
Raw/prepared legacy vectors are not reused: the active index starts empty and must
be rebuilt from currently approved notes. Legacy request/reflection state is
invalidated; policy-approved undo bodies survive exactly.

An authenticated manifest records source/backup hashes and verified output hashes.
Incomplete/pending/accepting migrations cannot process, even if the plaintext status
sidecar is lost, and direct JSON cache consumers enforce the same gate. Resume
validates source identity/content and finishes interrupted publication. Acceptance
validates outputs and recovery before retiring plaintext originals **and** encrypted
recovery copies. Interrupted acceptance is resumable; corruption while cleanup is
pending preserves remaining recovery copies and pauses. Until acceptance the offline
originals/backups are recovery material, not active caches; no cloud use is allowed.
Explicit acceptance ends that retention exception. Deletion is ordinary filesystem
unlink, not a forensic-erasure promise on APFS/snapshots/backups.

A lost storage key cannot decrypt old data. Replacing the Keychain key does not
silently regenerate, import, or bypass the old ciphertext: processing pauses.
Restoring the exact original key restores access; planned key rotation must retain
it until an explicitly validated offline re-encryption/rebuild is accepted. P01
provides no automatic rotation or legacy-key fallback. Memory erasure, a compromised
local user account, provider retention, signed bridge behavior, and OS backups are
not proven by unit tests. See [Task 3 evidence](evidence/P01-secure-storage.md).

### P01 Task 4 implemented network contract (2026-09-05)

`network.public_https_url(url: str, addresses: list[str]) -> bool` is a pure
predicate: it neither resolves nor fetches. It requires HTTPS without URL
credentials, well-formed public host syntax and a nonempty, entirely public
address set. Loopback, private, unspecified, link-local/metadata, shared-address,
multicast/reserved/documentation, IPv6 local/scoped/mapped and translation/tunnel
addresses are refused. A public-looking DNS answer cannot launder a prohibited
literal URL. The predicate alone never authorizes a request.

`validate_provider_endpoint(url, service, *, development=False) -> str` returns
the canonical base or raises `NetworkPolicyError`. That exception inherits the
existing `policy.PolicyError` and the pinned SDK's `OpenAIError`, so neither SDK
wrapping nor graph availability fallbacks convert a security refusal into a
successful fallback. Late credential, storage and policy failures at the adapter
are translated to this same sanitized fail-closed exception.

Production endpoints are exactly `https://api.tokenfactory.nebius.com/v1/` and
`https://api.tavily.com/search`, port 443. `ProviderTransport` enforces the actual
request host, method and path for all four operations: Nebius POST
`/v1/chat/completions`, POST `/v1/embeddings`, GET `/v1/models`; Tavily POST
`/search`. Other hosts/routes, userinfo, queries, fragments and ports fail closed.
No wildcard suffix matching, generic fetching, proxies or redirects are allowed.
All 3xx responses stop before following even same-host/relative redirects.
The HTTP path uses the existing OpenAI SDK over `httpx2`, with a shared
`http.client.HTTPSConnection` adapter instead of separate default transports.
Existing installed/locked versions are now explicit: `openai==3.7.0`,
`httpx2==2.12.0`; no provider or model configuration changed.

The adapter resolves once per connection, validates **every** returned address,
then calls `socket.connect` with a numeric sockaddr. It checks the peer against
that address and authenticates TLS using the original approved hostname with
certificate/hostname validation enabled. It never resolves the hostname again
between validation and connection. There is no remaining generic fetch. Every
request rechecks secure readiness and credentials. Only fixed JSON headers and
the current endpoint's injected authorization reach the wire; SDK environment
headers, proxy authorization, organization/project headers and cookies are not
forwarded. SDK automatic retries are disabled; the existing Brain reasoning
retry still prepares and rechecks inputs. Nebius retains the SDK's 600-second
read/write and five-second connection budgets; Tavily retains 20 seconds.

The existing `NEBIUS_BASE_URL` override may select another public HTTPS domain
with the same `/v1/` routes only when `NOTRON_DEVELOPMENT=1`. It requires the new
allowlisted credential account `development-nebius-api-key` (`DEV_NEBIUS_KEY`)
through the **same** injected `CredentialStore` and Swift name allowlist. Missing
that key pauses, even when production or environment keys are present. Direct
Brain constructor keys cannot bypass the store. Default Nebius uses only its
production account, including in development mode. Changing/disabling development
mode or removing its credential blocks an existing development client. No search
override or managed hosted service is invented. P06 startup still refuses real
processing; the development flag does not bypass signing, Keychain or storage.

Writer and planner citations are checked locally against exact URL tokens from
`prepare_outbound('write', ...)` over the same prompt's `web`, `user_request` and
`note` passages. Note identity/policy and redaction are rechecked. Raw context,
model/history/diagnostic output and mere substring matches cannot ground a URL.
Every unsupported citation is replaced with “citation unverifiable — link
removed,” without DNS, HEAD or any other network call, including beyond five
links. CJK/Markdown wrappers and adjacent citations are handled locally; removing
an unsupported URL cannot corrupt a different supported URL with the same prefix.
Only numeric removal counts enter traces. Grounding proves provenance, not source
truth or current reachability. `research.check_url` is removed.

Verification and limits: [Task 4 handoff](handoffs/2026-09-05-P01-task-4.md) and
[complete outbound/HTTP map](evidence/P01-outbound-map.md). Native signing/TLS/
Keychain/provider availability remain unverified; tests use synthetic adapters.
Tasks 1–3 policy/provenance/encryption/migration/retention protections and the P06
signed-startup gate remain in effect. Task 5 evidence and remaining release gates
are recorded below.

### P01 Task 5 implemented security-boundary contract (2026-09-05)

The existing `Passage`, policy, credential, network and encrypted-storage contracts
remain authoritative and unchanged. Model routing/answers cannot add operation
kinds, directly grant permission, or select executable source. `scheduler` validates
that present title/where/notes/when/ends fields are strings or null before building
an existing `Action`; kind/op remain limited to reminder create/complete and event
create. Extra response keys are ignored, never copied into policy or request state.
This does not guarantee correct model interpretation of user intent.

`eventkit.run(body, *, data: dict | None = None, timeout=30, runner=None)` accepts
trusted source constants only. Dynamic calendar window/create and reminder
create/complete use `data` as one JSON argv value; a fixed `function run(argv)`
parses it into `input`. Dynamic bodies explicitly return JSON; no-data readers
retain their static final-expression JSON contract. `_osascript(script, timeout,
*args)` passes argv directly with no shell. Injected dynamic runners receive the
script, timeout and serialized argument; injected calendar/reminder callers receive
the fixed body plus `data=`. There is no model/raw-text script entry point.
AppleScript continues fixed stdin + argv. Launchd builders use `plistlib.dumps`
with fixed `ProgramArguments`; paths cannot inject XML or additional arguments.

`tests/test_security_boundaries.py` exercises production model parsing, nodes,
executor and subprocess construction with hostile synthetic inputs. Existing
credential tests exercise real local pipe IPC with a fake helper. Global pytest
guards deny unmocked subprocess run/Popen as well as DNS/socket connections.
A checked-in CI workflow runs the default suite with frozen lock dependencies;
local tests are verified, hosted CI has not run. Swift/JXA execution, signed identity
and permissions are not proven by this test suite.

[SECURITY.md](../../SECURITY.md) and the
[security report](evidence/P01-security-boundaries.md) describe the threat model,
subprocess/HTTP inventory and pip-audit 2.10.1 results for every registry package
version in uv.lock. No known advisories were returned; build tooling, interpreter,
OS, future updater and unpublished vulnerabilities remain outside that result.
No dependency/provider/model changes were made.

P01 Tasks 1–5 implementation and synthetic regressions are complete. The owner
has not established a documented private vulnerability reporting route; that is
an explicit public-release blocker. Compromised local accounts, imperfect redaction,
untrusted model instructions, provider retention and Apple Notes non-atomic writes
remain limitations. P02 reliability, P05 hosted tenant isolation, P06 signed/native
startup/distribution and P07 independent security/release evidence remain open.
Default real processing stays paused; no sandbox or production-readiness claim.

### P02 Task 1 implemented request/ledger contract (2026-09-05)

`requests.RequestEnvelope` implements the version-1 identity, source, text, aware
capture/observation timestamps, IANA timezone, capture confidence, note ID, source
revision and optional thread ID. Encrypted envelope-only context adds
`source_text`, `reply_to`, `here`, and `source_modified`. Notes captures remain
`captured_at=None` / `observed_only`; CLI/morning captures are explicit. `create`
accepts `timezone_name`; otherwise it resolves validated `TZ` or the macOS
`/etc/localtime` zoneinfo link, and pauses if it cannot establish an IANA zone.
UTC timestamp serialization does not replace the user's timezone. Time resolution
and delayed-date confirmation remain Task 5.

`operations.OperationStore(path, payload_store)` owns SQLite schema version **1**,
with `requests`, `operations`, and `observations` tables. Each connection enables
foreign keys, WAL, and `synchronous=FULL`; state changes use `BEGIN IMMEDIATE`
transactions and compare-and-swap checks. The database is 0600 in a 0700 directory;
symlink database/sidecar paths and unknown schema versions are refused. SQLite
contains IDs, hashes, encrypted references, statuses, timestamps, source/target
IDs, expected revisions, external IDs and fixed failure codes, never note bodies.
Encrypted payload references are immutable and committed before SQL references;
orphan payloads are removed under the same transaction lock. Unknown schemas
require explicit migration, and no legacy state is automatically imported.

`prepare(request_id, operation_id, payload_hash, *, payload=None, source_id=None,
target_id=None, expected_revision=None)` returns a frozen `Operation` record.
`payload` is bytes and, when supplied, must match its SHA-256 digest. Identity
reuse must match request, digest and source/target/revision metadata; an existing
payload cannot be replaced. `get`, `payload`, `pending` and
`transition(operation_id, expected, target, external_id=None, *, failure_code=None)`
provide durable reads and checked transitions. `pending` includes APPLIED and
NEEDS_REVIEW until receipted/cancelled. Valid edges are PREPARED → APPLYING,
CANCELLED or NEEDS_REVIEW; APPLYING → APPLIED or NEEDS_REVIEW; APPLIED → RECEIPTED
or NEEDS_REVIEW; NEEDS_REVIEW → APPLIED or CANCELLED only after an explicit outcome
determination. No edge permits blind reapplication. External IDs cannot change.
`source_id` denotes a source Notes ID for retention; external system IDs belong
in `external_id`. A standalone preparation creates a request metadata stub, which
cannot be inferred/replayed without a captured envelope.

`requests.RequestStore` shares this ledger. `observe` commits all current source
occurrences before the watcher starts its settling timer. Anchors and source spans
are encrypted; note ID/revision identify the observation. Unique unchanged pending
anchors follow unrelated edits and retain original observation time. Changed
unexecuted text cancels its old request and creates a distinct request. Initially
separate identical spans receive distinct IDs. Ambiguous copied/reordered pending
turns stop for review. Completed turns remain recognized while present; a later
identical unanswered turn following the completed turn's receipt receives a new
ID even when no empty poll intervened. Without distinguishable occurrence evidence,
identity cannot recover an unobserved delete/retype of identical text at precisely
the same position; no Apple per-line identity or authenticated signature is claimed.

`graph.run_request(envelope, *, brain, dry_run=False)` commits/captures and atomically
claims the request before the first node. `graph.run` remains a wrapper and accepts
`request_id`. `State` exposes `request_id`, `envelope` and `source_revision`.
Completed, cancelled, running and review-needed IDs do not infer again. A live
source-revision check precedes admission; a changed source waits for re-observation.
Exceptions/failed results stop at request `needs_review`; a crash leaving `running`
also blocks replay. Request completion describes graph completion, **not verified
receipts or exactly-once effects**. Side-effect reconciliation and inference/receipt
recovery remain Task 3. Dry runs capture metadata but neither claim nor complete a
real request, and continue to pass `dry_run` to effect-producing nodes.

Ask and mention paths carry persisted envelopes into the graph; independent
settling/cooldown keys use full request IDs. Tagged context is bounded/redacted from
the same observed body and refreshed with its revision before inference. Envelopes
and caller-supplied IDs never grant permissions: only the existing watcher-scoped
`policy.explicit_reply` capability authorizes a reply. `requests.active_request()`
provides correlation during graph/filing execution, never a permission capability.
CLI `ask` and `file` accept `--request-id`. Automatic Brain Dump batches persist
before settling and use `requests.run_job` to claim before `filer.run`; CLI file
uses the same admission. Care/reflection/index maintenance job orchestration and
worker exclusivity remain Task 6; source/effect revision protection remains Task 2.

P01 retention now covers request and operation payloads. Policy changes invalidate
mixed-source payloads; successful live inventories remove deleted/unreadable source
content. Durable content-free tombstones prevent regrant/retry from resurrecting
purged work. Purged Notes observation history requires explicit review before more
automatic work in that source; there is no automatic reset. Encrypted orphan deletion
uses directory fsync. Metadata/pending history is retained conservatively; bounded
ledger compaction, review/reset UI, old scanner migration, receipt recovery, worker
leases and retry budgets remain Tasks 3/6. These are required before real use;
P06's signed-startup pause is unchanged.

Evidence: [Task 1 handoff](handoffs/2026-09-05-P02-task-1.md). No dependency,
provider or model changed, no live data/provider/native execution, and no merge or
push. Task 2's separately authored acceptance tests are intentionally excluded from
Task 1's gate while that sequential task is unimplemented.
