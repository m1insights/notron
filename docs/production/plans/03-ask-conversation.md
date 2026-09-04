# P03 — Ask Notron conversation implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Natural follow-ups work inside Ask Notron without broadening the product into an unlimited chat assistant.
**Architecture:** Parse preceding turns, carry a bounded context through routing and writing, distinguish transformations from new factual questions, and bind action clarifications to durable requests.
**Tech Stack:** Python 3.11+, existing HTML/Markdown parser, pytest; Nebius Nano and Super through P01 preparation.
**Spec:** [Shared design §§3–4](../design.md).
**Dependencies:** P01 outbound privacy and P02 request/operation contracts. Standalone parser tests can begin against synthetic note fixtures first.

## Global constraints

- At most three preceding complete exchanges and 8,000 characters total; a standalone `New topic` starts a new thread.
- Only content above the question is history. Never include later questions/answers.
- Existing About Me and Memory behavior remains separately controlled; conversation does not automatically become permanent memory.
- Tagged notes retain their local context, but cannot grant permissions through model interpretation.
- No follow-up enables unsupported calendar updates, deletion, recurrence or tool execution.

## Task 1 — Parse bounded preceding exchanges

**Files:** Modify `notron/conversation.py`, `notron/notedoc.py`, `tests/test_conversation.py`; Create `tests/fixtures/conversation/` with synthetic HTML cases and `tests/test_conversation_history.py`.
**Consumes:** Existing `Question(text, after)` and Notes body HTML.
**Produces:** `Turn(role, text, request_id=None)`, `ConversationContext(thread_id, turns, action_refs)`, `history_before(body_html, question, *, max_exchanges=3, max_chars=8000) -> list[Turn]`.

- [ ] Add the original failure as a synthetic note; no live user history in fixtures:

```python
from notron import conversation, markup

def test_simplification_has_the_previous_explanation():
    body = markup.render('Ask Notron',
        'What are dark energy and dark matter?\n\n' +
        conversation.turn('Dark matter pulls things together; dark energy relates to expansion.') +
        '\nCan you dumb it down a bit for me?')
    q = conversation.unanswered(body, ignore=('Ask Notron',))[-1]
    history = conversation.history_before(body, q)
    assert [t.role for t in history] == ['user', 'assistant']
    assert 'expansion' in history[-1].text
```

- [ ] Run `.venv/bin/python -m pytest tests/test_conversation_history.py tests/test_conversation.py -q`; confirm the new API/behavior fails before implementation.
- [ ] Reuse block parsing/signature/rule recognition rather than splitting on arbitrary blank lines. Preserve source position. Recognize `New topic` only as a standalone user block, not a quote inside an assistant answer. Exclude standing furniture and ticks/filing receipts.
- [ ] Select newest whole complete exchanges backward until the exchange/character budget is reached; do not emit a truncated assistant turn. If a single exchange is too large, omit it and clarify references rather than fabricate missing context.
- [ ] Add mid-note insertion, deleted prior answer, two identical questions in separate topics, long answer, pasted fake signature, list/table content, tag mode and malformed markup. Pasted signatures may confuse display parsing but must never create trusted action IDs.
- [ ] Run parser tests; commit fixtures and implementation together.

## Task 2 — Carry history to router and writer

**Files:** Modify `notron/state.py`, `notron/graph.py`, `notron/watch.py`, `notron/nodes.py`, `notron/brain.py`, `tests/test_watch.py`, `tests/test_graph.py`; Create `tests/test_followup_routing.py`.
**Consumes:** P02 RequestEnvelope and P01 Passage preparation.
**Produces:** `State.conversation`, `State.resolved_request`, `State.response_mode` in `answer|transform|clarify`; shared prompt assembly from prepared passages. `run_request` optionally accepts `conversation_context` without changing request identity.

- [ ] Spy on the fake Brain router and writer calls. Assert both receive the same prior answer and that current request is distinct from history.
- [ ] Add a deterministic transformation test:

