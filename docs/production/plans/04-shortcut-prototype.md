# P04 — Mobile Shortcut feasibility and prototype plan

> **For agentic workers:** Use `superpowers:executing-plans` for the tasks below. Task 1 is a feasibility experiment: do not represent untested Shortcuts behavior as a finished integration.

**Goal:** Find out whether Becky can explicitly capture and file one thought on her iPhone while her Mac sleeps.
**Architecture:** A user-installed Apple Shortcut selects text and two local destination notes, calls an authenticated classification service, confirms a proposal, then uses Apple's built-in append action. No iOS application is built.
**Tech Stack:** Apple Shortcuts; Python 3.11+/FastAPI/Pydantic 2; PostgreSQL metadata; Nebius Super for classification. Service dependencies live in `service/pyproject.toml` and its own lockfile.
**Spec:** [Shared design §5](../design.md); [Apple's Notes API/Shortcuts limitations](https://developer.apple.com/forums/thread/813810).
**Dependencies:** Task 1 is synthetic/local-only and can start immediately. Hosted tasks require P01 input/network contracts and P02 request IDs; P05 reuses the service after hardening, but subscriptions are not required for this experiment.

## Global constraints

- One thought, two explicitly selected ordinary text destinations, manual trigger and confirmation.
- No passive Notes monitoring, full Brain Dump scan, native iPhone Notron app, note rewrite, note deletion, automatic note creation, calendar action or provider API key embedded in the shortcut.
- Do not claim arbitrary app-controlled access to Notes. The user assembles/installs a Shortcut containing Apple's actions.
- Real-iPhone behavior is a gate. Missing hardware blocks device execution, not documentation/local service tests.

## Task 1 — Prove the Notes action sequence without a server

**Files:** Create `shortcuts/capture-one-thought.md`, `shortcuts/README.md`, `docs/production/evidence/P04-device-matrix.md`.
**Consumes:** A real iPhone, a synthetic test Notes account/folder and permission to edit those test notes.
**Produces:** Recorded action names/parameters, installed Shortcut export if available, actual device/OS version, and a go/no-go decision. No fabricated `.shortcut` binary or installation link.

- [ ] Create two synthetic notes, `Notron Test Ideas` and `Notron Test Shopping`, with one baseline line each. Keep the test Mac asleep or explicitly stop its listener; record which condition was tested.
- [ ] Assemble this workflow in Apple's Shortcuts editor. Record the exact localized action names when they differ:

```text
Ask for Input (Text): "What would you like to remember?"
If input has no text: Stop This Shortcut
Choose from Menu: "Ideas" / "Shopping"
  In each branch, select the corresponding test Note object locally
Show Alert: "Add this thought to [selected note]?" with Cancel enabled
Append to Note: original input, target = locally selected Note object
Show Result: "Added to [selected note]"
```

- [ ] Verify selecting a persistent Note object is supported; if only Find Notes is available, filter by exact folder/title and require exactly one result. Zero or multiple matches must stop for selection, not choose the first. This is a feasibility branch with explicit pass/fail criteria, not assumed functionality.
- [ ] Test cancellation, renamed target, two notes with the same title, locked note, no Notes permission, phone offline, app background/lock mid-run, long text, emoji and share-sheet invocation if available. Capture screenshots only of synthetic content.
- [ ] Export/share only a credential-free shortcut after checking the export. Record installation and permission prompts on a second test device or fresh Shortcut installation.
- [ ] **Decision:** proceed only if append preserves the baseline and the user can reliably select targets. Otherwise stop hosted filing work and report the limitation; a text-only capture/suggestion experiment may be proposed separately, not silently substituted for working Notes filing.

## Task 2 — Shared service schema and fake classifier

**Files:** Create `service/pyproject.toml`, `service/notron_service/__init__.py`, `service/notron_service/app.py`, `service/notron_service/schemas.py`, `service/notron_service/capture.py`, `service/tests/conftest.py`, `service/tests/test_capture.py`, `shortcuts/response-examples.json`.
**Consumes:** RequestEnvelope v1 serialized without desktop-only secrets; destination aliases `ideas` and `shopping`.
**Produces:** `create_app(settings, services)`, with `services.capture` injected, and `POST /v1/capture`; strict `CaptureRequest` / `CaptureResponse`; optional `clarify` response with no operation. P05 extends the same services container rather than changing the app factory signature.

- [ ] Write contract tests using FastAPI's in-process TestClient and injected fake classifier; the public route must not call `graph.run`, which reads live Mac Notes.

```python
def test_classifier_cannot_choose_an_unselected_destination(client, classifier, capture_payload):
    classifier.result = {'destination': 'private-vault'}
    response = client.post('/v1/capture', json=capture_payload,
                           headers={'Authorization': 'Bearer synthetic-test-token'})
    assert response.status_code == 200
    assert response.json()['status'] == 'clarify'
    assert response.json().get('operation') is None
```

Create fixtures with fixed UUID request ID, aware timestamp, `America/New_York`, original synthetic text and the two aliases. `client` injects a fake token verifier; authentication rejection tests must use the real verification code from Task 3 before hosted deployment.

- [ ] Validate types, extra fields, 4,000-character text/16 KiB body limit, UUID ID, timezone, two-alias maximum and enum operations with Pydantic. Return only `append_note` or `clarify`; never scripts, arbitrary note IDs or URLs.
- [ ] Echo the original prepared user text as the append text; AI chooses a permitted alias or clarification. Apply P01 preparation before classification; keep private note bodies off the service entirely.
- [ ] Lock service dependencies separately. Run `uv run --project service pytest service/tests/test_capture.py -q`; commit schema, fixtures and fake-server buildable slice.

## Task 3 — Authenticated, bounded Nebius classification

**Files:** Create `service/notron_service/config.py`, `service/notron_service/principals.py`, `service/notron_service/prototype_auth.py`, `service/notron_service/usage.py`, `service/notron_service/providers.py`, `service/notron_service/store.py`, `service/migrations/001_capture.sql`, `service/tests/test_prototype_auth.py`, `service/tests/test_capture_retries.py`, `service/Dockerfile`; Modify capture/app modules.
**Consumes:** Prepared inputs, RequestEnvelope, selected alias schema; runtime-managed Nebius secret.
**Produces:** Scoped expiring prototype tokens stored hashed, transactional request deduplication/usage counters, bounded model call, authenticated response cache; common `Principal(account_id, device_id, scopes, kind)` consumed by P05.

- [ ] Implement token generation with a cryptographic random value and store only a keyed hash plus expiry/scope/account ID. Set seven-day expiry, `capture:prototype` scope, 20 requests/day; never use the server's provider key as the client token.
- [ ] Write authorization and cross-account tests, then implement the boundary:

```python
def test_revoked_token_cannot_spend_provider_credits(client, token_store, provider, capture_payload):
    token = token_store.issue(account_id='tester-a', days=7)
    token_store.revoke(token)
    response = client.post('/v1/capture', json=capture_payload,
                           headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401
    assert provider.calls == []
```

- [ ] Use a transaction keyed by authenticated account plus request ID. Same ID/same payload reuses result; same ID/different payload returns 409; another account cannot retrieve the result. Reserve daily quota before inference. Store encrypted successful response for at most 24 hours for retry; purge on expiry. Metadata contains no note text.
- [ ] One provider attempt, 30-second deadline, no web search, fixed Super ID and bounded output. Timeout with uncertain upstream completion is not automatically retried; response says retry requires a deliberate new request. Bound costs even when a tester edits the visible Shortcut token/actions.
- [ ] Deploy only after an owner-approved host/region/spend ceiling is recorded in `service/deployment-inputs.md`. Use HTTPS, environment-injected server secrets and sanitized logs; no public unauthenticated demo endpoint. Container tests can run before any external provisioning.
- [ ] Run service auth/capture/retry tests against disposable PostgreSQL; record the actual staging address privately, never tokens, in the handoff. Commit code, not secrets.

## Task 4 — Connect the Shortcut and handle uncertainty

**Files:** Modify `shortcuts/capture-one-thought.md`; Create `shortcuts/install-test.md`, `shortcuts/failure-cases.md`, `service/tests/test_mobile_contract.py`.
**Consumes:** Task 1 proven Notes action sequence, Task 3 endpoint and per-tester token.
**Produces:** A tested user-triggered workflow, with its own request UUID created before the network request and retained during that invocation.

- [ ] Add `Generate UUID`, current ISO 8601 date, timezone, destination aliases, and Get Contents of URL POST with JSON. Token is entered only in the tester's installed copy; public shared export has an empty setup parameter and no secret.
- [ ] Extract typed response fields and compare echoed request ID. Reject unexpected status/operation/destination or missing text. Map destination alias to the locally selected Note object; never execute a model-supplied action name.
- [ ] Show the original text and proposed destination; Cancel must cause no write. Append only after confirmation. Add a short operation reference to the filed entry for manual duplicate identification. Do not also put this request into watched Brain Dump: the Shortcut owns this operation and the Mac must not independently replay it.
- [ ] No automatic append retry after an interrupted write. Where supported, check for the exact operation reference before retrying the same request; if reading/checking is unavailable or ambiguous, ask the tester to inspect the destination. Preserve the request ID within a run. A new invocation is a new request, not proven duplicate-safe repetition.
- [ ] Do not ask for permission to overwrite the source Brain Dump; it is outside prototype scope. If execution stops after append but before confirmation, clearly document the uncertain outcome rather than promising an exactly-once action.
- [ ] Test invalid token, expired token, quota, offline before send, service timeout, invalid JSON, background/lock, cancellation, duplicate taps and success with sleeping Mac. Run mobile-contract tests and record device evidence; commit instructions/export only after checking for credentials.

## Task 5 — Product decision from the experiment

**Files:** Create `docs/production/evidence/P04-outcome.md`; Modify roadmap status and `shortcuts/README.md`.
**Consumes:** Installation timing, observed completion rate and user feedback from explicitly authorized testers.
**Produces:** Continue, revise, or stop decision with evidence. This plan authorizes preparation, not contacting testers.

- [ ] Have three consented, Becky-like testers each attempt five synthetic captures; record setup assistance, time to first success, correct destination, duplicate/uncertain outcomes and whether the extra tap is acceptable. Do not claim market validation from this small sample.
- [ ] Continue criterion: no data loss; all cancellation cases write nothing; at least 12/15 tasks completed without developer intervention after setup; every network/auth failure is visible; no provider credentials in shared artifact. If not met, report exact failing behavior and revise/stop before marketing mobile functionality.
- [ ] Separate OS limitations from service bugs and UX friction. Document which actions need the phone unlocked/foreground and whether the exact tested OS can be supported.
- [ ] Revoke prototype tokens after testing, purge response payloads, stop unused staging resources through the approved deployment workflow. Record costs and handoff reusable service contracts to P05.

**Exit gate:** A real device proves or disproves the proposed interaction. A Markdown recipe, mocked API, or successful AI response alone is not a completed Shortcut prototype.
