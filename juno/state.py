"""What flows along the edges of the graph."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Write:
    """A proposed change to a note. Nothing is applied until the Guard passes it."""
    title: str
    markdown: str
    mode: str = "replace"       # "replace" | "append"
    folder: str | None = None   # None -> JUNO's own folder


@dataclass
class State:
    trigger: str = "manual"          # what woke the graph
    request: str = ""                # what the user actually typed
    about: str = ""                  # 📌 About Me — the standing instructions
    memory: str = ""                 # 🧠 Memory — long-term facts
    intent: str = ""                 # set by Router
    needs_context: bool = False
    needs_web: bool = False
    context: list[str] = field(default_factory=list)
    answer: str = ""
    writes: list[Write] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def note(self, node: str, detail: str = "") -> None:
        self.trace.append(f"{node}: {detail}" if detail else node)
