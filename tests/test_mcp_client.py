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
    assert "[image omitted]" in out


def test_an_error_result_is_marked_and_short():
    out = mcp_client.result_text([{"type": "text", "text": "x" * 1000}], is_error=True)
    assert out.startswith("error: ") and len(out) == len("error: ") + 300


def test_unit_tests_can_never_launch_a_real_server():
    # The SDK spawns through anyio, not subprocess.Popen, so the conftest
    # subprocess block alone would not stop it.
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
    ToolAnnotations = pytest.importorskip("mcp_types").ToolAnnotations
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
    t = pytest.importorskip("mcp_types")
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
    assert out == "found 2\n[image omitted]"
