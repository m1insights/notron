"""JUNO's graph.

Not an agent in a while-loop. A declared network of specialised nodes with
explicit edges, so it is obvious — to you and to a reviewer — exactly what runs,
in what order, and on which model tier.

    watcher ─► router ─► retriever ─► researcher ─► planner ─► writer ─► executor
                  │          │             │           │         │          │
                  └──────────┴─────────────┘           └─────────┴──────────┘
                  (each skipped unless the router asked for it)

Every node may decline: `retriever` no-ops unless the router asked for context,
`planner` only fires on plan intent, `writer` steps aside for plans. The Guard
sits inside `executor` and is the single choke point for every write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import nodes
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
    "planner": nodes.planner,
    "writer": nodes.writer,
    "executor": nodes.executor,
}

EDGES = (
    Edge("watcher", "router"),
    Edge("router", "retriever"),
    Edge("retriever", "researcher"),
    Edge("researcher", "planner"),
    Edge("planner", "writer"),
    Edge("writer", "executor"),
)

ORDER = ("watcher", "router", "retriever", "researcher", "planner", "writer", "executor")


def run(request: str, *, brain, trigger: str = "manual", dry_run: bool = False,
        reply_to: tuple | None = None, here: str = "",
        on_node: Callable[[str, State], None] | None = None) -> State:
    """Walk the graph once.

    `reply_to` is (note title, folder, block index) when the answer belongs
    underneath something specific rather than at the end of the Ask note.
    `here` is the note she was tagged in, which is context she gets for free.
    """
    state = State(request=request, trigger=trigger, reply_to=reply_to, here=here)
    for name in ORDER:
        fn = NODES[name]
        if name == "executor":
            state = fn(state, brain=brain, dry_run=dry_run)
        else:
            state = fn(state, brain=brain)
        if on_node:
            on_node(name, state)
        if state.intent == "ignore" and name == "router":
            state.note("graph", "halted — nothing addressed to Juno")
            break
    return state


def diagram() -> str:
    return "\n".join(f"  {e.frm} ─► {e.to}" for e in EDGES)
