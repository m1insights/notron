# P01 outbound caller, HTTP and subprocess map

Updated 2026-09-05 on `production/p01-task1`, Task 4 following `35aa233`.
Scope: every production inference, embedding, model-listing and search transport.
Python 3.11+ application; Python 3.14.2 tests, OpenAI SDK 3.7.0, httpx2 2.12.0,
stdlib HTTP/TLS. The Swift credential name allowlist gains the dedicated development
account; no native integration is enabled. See the Task 4 evidence below.

## Enforcement contract

`notron.outbound.Passage` carries origin, optional note ID, original title and
modified timestamp. `prepare_outbound(purpose, passages)` uses the current local
`policy.PolicySnapshot` and the existing `policy.PolicyError`. Every batch is
validated before the first transport call. Missing/corrupt policy, unknown
purpose/origin, raw strings, missing note IDs and denied notes fail closed without
echoing rejected text. Note-backed requests also recheck their source ID. Titles
and timestamps retain Task 1's sensitive-title exclusions and new-note cutoff.

`standing`, `memory`, and `lesson` require their registered system-note roles;
`note` and `history` require readable IDs. `user_request`, `web`, `agenda`, `model`
and `diagnostic` remain untrusted data. The last two origins explicitly identify
reflection proposals and measured application facts/date/structural headings;
neither implies permission. Any supplied note ID is checked regardless of origin.

Brain's public `ask`/`ask_json` require tagged passages and an explicit purpose;
`embed` accepts only tagged passages and fixes purpose to `embed`. Static system
instructions are separate code constants. `_call` checks and redacts immediately
before inference, including a retry. Embedding validates the entire input and
rechecks each transport batch. Search prepares immediately before constructing
its HTTP request. No production raw-string compatibility path remains.

## Complete production callers

| Caller | Boundary / purpose | Provenance retained |
|---|---|---|
| `nodes.router` | `Brain.ask_json` / `route` | Current user request; watcher note ID/title/timestamp when note-backed |
| `nodes.scheduler` | `Brain.ask_json` / `schedule` | Current request and application date only; no retrieved instructions, standing lessons, or model answers can originate an action |
| `nodes.organizer` | `Brain.ask` / `organize` | Actual note with stable ID plus separately tagged standing context/request |
| `nodes.planner` | `Brain.ask` / `write` | `_prompt`: standing, memory, lessons, web, tagged note, retrieved notes, agenda and request kept separate |
| `nodes.writer` | `Brain.ask` / `write` | Same typed `_prompt`; model reply is prose, never parsed into operations or policy |
| `filer.classify` | `Brain.ask_json` / `organize` | Candidate titles/glimpses carry IDs; each source item carries ID/title/timestamp. Redact before numbering; preserve run boundaries. Resolve unambiguous sanitized labels locally, refuse collisions |
| `reflect.run` proposer | `Brain.ask_json` / `reflect` | Ask transcript as `history` with Ask ID; existing lessons with registered Lessons ID |
| `reflect.run` verifier | `Brain.ask_json` / `reflect` | About Me `standing`, existing `lesson`, originating `history`, and generated candidates `model`; history remains attached for rechecking |
| `care.compose` | `Brain.ask` / `write` | Measured upkeep facts as `diagnostic`; no note bodies in these aggregate facts |
| `index.build` | `Brain.embed` / `embed` | Entire note is prepared before chunking. Every chunk retains ID/title/timestamp. Metadata title/folder is redacted before saving |
| `index.search` | `Brain.embed` / `embed` | Typed request passed intact from retriever; policy checked again by Brain |
| `cli.cmd_models → Brain.available_models` | `GET /v1/models` | No user passages; secure readiness and endpoint-scoped credentials still required |
| `nodes.researcher` | `research.search` / `search` | Current request with observed note identity when applicable; prepared before Tavily HTTP |

Internal boundary calls: `Brain.ask_json → Brain.ask → Brain._call →
_client.chat.completions.create`; empty-reasoning retry returns through `_call`.
`Brain.embed → _client.embeddings.create` is the only embedding transport.
`research.search → network.provider_client → ProviderTransport` is the only search transport.
All three SDK operations also use `ProviderTransport`; no default SDK HTTP transport remains.
Nebius remains the inference/embedding provider: Nano
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, Super
`nvidia/nemotron-3-super-120b-a12b`, configured deep tier
`nvidia/Nemotron-3-Ultra-550b-a55b`, embeddings `Qwen/Qwen3-Embedding-8B`.
Existing tier/environment configuration is unchanged; no model availability or
provider behavior is claimed from mocked tests.

