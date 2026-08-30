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
    answer: str = ""
    writes: list[Write] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def note(self, node: str, detail: str = "") -> None:
        self.trace.append(f"{node}: {detail}" if detail else node)
