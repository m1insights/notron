# Security status and reporting

Notron is a pre-release Python/Swift macOS project, **not production-ready**.
Default protected startup stops until P06 supplies and verifies the signed
Keychain integration. Do not bypass that gate to process real data. No sandbox
isolation, native security verification, or supported production release is claimed.

## Private vulnerability reporting

**Public-release blocker: the owner must choose and verify a private reporting
route.** No approved address or private reporting procedure was found in the
repository or production handoffs as of 2026-09-05. Remote repository reporting
settings have not been verified. No recipient has been contacted and no reporting
channel has been created by this work.

Do not put credentials, personal Notes content, private caches, or sensitive
exploit details in public issues. Once the owner establishes a private route,
this document must name it and the response process before public release.

## Implemented protections

- Local policy fails closed when missing or corrupt. Home, Read only and Ignore
  remain distinct; zero homes grants no filing access. A scoped tagged reply is
  limited to its readable source note and current request.
- Inference, embeddings and search use provenance-tagged, policy-checked inputs
  and supported secret redaction. Model responses cannot directly mint permissions
  or add operation types. Calendar creation and reminder creation/completion are
  fixed operations checked by the Guard/executor.
- AppleScript and dynamic JXA values travel as arguments to fixed scripts. Model
  output is never evaluated as Python, JavaScript, AppleScript or shell source.
  Launchd configuration serializes argument arrays. This is application boundary
  enforcement, not an operating-system sandbox.
- Sensitive local caches use authenticated AES-GCM encryption with an injected
  Keychain credential contract, explicit offline migration and retention rules.
  There is no plaintext or environment-credential fallback. Signed provisioning
  and startup are still blocked on P06.
- Provider HTTP uses fixed HTTPS destinations/routes, validated and pinned DNS
  addresses, hostname-verified TLS and redirect rejection. Development endpoints
  require a separate injected credential. Citation checking makes no requests.

## Limits and future work

- **Malicious content and model instructions:** prompts and model output are
  untrusted. They can be misleading and can influence answers and parameters of
  supported actions. Text signatures are not authenticated authorship. The
  restrictions above do not prove that a model understood the user's intent.
- **Compromised local account/software:** code, environment, local policy and
  in-memory data can be altered or inspected by sufficiently privileged software.
  Encryption does not protect an unlocked process or replace device security.
  Apple data and iCloud remain governed by Apple and the user's account.
- **Accidental disclosure:** secret redaction is imperfect. Supported patterns,
  policy exclusion and minimization reduce exposure; they cannot identify all
  secrets. Notes audit entries and process arguments can contain personal text.
  Filesystem unlink does not guarantee erasure from snapshots or backups.
- **Provider retention:** Nebius and Tavily retention/training behavior has not
  been verified for the intended account configuration. Do not promise zero
  provider retention. Approved content leaves the Mac when those services are used.
- **User edit loss:** Apple Notes writes replace a whole body and are non-atomic.
  Existing append/insert/mark rechecks reduce some stale writes; replace/restore,
  multi-device races, retry ambiguity and undo recovery need P02. Notron cannot
  promise lossless rich-content preservation or exactly-once external writes.
- **Supply chain and updates:** locked Python versions and a dated advisory audit
  are partial evidence. Signed builds, notarization, secure update/rollback and
  revocation remain P06/P07 gates. A clean advisory result does not rule out
  malicious packages, unpublished vulnerabilities or compromised distribution.
- **Hosted tenant isolation:** there is no implemented hosted multi-tenant service.
  Authentication, per-account authorization, worker leases, billing isolation and
  hosted retention are future P05 work, not current protections.

See the [security evidence and dependency audit](docs/production/evidence/P01-security-boundaries.md),
[complete outbound/subprocess inventory](docs/production/evidence/P01-outbound-map.md),
and [production roadmap](docs/production/README.md). Synthetic passing tests do
not authorize a pilot, public release, or real-data processing.
