# P05 managed service coding handoff — 2026-09-09

## Scope and status

The owner asked to complete coding while away from their desk. P04 real-iPhone
validation remains deferred. P05 Tasks 2–6 are locally implemented and task-reviewed; final integration review follows. No remote deployment,
provider payment/inference call, real Notes access, push or signed startup occurred.

Stack: Python, FastAPI/Pydantic, PostgreSQL, Stripe SDK, PyJWT and Swift/SwiftUI.
The existing Nebius model registry is preserved: fast
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, smart
`nvidia/nemotron-3-super-120b-a12b`, deep
`nvidia/Nemotron-3-Ultra-550b-a55b`, vision `openbmb/MiniCPM-V-4_5`, embeddings
`Qwen/Qwen3-Embedding-8B`; Tavily provides search. No model calls ran in tests.

## Implemented contracts

- OIDC browser PKCE/state/nonce sign-in, verified issuer access tokens, server-owned
  identity/device records, Keychain refresh rotation and persistent local sign-out.
- Signed raw Stripe webhooks, authoritative repair, account-bound checkout/portal,
  capped subscription/pilot/top-up grants, expiry/refund/dispute handling.
- Atomic allowance and monthly monetary reservations, fixed inference/vision/
  embedding/search adapters, strict responses, encrypted response retries, stable
  request IDs, conservative uncertain-cost holds and protected native token IPC.
- One managed Mac lease, renewal, monotone fencing and delayed transfer, final
  effect admission checks, local verified receipt/undo recovery, account erasure
  workflow and durable Stripe cancellation including late events.
- Account/IP abuse controls, evidence-based cost reconciliation, encrypted-cache key
  rotation, least-privilege roles, scheduled cleanup/repair, CI and operator runbooks.

Detailed contracts: [billing](../../../service/BILLING.md),
[inference](../../../service/INFERENCE.md), [devices and deletion](../../../service/DEVICES.md),
[operations](../../../service/OPERATIONS.md), and deployment settings in
[deployment-inputs.md](../../../service/deployment-inputs.md).

## Review outcomes

Independent task reviews accepted Tasks 2–6 after correcting persistent sign-out failure
handling, historical refunded-price grants, refreshed-upload privacy checks,
malformed provider replies/usage counts, retention-batch starvation and maintenance
batch validation before side effects. Synthetic
regressions reproduce the failures; no issue was dismissed merely to finish.

Write-path invariants remain: #2 existing-text preservation still uses the original
guards; #3 no model enters execution; #4 recovery exceptions require exact ledger
or snapshot proof; #5 policy/source validation remains enforced; #6 Calendar stays
create-only; #7 supported Reminders operations remain unchanged. A final Apple/
iCloud race is still nontransactional; lease transfer cannot recall an issued write.

## External gates and decisions

- Approved host/region/domain, issuer/client/callback profile and real signed
  browser/Keychain/helper round trip. P06 startup remains deliberately closed.
- Real Stripe test-clock lifecycle/cancellation, reviewed prices/allowances/top-up
  expiry, explicit financial retention and provider cost conversion/rates.
- Actual provider retention and maximum image/token accounting, container/TLS
  deployment, restore rehearsal, alerts, independent external review and P07 release.
- Identity-provider erasure requires the selected vendor's process. Its confirmation
  remains an operator attestation, not an invented generic OIDC API. Minimal permanent
  identity hashes are pseudonymous non-resurrection records and need disclosure.
- Unknown costs remain held until evidence-based operator reconciliation; deleting
  an account cannot erase current-month spend. No automatic overage or refund.

## Integration

Base: `11216be` (P05 Task 1). Task 2: `ee87a51`, `b6ba022`, `1f41b9f`.
Task 3: `32d1780`, `c8672d2`. Task 4: `30fca91`, `3175d20`.
Task 5: `08bdf83`, `441113f`. Task 6: `d6aa1ac`, `02e8067`, `93cfd98`.
Final integration verification is recorded in [P05 evidence](../evidence/P05-staging.md). Unrelated main-checkout metadata and untracked
user files are preserved. No push is authorized by this coding handoff.

## Implementation decisions

The approved roadmap and away-from-desk authorization cover all local P05 coding
and the repository workflow covers local integration. Deployment, billing and
signed activation remain separate gates. If this scope interpretation is wrong,
the local commits are reversible and no external service was changed.

- Native access tokens use an inherited duplex socket; refresh credentials remain
  in Keychain. The P06 startup gate stays closed. A different packaging contract
  would require revising that bridge before activation.
- Financial retention is an explicit deployment input, not an invented seven-year
  default; account content is purged immediately. An incorrect selected period
  would require policy/configuration correction before launch.
- Deletion has durable Stripe cancellation; actual identity-vendor erasure remains
  an operator-confirmed external step. Choosing an issuer adds its erasure procedure.
