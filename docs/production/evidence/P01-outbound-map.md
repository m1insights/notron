# P01 Task 2 outbound caller map

Inspected 2026-09-04 on `production/p01-task1`, based on `c3c5941`.
Scope: all production inference, embedding and search inputs. Python 3.11+
application; verification uses the existing Python 3.14.2 environment. No Swift
changes. All evidence uses synthetic data and mocked Apple/provider adapters.

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
| `nodes.researcher` | `research.search` / `search` | Current request with observed note identity when applicable; prepared before Tavily HTTP |

Internal boundary calls: `Brain.ask_json → Brain.ask → Brain._call →
_client.chat.completions.create`; empty-reasoning retry returns through `_call`.
`Brain.embed → _client.embeddings.create` is the only embedding transport.
`research.search → urllib.request.urlopen` is the only search transport.
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

The first audit identifies nine inference callers, two embedding callers, one
search caller and the two SDK transports (plus comments mentioning Brain).
The wider audit also finds the `ask_json` wrapper, SDK initialization and existing
citation HEAD checks. `Brain.available_models` sends no user passages. Citation
URL validation/HEAD behavior is explicitly P01 Task 4, unchanged here.

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
to recognize all secrets. Current and quarantined caches are still plaintext;
Keychain/encryption, retention and revocation/deletion purges belong to Task 3.
No real cache migration was run. URL restrictions belong to Task 4; durable
operation recovery belongs to P02. No listener, app, provider call or deployment
was started. No production or managed-server privacy/release gate is claimed.
