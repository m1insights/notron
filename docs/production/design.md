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
Origin = Literal['user_request', 'note', 'standing', 'memory', 'lesson', 'web', 'history', 'agenda']

@dataclass(frozen=True)
class Passage:
    text: str
    origin: Origin
    note_id: str | None = None

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
