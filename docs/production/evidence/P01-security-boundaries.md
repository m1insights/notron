# P01 Task 5 — security inventory and release-facing assertions

Date: 2026-09-05. Branch/worktree: `production/p01-task1`,
`/Users/m1labs/Dev/apps/juno/.worktrees/p01-task1`, starting clean at `1a31b8a`
after Task 4 implementation `5649a71`. Only Task 5 is implemented here.
Python 3.11+ and Swift/macOS; tests use Python 3.14.2 and the root venv.
Nebius/model configuration is unchanged: Nano
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, Super
`nvidia/nemotron-3-super-120b-a12b`, deep
`nvidia/Nemotron-3-Ultra-550b-a55b`; embeddings `Qwen/Qwen3-Embedding-8B`.
Tavily remains search. No AI/provider calls were made.

## Threat model and honest release boundary

Assets: user-authored Apple data, accessible-note policy, scoped request authority,
provider credentials/storage key, prepared context, encrypted cache/undo data,
and integrity of the executable/update chain. Untrusted sources include user and
shared-note text, retrieved passages, search results, model replies, local mutable
configuration, and network/package delivery. The local application code is the
current enforcement authority; a malicious local account can change that code.

| Threat | Implemented protection / evidence | Residual risk and future owner |
|---|---|---|
| Malicious notes, websites, retrieved context or model output | Tasks 1–2 provenance/policy/redaction; model routes cannot select filing/undo/organize; scheduler fixed kind/op pairs and text fields; Guard/executor recheck; fixed AppleScript/JXA source with argv data. New tests use actual Brain parsing, nodes, executor and subprocess construction | Prompt injection can influence prose and allowed action parameters. No promise of semantic intent verification, authenticated note signatures or perfect model obedience. P02/P03 own durable identity and conversation/action integrity; P07 adversarial review |
| Malicious local software or compromised account | User-only files, AES-GCM integrity, injected credential store, no plaintext/environment fallback; default signed startup refuses | Same-user/privileged access can inspect memory/argv, replace Python/helper/PATH, tamper policy, or directly use Apple data. No sandbox/isolation claim. P06 signed identities and entitlements; OS/account hardening outside this application |
| Supply-chain or update compromise | uv.lock package versions/hashes retained; explicitly pinned disposable advisory audit; CI action SHAs and uv version pinned | Advisory scanners do not identify all malicious packages or unpublished flaws. Python runtime, system libraries, build tooling and future updater need shipped-artifact review. P06 signing/notarization/update/rollback/revocation and P07 independent review remain open |
| Hosted tenant crossover | No hosted service implemented; local per-note policy is enforced | No tenant-isolation claim. P05 must bind authentication, ownership, billing, device leases, response caches and deletion to server-verified accounts; P07 tests cross-tenant access |
| Accidental disclosure | Selection before AI body use; prepared inference/embed/search; imperfect redaction; encrypted caches and migration/retention; constrained HTTP/DNS/redirects and development-key isolation; zero-fetch citation grounding | Provider retention/training unverified; local Note audit entries and argv may contain personal text; supported redaction misses unknown formats. Apple/iCloud copies and backups are separate; unlink is not forensic erasure. Provider disclosure before user traffic and P06/P07 native review required |
| User edit loss, replay or ambiguous external write | Existing append/insert/mark preservation and final reread; encrypted one-slot undo; ignored destinations denied; fixed external operations | Apple Notes full-body writes are non-atomic; replace/restore currently lack revision checks. No transaction across Apple apps, no durable operation ledger, retry/undo and rich-content risks remain. P02 owns these fixes; native/multi-device evidence remains P06/P07 |

[SECURITY.md](../../../SECURITY.md) is the release-facing summary. README/CLAUDE
claims about perfect secret exclusion, append-only behavior, complete pre-write
logging and `.env` setup were corrected to match inspected code. The Guard is one
layer, not the whole security model. The P06 gate was not relaxed.

## Boundary fixes and regressions

- **Scheduler schema:** malformed nontext title/location/notes/date fields previously
  crashed or entered Action objects. Reject them before creating an Action; preserve
  existing operation whitelist and extract only existing fields. Extra model keys
  cannot set permissions, capabilities, executable source or write destinations.
