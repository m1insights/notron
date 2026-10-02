"""Notron's Apple bridge as an MCP server, over stdio.

Thin on purpose: every tool is one call into `bridge`, which owns the policy.
`ask_notron` runs the normal graph, so Nemotron still decides and the Guard
still authorizes; without --writes it is a dry run.

The only server-side importer of the `mcp` SDK (2.x, where v1's FastMCP is
`mcp.server.mcpserver.MCPServer`), imported lazily so Notron runs without it.

Stdout is the protocol channel. Nothing here prints; every failure becomes a
plain {"error": ...} result rather than an exception, because a client shows a
crashed session as "the tool is broken" and the user cannot tell why.
"""

from __future__ import annotations

import json
import sys

INSTALL = "The MCP server needs the optional extra: pip install 'notron[mcp]'"
PAUSED = "Protected processing paused. Secure storage requires setup or recovery."


class SDKMissing(RuntimeError):
    pass


def _sdk():
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.types import ToolAnnotations
    except ImportError as e:
        raise SDKMissing(INSTALL) from e
    return MCPServer, ToolAnnotations


def _failure(exc: BaseException) -> dict:
    """The words a client sees. Never the exception text for unknown errors:
    it can carry a note title or a path into another provider's logs."""
    from .credentials import CredentialUnavailable
    from .policy import PolicyError
    from .securestore import StorageError
    if isinstance(exc, PolicyError):
        return {"error": f"Notron's note policy is not ready: {exc}"}
    if isinstance(exc, (CredentialUnavailable, StorageError)):
        return {"error": PAUSED}
    return {"error": f"Notron could not do that just now ({type(exc).__name__})."}


def _guard(call) -> dict:
    try:
        return call()
    except Exception as e:  # noqa: BLE001 - see _failure: one bad call must not end the session
        return _failure(e)


def build(*, writes: bool, ask: bool, brain_factory, after_writes=None):
    MCPServer, ToolAnnotations = _sdk()
    from . import bridge

    app = MCPServer("notron", instructions=(
        "Notron's bridge to the user's Apple Notes, Calendar and Reminders on their Mac. "
        "Only notes the user allowed Notron to read are visible; secrets are redacted and "
        "attachments are never returned."))
    read_only = ToolAnnotations(read_only_hint=True)

    @app.tool(annotations=read_only)
    def notes_search(query: str, limit: int = 8) -> dict:
        """Search the user's Apple Notes that they allowed Notron to read (keyword match)."""
        return _guard(lambda: {"notes": bridge.notes_search(query, limit)})

    @app.tool(annotations=read_only)
    def notes_list(limit: int = bridge.MAX_LIMIT) -> dict:
        """List the user's readable Apple Notes, newest first: id, title, folder, modified."""
        return _guard(lambda: {"notes": bridge.notes_list(limit)})

    @app.tool(annotations=read_only)
    def notes_read(note_id: str) -> dict:
        """Read one note's text by id. has_attachments_not_shown means pictures or files
        are in the note but were not returned; do not describe them."""
        return _guard(lambda: bridge.notes_read(note_id))

    @app.tool(annotations=read_only)
    def agenda(days: int = 7) -> dict:
        """Today's calendar, the next `days` (max 31), and open reminders. If Notron
        cannot read Calendar or Reminders it says so; that is not a free day."""
        return _guard(lambda: bridge.agenda(days))

    if ask:
        # destructive_hint False even with writes: every write goes through the
        # Guard, which only adds outside her folder (invariant 2), never deletes
        # a reminder (7) or moves an event (6). In-place rewrites need a per-note
        # opt-in the user typed in Notes, and keep an undo copy.
        @app.tool(annotations=ToolAnnotations(read_only_hint=not writes,
                                              destructive_hint=False if writes else None))
        def ask_notron(request: str) -> dict:
            """Ask Notron (NVIDIA Nemotron). It can answer from notes, calendar and reminders,
            and, if the user enabled writes, file notes or create reminders through its own
            safety checks."""
            def run():
                try:
                    brain = brain_factory()
                except SystemExit:
                    # cli._brain reports on stderr and exits; inside a server that
                    # would end the session, so it becomes an answer instead.
                    return {"error": "Notron could not start NVIDIA Nemotron. "
                                     "Run `notron ask hello` in a terminal to see why."}
                return bridge.ask(request, writes=writes, brain=brain,
                                  after=after_writes if writes else None)
            return _guard(run)

    return app


def serve(**kw) -> None:
    build(**kw).run()   # stdio


def config() -> str:
    """A ready-to-paste Claude Desktop / Cursor block for this interpreter."""
    return json.dumps({"mcpServers": {"notron": {
        "command": sys.executable, "args": ["-m", "notron", "mcp", "serve"]}}}, indent=2)
