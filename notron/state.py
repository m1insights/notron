"""What flows along the edges of the graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .requests import RequestEnvelope

from .outbound import Passage


@dataclass
class Write:
    """A proposed change to a note. Nothing is applied until the Guard passes it.

    `markdown` is Markdown in every mode but one: a `restore` carries the exact
    HTML the note held before her last write (what `undo.save` captured), and
    `Executor.restore` puts it back verbatim rather than rendering it again.
    """
    title: str
    markdown: str
    mode: str = "replace"       # "replace" | "append" | "insert" | "mark" | "restore"
    folder: str | None = None   # None -> NOTRON's own folder
    after: int | None = None    # insert mode: put the reply after this block
    anchor: str = ""            # insert mode: the text the reply belongs under,
                                # so it can be found again if the note moved
    rewrite_allowed: bool = False   # replace mode: the user has opted this one
                                    # note into being rewritten in place


@dataclass
class Action:
    """Something Notron wants to do outside Notes. Nothing happens until the Guard
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
    request_id: str = ""
    envelope: RequestEnvelope | None = None
    source_revision: str | None = None
    trigger: str = "manual"          # what woke the graph
    request: str = ""                # what the user actually typed
    about: str = ""                  # 📌 About Me — the standing instructions
    memory: str = ""                 # 🧠 Memory — long-term facts
    lessons: str = ""                # 📖 Lessons — rules she taught herself
    intent: str = ""                 # set by Router
    needs_context: bool = False
    needs_web: bool = False
    here: str = ""                   # the note she was tagged in, if any
    source: str = ""                 # the exact lines she was tagged in, tags intact —
                                     # what the Filer copies and ticks
    reply_to: tuple | None = None    # (title, folder, block index) to answer under
    source_note_id: str | None = None
    source_modified: str = ""
    system_sources: dict[str, Passage] = field(default_factory=dict)
    context: list[Passage] = field(default_factory=list)
    web: list[str] = field(default_factory=list)
    agenda: str = ""                          # today's calendar + open reminders
    actions: list[Action] = field(default_factory=list)
    answer: str = ""
    writes: list[Write] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def note(self, node: str, detail: str = "") -> None:
        self.trace.append(f"{node}: {detail}" if detail else node)
