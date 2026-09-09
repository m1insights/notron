# P03 conversation evidence

Implementation date: 2026-09-09. All automated cases use synthetic notes, temporary
encrypted storage, a real request/operation ledger, and fake provider/native adapters.
No personal Notes, Calendar, Reminders, credentials, or live model endpoint were used.

## Behavior and acceptance coverage

| Case | Production path exercised | Evidence |
|---|---|---|
| Dark matter/dark energy → “Can you dumb it down a bit for me?” | Notes parser → request → router → writer | `test_conversation_acceptance.py`, `test_followup_routing.py`; preceding explanation reaches both calls, no search |
| Expand the previous answer | Same graph, transform response mode | Conversation acceptance |
| New topic | Standalone user boundary; history empty | Parser and conversation acceptance |
| Question inserted at top | Only source blocks before current question eligible | Parser and conversation acceptance; no later answer in transport |
| Deleted/empty latest answer | Older answer is not substituted for missing referent | Parser and conversation acceptance |
| Ambiguous “other one” | Clarification, no search or action | Follow-up and conversation acceptance |
| New factual follow-up | Resolved query searched; original request retained | Follow-up and conversation acceptance |
| Unknown capture date after backlog | Saved task asks for explicit ISO date; reply fulfills original task | Clarification acceptance |
| Unsupported meeting edit | Fixed create-only explanation; no replacement meeting | Conversation/clarification tests |
| Secret in previous context | P01 preparation redacts history before transport/checkpoint | Follow-up and conversation acceptance |
| Which list? → Work | Exact saved proposal, current stable target, original request linkage | Clarification acceptance |
| Conflict → yes | Exact saved event and conflict fingerprint rechecked | Clarification acceptance |
| Wrong thread / edited source / target changed / permission denied | No external effect | Clarification acceptance |
| Retry after resolution | Same reply identity cannot create twice | Clarification store and acceptance |
| Action reference | Only APPLIED/RECEIPTED action payloads with external IDs qualify | Clarification and follow-up routing tests |
| Long answer / fake signature / lists / tables / malformed markup / ticked receipt | Whole exchanges, hard 3-exchange/8,000-character cap, no text-derived trusted IDs | Conversation history fixtures |
| Tagged note local context | Current thought retained, completed exchanges and later questions excluded | Conversation acceptance |
| Source changes during context loading | Admission stops before inference | Follow-up routing regression |
| Restart | Typed history restored from encrypted checkpoints | Follow-up routing recovery regression |

## Verification record

- Baseline on Python 3.14.2: **1,190 tests passed**.
- Initial Python 3.11.14 run exposed 26 pre-existing CLI-import failures from a
  backslash inside an f-string expression in `notron/cli.py:140`; no P03 fix was
  folded into that unrelated compatibility issue. The project runtime is 3.14.2.
- New parser, history transport, source-race, title/receipt/deletion and durable
  clarification tests were observed failing before their implementations/fixes.
- First whole-suite P03 run: 10 failures, 1,234 passes. Root cause was changed
  ordering of existing delayed-date checks and native target resolution. These
  failures were passed back to implementation before completion.
- Review-fix validation: **105 tests passed** across clarification acceptance,
  clarification persistence, delayed requests and target resolution.
- Final command: `.venv/bin/python -m pytest tests -o addopts='' -q`:
  **1,264 passed in 25.30s**, exit 0.
- Final scoped review: approved; **28 tests passed** covering clarification
  acceptance and follow-up routing. Legacy tagged-request compatibility also
  passed **130** request/write-race/conversation regression tests.
- `.venv/bin/python -m compileall -q notron` and `git diff --check`: exit 0.

## Model quality versus deterministic correctness

Configured defaults remain Nebius Token Factory:
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` for routing/scheduling and
`nvidia/nemotron-3-super-120b-a12b` for writing. No provider/model was changed.
These tests verify context, routing, redaction, identity, and effects; they do not
measure subjective explanation quality or live endpoint latency. The expected
pilot case is a simpler explanation of the prior dark-matter/dark-energy answer,
not a search for the phrase “dumb it down.” Live synthetic-model and native
Apple/iCloud qualification remain unperformed and must not be inferred from tests.

## Contract decisions

- The graph reconstructs context from the exact observed Notes body after admission
  rather than trusting a watcher cache. Optional contexts obey the same cap; real
  Notes observations always reconstruct their local context from the source.
- Thread identity combines note identity and standalone topic ordinal. Edits to
  earlier boundaries can change it, so action confirmation also checks the original
  visible request, current target snapshot/proposal, expiry, and active reply lease. The original
  source prefix through the request is fingerprinted; edits before it require a
  fresh request. An intervening unrelated question cancels pending consent.
- A follow-up keeps its own durable request identity for receipt and active-lease
  checks. Encrypted action metadata names `origin_request_id`; its operation ID
  includes the original request and clarification ID. This links fulfillment to
  the original proposal without reopening a completed receipt request.
- History does not automatically write Memory. Old assistant statements and pasted
  signatures remain untrusted text. Known IDs come only from verified ledger actions.
