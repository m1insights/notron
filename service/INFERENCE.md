# Managed inference contract — P05 Task 4

Python/FastAPI/PostgreSQL; native Swift session bridge; desktop Python. No live
provider, identity, Stripe, Notes or cloud calls were used for verification.
This implementation does not activate the P06 signed startup gate.

## Configuration

Alongside complete billing configuration, all four inputs are required together:

- `NOTRON_SERVICE_PROVIDER_RATES`: JSON `{ "version": <reviewed version>,
  "vision_input_tokens": <verified maximum full vision input context>,
  "rates": { <operation:tier>: {"input": <integer>, "output": <integer>,
  "fixed": <integer>} } }`. All rates are positive integer **micro-USD per
  token**, plus fixed micro-USD per request. Required entries: `infer:fast`,
  `infer:smart`, `infer:deep`, `embed:fast`, `vision:fast`, `search:fast`.
  Vision's positive operator-reviewed input-token ceiling is mandatory because
  compressed image size is not a token bound. No production rate or bound is
  supplied by this repository. Search uses `fixed` for basic and twice `fixed`
  for advanced; operator must cover the provider's maximum charged search cost.
- `NOTRON_SERVICE_UNITS_PER_MICRO_USD`: positive integer conversion into Task 3's
  dimensionless allowance units. It is not a dollar allowance. Settlement keeps
  the original reservation conversion across configuration changes.
- `NOTRON_SERVICE_NEBIUS_KEY`, `NOTRON_SERVICE_TAVILY_KEY`: deployment credentials.
  They never travel to the desktop. Secret settings are excluded from repr.

Absent configuration disables paid inference; partial/invalid configuration stops
startup. `MONTHLY_SPEND_CEILING` dollars separately caps aggregate UTC-calendar-
month committed and reserved provider costs, converted to micro-USD. No automatic
overage or automatically minted grants exist. P07 must approve monetary/rate
conversion and actual model token accounting before traffic.