## Upstream sources and local indexing

- `watch.Watcher._answer` forwards the observed source ID through `graph.run`
  and `State`. Tagged-note excerpts are redacted before truncation; request
  capabilities still come only from `policy.explicit_reply`.
- `nodes.watcher` records the actual registered system-note sources in
  `State.system_sources`. A populated standing field without provenance is refused.
- `retrieval.Hit` now preserves note IDs/timestamps. Keyword bodies are prepared
  before excerpting. Semantic chunks preserve IDs into `State.context`.
- `index.build` prepares full bodies before overlapping chunks, embedding and
  persistence. Rechecks readable rows before saving. Its metadata wrapper is
  `{"outbound_version": 1, "notes": {...}}`; pre-provenance caches are excluded
  from search, glimpses and reuse regardless of matching modification times.
  On rebuild, old JSON/NPY files are retained locally as `.unprepared`, never
  used as context or embeddings. Current policy filters cached rows at query time.
- Existing guard/executor decisions remain authoritative. Scheduler rejects
  unsupported kind/operation pairs in code; only explicit user filing words can
  select filing. Model output cannot select filing, undo or organizer routes.

## Reproducible audit commands

Run from `/Users/m1labs/Dev/apps/juno/.worktrees/p01-task1`:

```sh
rg -n 'brain\.(ask|ask_json|embed)|research.search|chat.completions|embeddings.create' notron
rg -n '\.(ask|ask_json|embed)\(|urlopen\(|requests\.|httpx\.|OpenAI\(' notron
/Users/m1labs/Dev/apps/juno/.venv/bin/python -m pytest tests/test_outbound.py tests/test_privacy.py -o addopts='' -q
/Users/m1labs/Dev/apps/juno/.venv/bin/python -m pytest tests/test_outbound.py tests/test_privacy.py tests/test_research.py tests/test_graph.py tests/test_citations.py tests/test_policy.py tests/test_library.py tests/test_filer.py tests/test_reflect.py tests/test_care.py tests/test_nodes.py tests/test_watch.py -o addopts='' -q
/Users/m1labs/Dev/apps/juno/.venv/bin/python -m pytest tests -o addopts='' -q
```

The original Task 2 audit identified nine inference callers, two embedding callers
and one search caller. Task 4 additionally accounts for credentialed model listing
and replaces both former default transports. The current exhaustive HTTP inventory
below supersedes the old citation HEAD exception.

## Evidence and limits

- Baseline: 409 tests passed on the expected Task 1 commits.
- Initial outbound/privacy regressions: 17 failed, 15 passed, exit 1. The ordinary
  approved-note password appeared in the captured embedding request and saved
  cache; missing outbound API and raw embedding/search acceptance were exposed.
- Model-operation regressions: two failed before the router/scheduler restrictions.
- Independent review: grouping and redacted-title regressions failed at captured
  transport before fixes; two additional collision regressions failed before the
  ambiguous-prefix fix. All are retained as tests.
- Full final results and commits are recorded in the
  [Task 2 handoff](../handoffs/2026-09-04-P01-task-2.md).

Redaction uses the existing supported labelled/high-entropy patterns, not a claim
to recognize all secrets. Task 3 subsequently implemented authenticated cache
encryption, explicit offline migration and retention/revocation purges; see
[P01 secure storage](P01-secure-storage.md). No real migration was run.
Task 4 network restrictions are below; durable operation recovery remains P02.
No production or managed-server privacy/release gate is claimed.


## Task 4: every remaining HTTP path

| Entry / actual operation | Destination and wire adapter | Payload / credential boundary |
|---|---|---|
| All nine inference callers above → `Brain._call` → SDK `chat.completions.create` | POST `https://api.tokenfactory.nebius.com/v1/chat/completions` → `ProviderTransport` → `_ProviderConnection.request` | Prepared passages + static system instruction; current `NEBIUS_KEY` rebuilt as Authorization at the adapter |
| `index.build`, `index.search` → `Brain.embed` → SDK `embeddings.create` | POST `https://api.tokenfactory.nebius.com/v1/embeddings` → same adapter | Every batch prepares/rechecks; same endpoint-scoped credential |
| `cli.cmd_models` → `Brain.available_models` → SDK `models.list` | GET `https://api.tokenfactory.nebius.com/v1/models` → same adapter | No user text; same secure readiness and credential checks |
| `nodes.researcher` → `research.search` → `httpx2.Client.post` | POST `https://api.tavily.com/search` → same adapter | Prepared query; injected `SEARCH_KEY` in JSON body; no ambient authentication headers |
| Explicit development Nebius-compatible override | Same three Nebius methods/paths on the single configured public HTTPS host, port 443 | Requires `NOTRON_DEVELOPMENT=1` and `DEV_NEBIUS_KEY`; no production/environment/constructor-key fallback |
| Writer/planner citation grounding | **No transport** | Exact URLs in prepared source/request/note passages; unsupported citations replaced locally |