- **JXA data separation:** JSON-escaped string interpolation had no demonstrated
  string-breakout exploit, but mixed dynamic values into source and did not satisfy
  the fixed-source contract. Calendar create/window and reminder create/complete
  now send a JSON argument to a fixed handler. Native execution is not verified.
- **launchd serialization:** raw XML interpolation let special-character local
  installation paths corrupt or inject plist elements. `plistlib.dumps` preserves
  executable/work-directory strings and fixed argument boundaries. Paths are local
  configuration, not model-selected; this does not close compromised-account risk.
- **Regression coverage:** hostile model operation pairs are refused at scheduler
  and final executor; forged routing/grant fields leave policy unchanged; model
  writer JSON remains prose in the locally selected note; malicious action fields
  reach argv only. Notes read/write/show/create/folder and every EventKit read/write
  family, launchctl install/off/status, helper name rejection and P06 startup refusal
  use mocked adapters. Existing credential tests exercise the actual socketpair
  request protocol with a fake helper and now check exact executable/FD argv/env.
- **Test isolation:** retain global DNS/socket denial and add subprocess run/Popen
  denial. No obsolete unmocked provider adapter reached DNS in this task.

The [caller map](P01-outbound-map.md#task-5-complete-subprocess-and-auxiliary-boundary-inventory)
accounts for all four actual HTTP operations, 10 Python subprocess sites and the
Swift Process boundary. Native Swift/JXA validation is explicitly deferred; source
inventory supplements the Python behavioral tests, not a claim of native proof.

## Dependency vulnerability audit

**Result: no known advisories returned**, zero skipped packages, zero fixes applied.
This is a dated advisory result, not approval to ship.

- Auditor: **pip-audit 2.10.1**, Python 3.14.2, PyPI advisory service (default).
  [Official tool documentation](https://github.com/pypa/pip-audit/tree/v2.10.1)
  describes requirement-file auditing, `--disable-pip`, `--no-deps`, and limits.
- Disposable environment:
  `/var/folders/nm/r7lx8t8n0sxbtltc3q2c0n200000gn/T/notron-p01-audit-f8q64pny/venv`.
  Installed only the auditor and its dependencies there, with pip cache disabled.
  [Auditor environment snapshot](P01-dependency-audit/tool-environment.txt).
  The shared root test environment and application dependency files were unchanged.
  After copying evidence, the disposable environment/cache were removed.
- Source: **actual uv.lock**, SHA256
  `e622872eb113c52cc273ef825dbc7479c7b9e14ec18bae0059459ca2f7331bc7`.
  `uv lock --check --offline` with uv **0.9.22**: exit 0, 27 records including
  local Notron. All 26 registry package/version pairs were queried, including dev
  and platform-conditional entries. No installed-environment substitution.
- NumPy has two locked versions: 2.4.6 for Python <3.12 and 2.5.2 for >=3.12.
  An initial combined requirements attempt exited 1 for duplicate NumPy requirements;
  it was not counted as an audit. Two conservative union inventories preserve both
  versions and also query platform-only packages even when inactive on this Mac.
- [Python 3.11 input](P01-dependency-audit/py311.txt) and
  [raw result](P01-dependency-audit/py311.json): **25 packages, 0 findings, 0 skipped,
  exit 0**. [Python >=3.12 input](P01-dependency-audit/py312plus.txt) and
  [raw result](P01-dependency-audit/py312plus.json): same counts, exit 0.
- `--no-deps --disable-pip` prevents re-resolving/installing target dependencies.
  Exact versions are already extracted from the lock. The auditor warned that
  fully hashed inputs are encouraged; this run queried advisory metadata only and
  did not download/install the application artifacts. Lock artifact hash verification
  is not claimed. Public PyPI/package/advisory downloads were permitted and used.

Reproduction from the worktree (the original run used the concrete temp path above):

```sh
export AUDIT_DIR=$(mktemp -d -t notron-p01-audit)
/Users/m1labs/Dev/apps/juno/.venv/bin/python -m venv "$AUDIT_DIR/venv"
"$AUDIT_DIR/venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir 'pip-audit==2.10.1'
"$AUDIT_DIR/venv/bin/python" -m pip_audit --version
```

The audited input was generated using the root Python's stdlib `tomllib`:

```python
from pathlib import Path
import tomllib
import os
lock = tomllib.loads(Path('uv.lock').read_text())
rows = sorted({(p['name'], p['version']) for p in lock['package']
               if 'registry' in p['source']})
OUTPUT = Path(os.environ['AUDIT_DIR'])
for version, name in [('2.4.6', 'py311'), ('2.5.2', 'py312plus')]:
    (OUTPUT / f'{name}.txt').write_text(''.join(
        f'{n}=={v}\n' for n, v in rows if n != 'numpy' or v == version))
```

For each generated `py311.txt` / `py312plus.txt`, commands actually run were:

```sh
"$AUDIT_DIR/venv/bin/python" -m pip_audit -r "$AUDIT_DIR/py311.txt" --no-deps --disable-pip --strict --progress-spinner off --cache-dir "$AUDIT_DIR/cache" -f json -o "$AUDIT_DIR/py311.json"
"$AUDIT_DIR/venv/bin/python" -m pip_audit -r "$AUDIT_DIR/py312plus.txt" --no-deps --disable-pip --strict --progress-spinner off --cache-dir "$AUDIT_DIR/cache" -f json -o "$AUDIT_DIR/py312plus.json"
"$AUDIT_DIR/venv/bin/python" -m pip freeze --all
```

Triage: there are no returned vulnerability IDs to assign severity or applicability.
Runtime packages (SDK/HTTP, crypto, NumPy and their transitive dependencies) and
locked pytest/tooling dependencies were all included. Inactive colorama/jsfetch
entries were conservatively queried too. No advisory was suppressed or accepted.
The stale `requirements.txt` is not a complete installation contract and does not
include Task 3 crypto/Task 4 HTTP pins; documentation now directs developers to
`pyproject.toml` + `uv.lock`. Setuptools build requirement remains an unpinned lower
bound outside uv.lock; neither it, the interpreter/system libraries nor the future
signed bundle/update artifacts are covered by this application-lock result. P06/P07
must pin/inventory shipped build artifacts and repeat the audit before release.
Swift Package.swift has no external dependencies or Package.resolved.

## CI and verification

No tracked CI configuration existed at the start. Added
`.github/workflows/tests.yml` for the full synthetic Python suite on macOS/Python
3.12, frozen uv.lock dependencies, uv 0.9.22, read-only repository permissions,
and commit-pinned checkout/setup-python actions. Action tag SHAs were verified
read-only using `git ls-remote` against public GitHub repositories. No workflow was
pushed, dispatched or represented as a hosted CI pass. Ruby YAML parsing succeeded
(exit 0); local pytest is the behavioral evidence.

Initial baseline: **624 passed**. Before production fixes, corrected new regressions
reported **10 failed, 23 passed** (five malformed-field, three JXA-data, two plist
failures). The first draft also had a test typo (`notes.show` rather than
`show_note`), corrected before recording that red run. After the adapter change,
four old tests expected dates inside source; updated them to assert the actual data
arguments. No network/Apple access occurred in these failing runs.

Final test commands, independent review, commits and exact results are recorded in
the [Task 5 handoff](../handoffs/2026-09-05-P01-task-5.md). The new security suite
contains 40 cases; the full Python suite has **664 passing tests**. Independent
review found no blocking issue and reproduced the full pass. Native JXA/Swift
execution remains unverified. Review identified a pre-existing native request
`--help` option-parsing issue, recorded in the subprocess inventory for P06.

## Unresolved release decisions

No approved private vulnerability route was found in README, CLAUDE, production
plans/handoffs or tracked security files. Remote private-report settings were not
inspected; no contact address was inferred from repository ownership. **Owner must
choose, verify and publish a private route before public release.** This is a release
blocker, not a reason to leave independent Task 5 implementation unfinished.

P01 synthetic implementation evidence is complete. M1 still requires P02/P03;
P06 signed Keychain/startup, native permissions, packaging/update chain, provider
retention disclosure and P07 external security review/release decision remain open.
No real processing, migration, Apple/Keychain access, provider/citation request,
listener, helper or launchd startup was performed. No merge, push or publication.