Fixed registry: Nebius fast `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, smart
`nvidia/nemotron-3-super-120b-a12b`, deep `nvidia/Nemotron-3-Ultra-550b-a55b`,
vision `openbmb/MiniCPM-V-4_5`, embeddings `Qwen/Qwen3-Embedding-8B`; search Tavily.
No body model ID or destination URL is accepted.

## HTTP and accounting

Authenticated `POST /v1/infer`, `/v1/embed`, `/v1/vision`, `/v1/search` consume:
`request_id` UUID, positive `lease_fence`, and 1–64 prepared `passages` with text,
origin and note provenance. Optional fields are `tier` (fast/smart/deep; non-infer
requires fast), `system`, `max_tokens` (1–16384 including reasoning), `json_mode`,
`temperature` (0–1), `image` base64, `mime`, `limit` (1–5), `depth` basic/advanced.
Unknown fields fail schema validation. Text input is bounded to 131072 UTF-8 bytes;
images to 512000 decoded bytes and approved MIME/signature with note provenance.
Response shapes: `{content, reasoning: bool}`, `{embeddings}`, or
`{answer, results:[{title,url,content}]}`. Results remain untrusted data.

Text is redacted again with the exact desktop `notron/privacy.py` source. The
wheel contains this shared file. Docker builds from repository root using
`docker build -f service/Dockerfile .`; root `.dockerignore` only admits service
source/locks/migrations and that privacy file.

`Usage.reserve(principal, request_id, digest, ceiling_micro_usd,
lease_fence=..., rate_version=...)` returns a reservation ID and optional cached
response. `settle(principal, reservation_id, actual_micro_usd, response)` changes
allocation/usage/currency/cache in one transaction. `uncertain(...)` holds all
capacity. No provider-side retries run: a missing/invalid usage record, timeout,
malformed response, interruption or excess settlement remains uncertain. Completion
tokens include reasoning; inconsistent separately reported reasoning is rejected.
Reservations use UTF-8 bytes plus framing allowance for input, the full output
budget, and the configured full vision-input budget.

Lock order is account → existing usage reservation (when present) → global
monetary-budget advisory lock → new reservation and billing allocations. Every caller of currency mutations uses the
same account-then-global order. Fresh entitlement is refreshed before reservation;
`reserve_locked` rechecks authoritative billing freshness. Account/device/fence/
lease expiry are rechecked just before provider admission. Per-account reserved
and uncertain calls are capped at two; the default HTTP adapter has eight bounded
in-flight slots and a 25-second absolute caller deadline. Late blocked HTTP work
keeps its slot and unknown cost remains held; no unlimited retry pool grows.

Same account + request ID + identical payload replays encrypted output without a
provider call. Different payload under an old ID returns 409 `request_conflict`.
Cross-account IDs never reveal another account's result. Active ownership is
required for replay, but replay does not require a renewed subscription. Cache
expires after 24 hours. `Usage.purge_expired()` deletes ciphertext; durable usage
identity remains, so expiry never executes old paid work again. The cache uses
AES-GCM with account, reservation and digest bound as authenticated context.
No prompt body is stored. Deletion/revocation cannot be undone by a late cache
write. Task 5 must schedule expiry purge and account ciphertext deletion.

Visible desktop errors: `signin_required`, `subscription_required`,
`allowance_exhausted`, `provider_unavailable`, `permission_required`,
`outcome_uncertain`, plus `request_conflict`. They propagate as policy failures;
local pending/review state and receipt recovery are preserved, never empty answers.

## Desktop/native seam

`Brain.transport` accepts `ManagedTransport`; otherwise `DirectTransport` retains
user-Keychain Nebius behavior. `Brain.from_credentials` and research use the
protected configured managed transport without loading company/provider keys.
Existing preparation and live vision source revision/policy checks remain on
every call. `ManagedTransport` permits only four fixed routes on the injected
native service origin, pinned public DNS addresses, TLS and no redirects/proxies.

P02 `active_request()` derives deterministic child UUIDs from parent + operation
+ exact payload. Without P02, an encrypted, file-locked bounded ledger keeps one
ID per payload across restarts. It never expires an uncertain identity into a new
purchase; cache expiry requires explicit new request context/review. Its 10000-ID
limit fails closed instead of deleting unresolved IDs. This intentionally favors
no duplicate purchase over transparently refreshing old unscoped work.

`NativeTokenChannel` uses an inherited dedicated duplex Unix socket. Native
`ManagedIPCSession` accepts only access-token requests with a Boolean refresh flag
or non-secret configuration. It calls `AccountSession.accessToken(forceRefresh:)`;
refresh tokens remain native Keychain-only. One HTTP 401 gets one forced refresh
with identical paid request ID. Native sign-out notification closes all channels
and stops attached children; late token results are refused. Local Apple writes
already issued cannot be recalled and remain subject to P02 uncertainty recovery.

Core.run has complete FD0 socket attachment when no command stdin is used, but
`Core.protectedManagedStartupValidated` stays **false**. Existing identity bundle
gate and Python `credentials.startup()` also stay closed. P06 must verify the
signed app/runtime/helper and inject KeychainStore before enabling these gates;
the environment only marks descriptor 0, never contains token/URL and cannot
satisfy protected startup. Main-thread managed subprocess waits are refused.
Commands needing stdin remain local and do not receive the token channel.

Task 5 supplies `notron.transport.configure_lease_fence(callable)` returning the
current server-issued fence; its default zero refuses admission. Server checks
existing `worker_leases(account_id,device_id,fence,expires_at)` under account lock.
Task 5 adds lease endpoints/renewal/transfer/scheduling in migration 006.

## Remaining release gates

No staging or production-readiness claim. Required: reviewed real provider rates,
retention and maximum vision/token accounting; issuer/Keychain/signed socket roundtrip;
Task 5 leases/deletion/periodic cache purge; TLS deployment and edge per-IP/account
rate limiting; restore/backup retention and key rotation procedure; P06/P07 approval.
Aggregate provider spend and account concurrency are enforced locally in code;
edge abuse controls remain a deployment gate, not a claimed implemented feature.