This is the complete active HTTP inventory, not just the model-input inventory.
No `urllib.request.urlopen`, HEAD, generic fetch, async transport, WebSocket,
Swift `URLSession`, shell curl/wget or alternative provider path remains in the
application source. The plist DOCTYPE URLs in `watch.py`/`daily.py` are literal
identifiers, not fetches. `research.quality` only parses domain text locally.
`credentials.socketpair` is local pipe IPC with the Keychain helper, not HTTP.
No managed-service endpoint is configured yet; P05 must consume these contracts.

All four operations validate scheme, exact host/method/path and port before
connecting. Query strings/fragments/userinfo and redirects are refused. The sole
connection routine checks all DNS results, connects a numeric sockaddr without a
second DNS lookup, checks its peer, then verifies TLS for the original service
name. A private member of a mixed answer rejects the whole connection. Environment
proxies cannot mount a different transport. The adapter reconstructs only fixed
JSON headers and injected authorization, blocking SDK custom/ambient headers and
cookie replay. No redirect can carry the Nebius header or Tavily body elsewhere.
Late storage/credential/policy errors retain a fail-closed PolicyError subtype
through the SDK. Automatic SDK retries are disabled; Brain's existing reasoning
retry and embedding batches still prepare/recheck. Model IDs/settings are unchanged.

Additional audit commands (run in the same worktree):

```sh
rg -n 'available_models|models\.list|chat\.completions|embeddings\.create|research.search' notron
rg -n 'urllib|requests|httpx|httpcore|OpenAI|urlopen|urlretrieve|HTTPSConnection|HTTPConnection|socket|URLSession|curl|wget' notron mac/Sources --glob '*.py' --glob '*.swift'
rg -n 'http|requests|urllib|curl|wget|socket|fetch\(' --glob '*.py' --glob '*.swift' --glob '*.sh' --glob '*.js' --glob '*.ts' --glob '*.html' . -g '!tests/**' -g '!docs/**'
```

Task 4 evidence: initial targeted regressions **105 failed, 20 passed** before
implementation. Subsequent citation, SDK ambient-header, reasoning timeout and
late secure-recheck failures were reproduced before fixes. Final commands/results
are in the [Task 4 handoff](../handoffs/2026-09-05-P01-task-4.md). DNS rebinding,
private/mixed results, original-name TLS, mismatched peer, redirects, proxies,
development isolation, endpoint mutation and zero citation networking exercise
real SDK/HTTP boundaries with fake sockets/DNS/TLS.

Test isolation correction: eight existing search/privacy tests initially retained
the old urllib mock after the transport changed. The socket guard blocked HTTP,
but public DNS resolution of the fixed Tavily domain occurred. No provider HTTP,
citation fetch, real credentials or user content were sent. The fixture was
updated and the suite now globally denies unmocked DNS as well as socket connects.
All final tests used synthetic integrations; no native Apple/Keychain/provider
verification, listener startup, migration, deployment, merge or push occurred.

## Task 5: complete subprocess and auxiliary boundary inventory

Updated 2026-09-05. The four HTTP operations above are unchanged. No additional
application HTTP caller was found. Static plist DOCTYPE identifiers are now emitted
by `plistlib`; they do not trigger a fetch. Dependency/advisory downloads during
this task are tooling traffic, documented separately in the
[security report](P01-security-boundaries.md), not application/provider calls.

