"""What flows along the edges of the graph."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Write:
    """A proposed change to a note. Nothing is applied until the Guard passes it."""
    title: str
    markdown: str
    mode: str = "replace"       # "replace" | "append" | "insert"
    folder: str | None = None   # None -> JUNO's own folder
    after: int | None = None    # insert mode: put the reply after this block


@dataclass
class Action:
    """Something Juno wants to do outside Notes. Nothing happens until the Guard
    passes it, and the Guard is plain code."""
    kind: str                        # "reminder" | "event"
    op: str                          # reminder: create|complete   event: create
    title: str
    when: str | None = None          # ISO 8601 local: "2026-09-03T09:00" or "2026-09-03"
    ends: str | None = None          # events only
    where: str = ""                  # list name / calendar name
    notes: str = ""
    target_id: str | None = None     # set by the doer for "complete"


@dataclass
class State:
    trigger: str = "manual"          # what woke the graph
    request: str = ""                # what the user actually typed
    about: str = ""                  # 📌 About Me — the standing instructions
    memory: str = ""                 # 🧠 Memory — long-term facts
    intent: str = ""                 # set by Router
    needs_context: bool = False
    needs_web: bool = False
    here: str = ""                   # the note she was tagged in, if any
    reply_to: tuple | None = None    # (title, folder, block index) to answer under
    context: list[str] = field(default_factory=list)
    web: list[str] = field(default_factory=list)
    agenda: str = ""                          # today's calendar + open reminders
    actions: list[Action] = field(default_factory=list)
    answer: str = ""
    writes: list[Write] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def note(self, node: str, detail: str = "") -> None:
        self.trace.append(f"{node}: {detail}" if detail else node)
