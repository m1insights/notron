"""Talking to an MCP server over stdio. The only client-side importer of the SDK.

One process per call, launched from an argv the user typed at the terminal,
with a stripped environment plus exactly the secrets that server was granted.
Everything that comes back is untrusted text for the model, capped at
`MAX_OUTPUT`.

Both entry points are synchronous on purpose: the graph is synchronous and
`brain._deadline_guard` needs the main thread, so the async SDK runs inside its
own short-lived event loop here and nowhere else.

Written against `mcp==2.2.0`. The 2.x types are snake_case in Python
(`input_schema`, `read_only_hint`, `is_error`, `next_cursor`) and camelCase on
the wire; what this module hands back uses the wire names, because that is what
a server's own documentation and the connector registry talk about.
"""

from __future__ import annotations

import os

#: Characters of one tool's output the model is shown. A busy log or PR list is
#: long; the tail of it is rarely what anyone asked about.
MAX_OUTPUT = 6000
TIMEOUT = 30
KEEP_ENV = ("PATH", "HOME", "USER", "LANG", "TMPDIR")
# A server that pages its tool list forever must not hold a call open forever.
MAX_PAGES = 10


class Unavailable(RuntimeError):
    pass


def _sdk():
    try:
        import mcp  # noqa: F401
        return mcp
    except ImportError:
        return None


def child_env(secrets: dict[str, str], argv=()) -> dict[str, str]:
    """The server's whole environment: essentials plus its own granted secrets.

    The SDK also merges its own short safe list (PATH, HOME, USER, SHELL, TERM,
    LOGNAME on macOS) under this. Nothing else from Notron's environment, such as
    the Nebius key, ever reaches a third-party process.

    A server registered by absolute path gets its own folder first on PATH:
    `npx` is `#!/usr/bin/env node`, and a launchd listener's PATH has never
    heard of the folder node lives in. It is the folder of the binary the user
    already approved, so it adds nothing they did not choose.
    """
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    if argv and os.path.isabs(argv[0]):
        env["PATH"] = os.pathsep.join(p for p in (os.path.dirname(argv[0]), env.get("PATH")) if p)
    env.update(secrets)
    return env


def result_text(blocks: list[dict], *, is_error: bool) -> str:
    """The text a server sent, capped, followed by a line naming each thing it was
    not shown.

    The omitted markers go after the cap, never inside it: a long text block must
    not push "[image omitted]" off the end, or the model reads the result as if it
    had seen everything the server sent (CLAUDE.md invariant 12). They are counted
    per type, not listed one by one, so 5,000 images stay one short line.
    """
    text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    counts: dict[str, int] = {}
    for b in blocks:
        if b.get("type") != "text":
            kind = str(b.get("type") or "content")[:20]
            counts[kind] = counts.get(kind, 0) + 1
    omitted = [f"[{n} {kind} omitted]" for kind, n in counts.items()]
    if is_error:
        text = "error: " + (text or "(no message)")[:300]
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n[… {len(text) - MAX_OUTPUT} more characters not shown]"
    return "\n".join(p for p in [text, *omitted] if p) or "(nothing)"


def _require_sdk() -> None:
    # Checked before `_run`, not inside it: `_run` is the seam tests replace, and
    # a missing extra must read as plain words whatever stands in for it.
    if _sdk() is None:
        raise Unavailable("MCP support is not installed: pip install 'notron[mcp]'")


def _run(argv, secrets, work):
    """Open a session, run `work(session)` and close it. Tests replace this."""
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=argv[0], args=list(argv[1:]), env=child_env(secrets, argv))

    async def main():
        # A third-party server's stderr can echo the token it was handed; it
        # never reaches Notron's terminal or logs.
        with open(os.devnull, "w") as quiet, anyio.fail_after(TIMEOUT):
            async with stdio_client(params, errlog=quiet) as (r, w), \
                    ClientSession(r, w) as session:
                await session.initialize()
                return await work(session)
    return anyio.run(main)


def list_tools(argv, secrets) -> list[dict]:
    _require_sdk()

    async def work(session):
        tools, cursor = [], None
        for _ in range(MAX_PAGES):
            if cursor is None:
                res = await session.list_tools()
            else:
                from mcp.types import PaginatedRequestParams
                res = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
            tools += res.tools
            cursor = res.next_cursor
            if not cursor:
                break
        else:
            # A partial list must not reach the approval screen looking complete.
            raise Unavailable("This server lists more tools than Notron reads, so none were shown.")
        return [{"name": t.name, "description": t.description or "",
                 "inputSchema": t.input_schema or {"type": "object"},
                 # by_alias: `readOnlyHint`, the name the approval digest pins.
                 "annotations": (t.annotations.model_dump(exclude_none=True, by_alias=True)
                                 if t.annotations else {})}
                for t in tools]
    return _run(argv, secrets, work)


def call_tool(argv, secrets, name: str, arguments: dict) -> str:
    _require_sdk()

    async def work(session):
        res = await session.call_tool(name, arguments)
        blocks = [c.model_dump() for c in res.content]
        return result_text(blocks, is_error=bool(res.is_error))
    return _run(argv, secrets, work)
