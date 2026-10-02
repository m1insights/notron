import asyncio
from types import SimpleNamespace

import pytest

from notron import mcp_client


def test_missing_sdk_is_a_plain_error(monkeypatch):
    monkeypatch.setattr(mcp_client, "_sdk", lambda: None)
    with pytest.raises(mcp_client.Unavailable, match="pip install 'notron\\[mcp\\]'"):
        mcp_client.list_tools(("npx", "x"), {})


def test_env_is_stripped_to_essentials_plus_granted_secrets(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "leak")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = mcp_client.child_env({"GITHUB_TOKEN": "t"})
    assert env["PATH"] == "/usr/bin" and env["GITHUB_TOKEN"] == "t"
    assert "NEBIUS_API_KEY" not in env


def test_tool_result_text_is_joined_and_capped():
    blocks = [{"type": "text", "text": "a" * 7000}, {"type": "image", "data": "..."}]
    out = mcp_client.result_text(blocks, is_error=False)
    assert out.startswith("a" * 100) and "more characters not shown" in out
    assert "[1 image omitted]" in out


def test_an_error_result_is_marked_and_short():
    out = mcp_client.result_text([{"type": "text", "text": "x" * 1000}], is_error=True)
    assert out.startswith("error: ") and len(out) == len("error: ") + 300


def test_unit_tests_can_never_launch_a_real_server(monkeypatch):
    # The SDK spawns through anyio, not subprocess.Popen, so the conftest
    # subprocess block alone would not stop it. `_sdk` is faked so this holds
    # without the optional extra installed too.
    monkeypatch.setattr(mcp_client, "_sdk", lambda: object())
    with pytest.raises(AssertionError, match="fake"):
        mcp_client.list_tools(("npx", "x"), {})


def _fake_run(session):
    """Stand in for `_run`: hand `work` a fake session, no process, no SDK."""
    def run(argv, secrets, work):
        return asyncio.run(work(session))
    return run


def _tool(name, annotations=None, schema=None, description="d"):
    return SimpleNamespace(name=name, description=description, input_schema=schema,
                           annotations=annotations)


def test_list_tools_reads_every_page_with_wire_names(monkeypatch):
    ToolAnnotations = pytest.importorskip("mcp").types.ToolAnnotations
    pages = {None: SimpleNamespace(tools=[_tool("a", ToolAnnotations(read_only_hint=True),
                                                {"type": "object", "properties": {}})],
                                   next_cursor="p2"),
             "p2": SimpleNamespace(tools=[_tool("b", description=None)], next_cursor=None)}

    class Session:
        async def list_tools(self, *, params=None):
            return pages[params.cursor if params else None]

    monkeypatch.setattr(mcp_client, "_run", _fake_run(Session()))
    tools = mcp_client.list_tools(("srv",), {})
    assert tools == [
        {"name": "a", "description": "d", "inputSchema": {"type": "object", "properties": {}},
         "annotations": {"readOnlyHint": True}},
        {"name": "b", "description": "", "inputSchema": {"type": "object"}, "annotations": {}},
    ]


def test_call_tool_returns_capped_untrusted_text(monkeypatch):
    t = pytest.importorskip("mcp").types
    CallToolResult, ImageContent, TextContent = t.CallToolResult, t.ImageContent, t.TextContent
    seen = {}

    class Session:
        async def call_tool(self, name, arguments):
            seen.update(name=name, arguments=arguments)
            return CallToolResult(content=[TextContent(type="text", text="found 2"),
                                           ImageContent(type="image", data="AA==",
                                                        mime_type="image/png")],
                                  is_error=False)

    monkeypatch.setattr(mcp_client, "_run", _fake_run(Session()))
    out = mcp_client.call_tool(("srv",), {}, "search", {"q": "bug"})
    assert seen == {"name": "search", "arguments": {"q": "bug"}}
    assert out == "found 2\n[1 image omitted]"


def test_omitted_content_is_counted_not_listed():
    # 5,000 images must not become 80,000 characters of markers.
    blocks = [{"type": "image"}] * 5000 + [{"type": "audio"}, {"type": "text", "text": "hi"}]
    out = mcp_client.result_text(blocks, is_error=False)
    assert out == "hi\n[5000 image omitted]\n[1 audio omitted]"


def test_a_server_that_never_stops_paging_is_refused_not_truncated(monkeypatch):
    pytest.importorskip("mcp")

    class Session:
        async def list_tools(self, *, params=None):
            return SimpleNamespace(tools=[_tool("t")], next_cursor="more")

    monkeypatch.setattr(mcp_client, "_run", _fake_run(Session()))
    with pytest.raises(mcp_client.Unavailable, match="more tools than Notron reads"):
        mcp_client.list_tools(("srv",), {})


def test_server_stderr_never_reaches_notrons_terminal(monkeypatch):
    # The real `_run`, with the SDK's transport and session faked: whatever the
    # server writes to stderr (it can echo a token) goes to /dev/null.
    pytest.importorskip("mcp")
    import contextlib
    import os
    import mcp
    import mcp.client.stdio as stdio
    real_run = mcp_client._run.original  # conftest blocks `_run`; this is the real one

    seen = {}

    @contextlib.asynccontextmanager
    async def fake_stdio(params, errlog=None):
        seen["errlog"] = errlog.name if errlog else None
        yield (None, None)

    class FakeSession:
        def __init__(self, r, w): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def initialize(self): pass

    monkeypatch.setattr(stdio, "stdio_client", fake_stdio)
    monkeypatch.setattr(mcp, "ClientSession", FakeSession)

    async def work(session):
        return "ok"

    assert real_run(("srv",), {}, work) == "ok"
    assert seen["errlog"] == os.devnull
