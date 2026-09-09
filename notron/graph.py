"""NOTRON's graph.

Not an agent in a while-loop. A declared network of specialised nodes with
explicit edges, so it is obvious — to you and to a reviewer — exactly what runs,
in what order, and on which model tier.

    watcher ─► router ─► retriever ─► researcher ─► agenda ─► planner ─► scheduler ─► doer ─► filer ─► organizer ─► undoer ─► writer ─► executor
                  │          │             │           │         │           │           │        │         │          │        │          │
                  └──────────┴─────────────┴───────────┘         └───────────┴───────────┴────────┴─────────┴──────────┴────────┴──────────┘
                  (each skipped unless the router asked for it)

Every node may decline: `retriever` no-ops unless the router asked for context,
`planner` only fires on plan intent, `filer` only on file intent, `organizer` and
`undoer` only on their own, and `writer` steps aside for all four. The Guard sits
inside `executor` and is the single choke point for every write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import nodes, recovery
from .state import State

Node = Callable[..., State]


@dataclass(frozen=True)
class Edge:
    frm: str
    to: str


NODES: dict[str, Node] = {
    "watcher": nodes.watcher,
    "router": nodes.router,
    "retriever": nodes.retriever,
    "researcher": nodes.researcher,
    "agenda": nodes.agenda,
    "planner": nodes.planner,
    "scheduler": nodes.scheduler,
    "doer": nodes.doer,
    "filer": nodes.filer,
    "organizer": nodes.organizer,
    "undoer": nodes.undoer,
    "writer": nodes.writer,
    "executor": nodes.executor,
}

EDGES = (
    Edge("watcher", "router"),
    Edge("router", "retriever"),
    Edge("retriever", "researcher"),
    Edge("researcher", "agenda"),
    Edge("agenda", "planner"),
    Edge("planner", "scheduler"),
    Edge("scheduler", "doer"),
    Edge("doer", "filer"),
    Edge("filer", "organizer"),
    Edge("organizer", "undoer"),
    Edge("undoer", "writer"),
    Edge("writer", "executor"),
)

ORDER = ("watcher", "router", "retriever", "researcher", "agenda",
         "planner", "scheduler", "doer", "filer", "organizer", "undoer",
         "writer", "executor")


def run(request: str, *, brain, trigger: str = "manual", dry_run: bool = False,
        reply_to: tuple | None = None, here: str = "", source: str = "",
        source_note_id: str | None = None, source_modified: str = "",
        request_id: str | None = None,
        carried: list | None = None, conversation_context=None,
        on_node: Callable[[str, State], None] | None = None) -> State:
    """Walk the graph once.

    `reply_to` is (note title, folder, block index) when the answer belongs
    underneath something specific rather than at the end of the Ask note.
    `here` is the note she was tagged in, which is context she gets for free.
    `source` is the exact turn she was tagged in, tags intact — what the Filer
    copies and ticks. `carried` is every file hanging off that note, which the body
    cannot tell her about.
    """
    from . import policy, requests
    policy.require_ready()
    envelope = requests.create(request, request_id=request_id,
                               source='mention' if trigger == 'notes' else ('morning' if trigger == 'morning' else 'cli'),
                               note_id=source_note_id or policy.request_note_id(),
                               reply_to=reply_to, here=here, source_text=source,
                               source_modified=source_modified)
    return run_request(envelope, brain=brain, dry_run=dry_run,
                       on_node=on_node, trigger=trigger, carried=carried,
                       conversation_context=conversation_context)


@recovery.serialized
def run_request(envelope, *, brain, dry_run: bool = False,
                on_node: Callable[[str, State], None] | None = None,
                trigger: str | None = None, carried: list | None = None,
                conversation_context=None) -> State:
    """Commit identity and claim admission before the first graph node.

    Existing completed/uncertain requests are never silently replayed. The
    envelope carries provenance, but does not grant an explicit reply capability.
    """
    from . import policy, retention, requests, recovery
    policy.require_ready()
    retention.reconcile()
    store = requests.current()
    envelope = store.capture(envelope)
    state = State(request=envelope.text, request_id=envelope.request_id, envelope=envelope,
                  trigger=trigger or ('notes' if envelope.source in {'ask', 'mention'} else envelope.source),
                  reply_to=envelope.reply_to, here=envelope.here, source=envelope.source_text,
                  source_note_id=envelope.note_id, source_modified=envelope.source_modified,
                  source_revision=envelope.source_revision, carried=list(carried or []))
    record = store.get(envelope.request_id)
    resumed = recovery.latest(envelope, ORDER) if recovery.available(record) else None
    if record.status != 'prepared' and resumed is None:
        state.answer = ('This request was already completed.' if record.status == 'completed'
                        else 'This request needs review before it can run again.')
        state.note('graph', record.status)
        return state
    if resumed is None and not store.validate_source(envelope):
        state.answer = 'The source changed. Waiting for a fresh observation before running.'
        state.note('graph', 'source changed before inference')
        return state
    if resumed is None:
        try:
            state.conversation = _conversation(envelope, conversation_context, state=state)
        except requests.OperationConflict:
            state.answer = 'The source changed. Waiting for a fresh observation before running.'
            state.note('graph', 'source changed while reading history')
            return state
    if not dry_run and not (recovery.claim(record) if resumed else store.claim(envelope.request_id)):
        state.answer = 'This request is already active or needs review.'
        state.note('graph', 'request not claimed')
        return state
    start = 0
    if resumed:
        name, state = resumed
        state.resumed = True
        start = ORDER.index(name) + 1
    try:
        with requests.execution(envelope):
            for name in ORDER[start:]:
                fn = NODES[name]
                if name in ('executor', 'doer', 'filer'):
                    state = fn(state, brain=brain, dry_run=dry_run)
                else:
                    state = fn(state, brain=brain)
                if any(r.startswith('✗') for r in state.results):
                    break
                if not dry_run:
                    if not recovery.checkpoint(state, name):
                        state.results.append('✗ source permission changed; recovery payload not retained')
                        break
                if on_node:
                    on_node(name, state)
                if state.intent == 'ignore' and name == 'router':
                    state.note('graph', 'halted — nothing addressed to Notron')
                    break
    except BaseException:
        if not dry_run:
            store.finish(envelope.request_id, needs_review=True)
        raise
    if not dry_run:
        store.finish(envelope.request_id, needs_review=any(r.startswith('✗') for r in state.results))
    return state


def diagram() -> str:
    return "\n".join(f"  {e.frm} ─► {e.to}" for e in EDGES)


def _conversation(envelope, supplied=None, *, state=None):
    """Reconstruct only preceding history from the exact observed source revision."""
    from dataclasses import replace
    from . import conversation, notes, policy, requests, watch
    from .outbound import Passage, sanitized
    context = supplied
    if envelope.note_id and envelope.reply_to and envelope.source_revision:
        body = notes.read_body(envelope.note_id)
        if requests.revision(body) != envelope.source_revision:
            raise requests.OperationConflict('Conversation source changed.')
        ignore = watch.ASK_FURNITURE if envelope.source == 'ask' else (envelope.reply_to[0],)
        questions = conversation.unanswered(body, ignore=ignore, require_tag=envelope.source == 'mention')
        matches = [q for q in questions if q.after == envelope.reply_to[2]
                   and q.text == (envelope.source_text or envelope.text)]
        if len(matches) != 1:
            raise requests.OperationConflict('Conversation source occurrence changed.')
        if state is not None and envelope.source == 'mention':
            from . import privacy
            state.here = privacy.redact(conversation.local_context_before(body, matches[0], ignore=ignore)[:watch.HERE_CHARS])
        context = conversation.context_before(body, matches[0], note_id=envelope.note_id,
                                               ignore=ignore, require_tag=envelope.source == 'mention')
    if context is None:
        return None
    if not isinstance(context, conversation.ConversationContext) or not envelope.note_id:
        raise policy.PolicyError('Conversation history requires its source note.')
    # Optional API contexts obey the same hard cap and complete-pair contract.
    selected, size = [], 0
    turns = context.turns
    for i in range(len(turns) - 2, -1, -2):
        pair = turns[i:i + 2]
        if [t.role for t in pair] != ['user', 'assistant']:
            break
        count = sum(len(t.text) for t in pair)
        if len(selected) >= 6 or size + count > 8000:
            break
        selected[0:0] = pair
        size += count
    passages = sanitized('route', [Passage(t.text, 'history', envelope.note_id,
                                         envelope.reply_to[0] if envelope.reply_to else '',
                                         envelope.source_modified) for t in selected])
    safe_turns = [replace(t, text=p.text, request_id=None) for t, p in zip(selected, passages)]
    from . import operations
    from .executor import Executor
    ids = requests.current().history_request_ids(envelope.note_id, context.thread_id,
                                                 [t.text for t in safe_turns if t.role == 'user'])
    refs = [op.external_id for op in operations.current().action_references(ids)
            if op.content_source_ids is not None and Executor._content_readable(op.content_source_ids)]
    return conversation.ConversationContext(context.thread_id, safe_turns, refs)