```python
def test_simplify_reuses_context_without_search(followup_graph):
    result = followup_graph.run(
        previous_user='What are dark energy and dark matter?',
        previous_answer='An explanation about the universe.',
        request='Can you dumb it down a bit for me?',
        router_result={'intent': 'question', 'response_mode': 'transform',
                       'needs_context': False, 'needs_web': False})
    assert followup_graph.search_calls == []
    assert 'universe' in followup_graph.writer_input
    assert result.answer
```

Create `followup_graph` in the test file using production graph nodes, P02 temporary ledger, fake Notes and a Brain spy implementing the P01 interfaces. Do not implement separate test routing logic.

- [ ] Include bounded history in routing. Add validated response mode and resolved query fields. Transform a prior answer without the existing forced-world-search fallback; new factual questions can still search. A short unambiguous phrase such as “make that simpler” may take a code path if an immediately preceding answer exists; absent history must clarify.
- [ ] For factual follow-ups, resolve referents using history then search the resolved query, retaining original user wording for receipts and authorization. Do not execute actions extracted solely from an old answer.
- [ ] Writer receives the same bounded prepared history. Distinguish sourced facts from assistant prose; existing answers are not newly verified external sources. Do not rerun completed actions while writing a conversational explanation.
- [ ] Test new unrelated topic, “what about the other one?”, truncated history, invalid router JSON, secret in history and fresh facts after a transformation. Run graph/watch/privacy/research suites; commit.

## Task 3 — Clarification and actual action references

**Files:** Create `notron/clarifications.py`, `tests/test_clarifications.py`; Modify `notron/nodes.py`, `notron/state.py`, `notron/operations.py`, `notron/watch.py`, `tests/test_actions.py`.
**Consumes:** Durable P02 operations and action IDs; request thread ID.
**Produces:** `PendingClarification(id, request_id, thread_id, question, candidate_ids, expires_at, source_revision)`; `resolve_reply(pending, reply, live_revision) -> Resolution`. Clarifications expire after 24 hours or a changed underlying target/proposal; then ask again.

- [ ] Test “remind me tomorrow” after backlog, “which list?”, ambiguous reminder names and unsupported “move that meeting.” A reply “yes” in another thread must not confirm anything.

```python
def test_yes_in_another_thread_is_not_consent(clarification_store):
    c = clarification_store.add(request_id='r1', thread_id='a', candidate_ids=['event1'])
    result = clarification_store.resolve(c.id, reply='yes', thread_id='b')
    assert result.status == 'unmatched'
```

- [ ] Persist a clarification linked to the original request and exact proposal; a reply resolves that request rather than creating a new unrelated scheduling request. Recheck permissions, date, revision and active lease immediately before execution.
- [ ] Pull action references only from APPLIED/RECEIPTED ledger records. A remembered title without an ID does not select a reminder. Do not interpret a past assistant statement as evidence an action succeeded.
- [ ] Expired/ambiguous replies produce one clear question and no side effect. “Move that meeting” explains current create-only capability without creating a replacement meeting.
- [ ] Verify no new permanent Memory writes occur merely because a conversation continues; run action/conversation/recovery suites; commit.

## Task 4 — User guidance and acceptance pack

**Files:** Modify `notron/workspace.py`, `README.md`; Create `docs/production/evidence/P03-conversation-cases.md`, `tests/test_conversation_acceptance.py`.
**Consumes:** Tasks 1–3.
**Produces:** Discoverable help for `New topic`, follow-ups and clearing history; acceptance evidence.

- [ ] Add concise seed/help examples without rewriting existing users' Ask notes. Onboarding copy integration is owned by P06.
- [ ] Run ten synthetic end-to-end conversations: simplification, expansion, new topic, insertion at top, deletion, ambiguous pronoun, new factual query, ambiguous date, unsupported edit, secret in prior context.
- [ ] Run `.venv/bin/python -m pytest tests -q`. If evaluating live models, use synthetic accounts/content and record exact model IDs, prompts, latency and outcomes; never upload real logs as test data.
- [ ] Record deterministic tests separately from subjective model quality. Expected pilot case: dark-matter follow-up actually explains the previous answer more simply, without searching for the definition of “dumb it down.” Commit and hand off.

**Exit gate:** Context is present and bounded; transforms avoid irrelevant searches; unresolved references clarify; action permission and privacy tests remain passing.