| Boundary / every caller | Executable and input separation | Evidence / remaining limit |
|---|---|---|
| `notes.warm_up`, `folders`, `folder_at`, `ensure_folder`, `read_body`, `write_body`, `show_note`, `create_note`; `permissions.check` Notes probe → `applescript.run` → `_osascript` | `['osascript', '-', *args]`; fixed AppleScript stdin, folder/index/ID/body argv; no shell | Real Notes builders and lock wrapper exercised with fake subprocess; metadata fixtures cover listing/resolution. Lock is a script-request lock, not an Apple transaction |
| `calendar.names`, `calendar.window`, `calendar.create`; `reminders.lists`, `open_items`, `create`, `complete`; `permissions._read` → `eventkit.run` → `_osascript` | `['osascript', '-l', 'JavaScript', '-', *args]`; fixed JXA stdin. Dynamic calls use one JSON argv object and `JSON.parse(argv[0])` in a fixed `run` handler. No string interpolation of fields, eval or shell | Scheduler → executor → actual adapters exercised with malicious synthetic model fields and mocked subprocess. Static readers keep final-expression JSON; dynamic handlers return JSON. JXA/ObjC runtime behavior remains native-unverified |
| `credentials.KeychainStore.get/put/delete` → `_request` → `Popen` | Explicit helper path, `--credential-fd`, numeric FD only; name/operation/value JSON over inherited local socketpair; no secret argv, inherited environment or stdout/stderr | Actual pipe protocol exercised with a fake helper process; allowlisted names/Swift dispatch. Helper path/signature validation is P06, not active startup |
| `cli.cmd_schedule`: off bootout; install bootout + bootstrap (three call sites) | `launchctl` argv, fixed label and UID, single plist path argument; structured `ProgramArguments` from `daily.plist` | Fake subprocess/temp home covers both branches. No launchd job loaded |
| `cli.cmd_listen`: off bootout; install bootout + bootstrap (three call sites) | Same fixed argv; `watch.plist` serializes Python path, `-m`, `notron`, `listen` | Fake subprocess/temp home; special-character paths round-trip without extra argv. P06 portable runtime still pending |
| `watch.is_running` | `['launchctl', 'print', 'gui/<uid>/<fixed label>']` | Fake subprocess; proves registration query, not listener health |
| `mac/Sources/Notron/Core.swift: Core.run` → Foundation `Process` | Explicit executableURL; `['-m', 'notron'] + args`, optional JSON stdin; no shell | Read-only native source audit. Callers: AskNotronIntent (ask/quiet/request), Library (scan/peek/open/save), Onboarding (permissions/listen install/status), RewriteDefault (fixed enum default). Development paths/env still mutable and unsigned, gated P06 |
| launchd → Python module | Plist arrays for `morning` / `listen`; no shell wrapper; stdout/stderr `/dev/null` | Parsed hostile-path plist regressions. `credentials.startup` still refuses before protected processing |

There are **10 Python subprocess call sites and one Swift Process boundary**.
No `shell=True`, `os.system`, dynamic Python eval/exec, `do shell script`, JXA
`doShellScript`, NSAppleScript, shell curl/wget or alternate process executor was
found in application source. `scripts/bootstrap.py` and `scripts/demo_guard.py`
call existing Python APIs directly; neither was executed. The Swift Keychain
helper calls Security APIs directly, not subprocesses. Onboarding opens a fixed
`x-apple.systempreferences:` pane through NSWorkspace; it is not an HTTP fetch or
model-selected URL. Swift Package.swift has no external package dependencies.

Argument separation is not sandboxing: process arguments may expose personal text
to local process inspection. `osascript` and `launchctl` use PATH lookup; developer
Python/helper paths are trusted startup configuration. A compromised local account
can replace code, environment or policy. AskNotronIntent currently lacks an end-of-
options `--` before its single request argument, so `--help` can select CLI help;
this does not create shell source or another subcommand, and portable native CLI
invocation should be corrected/tested in P06. No native safety claim is made.

Reproduction (source audit supplements, never substitutes for behavioral tests):

```sh
rg -n 'subprocess|Popen|os.system|popen|exec\(|eval\(|Process\(|executableURL|doShellScript|do shell script|NSAppleScript|NSTask' notron mac scripts --glob '*.py' --glob '*.swift'
rg -n 'run\(|eventkit|Core.run|ProgramArguments' notron/notes.py notron/permissions.py notron/calendar.py notron/reminders.py notron/credentials.py notron/cli.py notron/watch.py notron/daily.py mac/Sources
rg -n 'urllib|requests|httpx|httpcore|OpenAI|urlopen|urlretrieve|HTTPSConnection|HTTPConnection|socket|URLSession|curl|wget' notron mac/Sources scripts --glob '*.py' --glob '*.swift'
```

The new default pytest collection includes `test_security_boundaries.py`.
Global test guards deny unmocked subprocesses, DNS and socket connections; only
explicit synthetic adapters override them. No native applications, credentials,
private cache, live providers or citations were accessed in Task 5.
