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
        on_node: Callable[[str, State], None] | None = None) -> State:
    """Walk the graph once.

    `reply_to` is (note title, folder, block index) when the answer belongs
    underneath something specific rather than at the end of the Ask note.
    `here` is the note she was tagged in, which is context she gets for free.
    `source` is the exact turn she was tagged in, tags intact — what the Filer
    copies and ticks.
    """
    from . import policy, requests
    policy.require_ready()
    envelope = requests.create(request, request_id=request_id,
                               source='mention' if trigger == 'notes' else ('morning' if trigger == 'morning' else 'cli'),
                               note_id=source_note_id or policy.request_note_id(),
                               reply_to=reply_to, here=here, source_text=source,
                               source_modified=source_modified)
    return run_request(envelope, brain=brain, dry_run=dry_run,
                       on_node=on_node, trigger=trigger)


@recovery.serialized
def run_request(envelope, *, brain, dry_run: bool = False,
                on_node: Callable[[str, State], None] | None = None,
                trigger: str | None = None) -> State:
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
                  source_revision=envelope.source_revision)
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
    if not dry_run and not (recovery.claim(record) if resumed else store.claim(envelope.request_id)):
        state.answer = 'This request is already active or needs review.'
        state.note('graph', 'request not claimed')
        return state
    start = 0
    if resumed:
        name, state = resumed
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
