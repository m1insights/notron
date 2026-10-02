# MCP Connectors + Apple Bridge Server Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Notron extensible through the industry standard (MCP) in both directions:
**(A)** Notron can use any MCP server as a tool inside a project channel, with Nemotron choosing
the tool and its arguments, and **(B)** Notron's Apple bridge (Notes, Calendar, Reminders, and
"ask Notron") runs as an MCP server any MCP client can use (Claude Desktop, Cursor, and others).

**Why:** Adoption. A custom plugin protocol (R04 Task 1, `protocol-v1`) asks the world to write new
code for us. MCP already has thousands of servers and every major AI client speaks it. Part A gives
Notron those servers on day one. Part B puts the defensible part, the Apple bridge, in front of
every MCP user, and that is the shareable "connect Siri AI to the world" story.

**Architecture:**
- **A, connectors (client):** a user-owned registry (`connectors.json`) of MCP servers launched over
  stdio from an argv the user typed at the terminal. Each server's tools are listed once, shown to
  the user and approved one by one. The approval pins a digest of the tool's name, description, schema
  and annotations, so a server that changes a tool after approval loses it until the user approves
  it again. A channel grants connectors by name (`channel set X --connect github`). In the `project`
  node Nemotron Super picks `calls: [{tool, arguments}]`. Code validates every call against the
  channel grant and the pinned schema, redacts the arguments, runs the call and appends the output to
  `state.tools`. That is the existing untrusted-tool path, so outbound policy already covers it.
- **v1 is read-only.** Only tools the server annotates `readOnlyHint: true` *and* the user approves
  can run. Write-capable tools are refused at approval time with a plain message. Writes come later
  through the existing id-bound Approve flow; that work is not in this plan.
- **B, Apple bridge server:** `notron mcp serve` is a stdio MCP server. Its read tools go through
  `library` and `prepare_outbound` exactly as a model call does: an ignored note does not exist,
  secrets are redacted, and nothing ever returns attachments. `ask_notron` runs the normal Nemotron
  graph. Nemotron still decides and the Guard still authorizes, and writes stay off unless the server
  was started with `--writes`. Note reads use no API key. Only `ask_notron` needs Nebius.
- **Pure core, thin SDK shell.** All logic lives in plain modules (`schema.py`, `connectors.py`,
  `bridge.py`) that tests drive with fakes. Only `mcp_client.py` and `mcp_server.py` import the
  official `mcp` SDK, which is an optional extra (`pip install 'notron[mcp]'`). When the extra is not
  installed, both features say so and the rest of Notron still works.

**Tech Stack:** Python 3.11, official MCP Python SDK (`mcp`, optional extra, version pinned at
implementation time), existing `channels`, `nodes.project`, `outbound`, `library`, `credentials`,
`retrieval`, `calendar`, `reminders`, `graph`. Before you write any SDK code, confirm the current SDK
API with context7 (`resolve-library-id mcp` then `query-docs`): the class names below
(`ClientSession`, `stdio_client`, `StdioServerParameters`, `FastMCP`) are from the 1.x line.

**Supersedes:** R04 Task 1's custom `protocol-v1` for *tools*. Add one line to
`docs/production/plans/R04-plugin-kit.md` (Task B6). R04's declarative skills (Task 2) still stand.
They resolve tool IDs, and connector tools become valid IDs.

**Invariants this plan must not break** (from `CLAUDE.md`): no model in the write path; grants live in
the registry and never in a note or model output; the model names tools and never commands; ignored
notes are invisible; no attachment is ever returned; tool output is untrusted data; tests touch no
network, no subprocess and no real Notes app.

**Estimate:** Part A ~4 days, Part B ~2.5 days, docs ~0.5 day. Parts A and B are independent after
Task A1 and can run in parallel worktrees.

---

## Part A — Notron uses MCP servers

### Task A1: Optional dependency + outbound purposes

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies]`)
- Modify: `notron/outbound.py:14-16` (`Purpose`)
- Test: `tests/test_outbound.py`

**Step 1: Write the failing test** (append to `tests/test_outbound.py`)

```python
def test_connector_and_export_are_known_purposes():
    from notron.outbound import Passage, prepare_outbound
    assert prepare_outbound("connector", [Passage("hello", "user_request")]) == ["hello"]
    assert prepare_outbound("export", [Passage("hello", "user_request")]) == ["hello"]
```

**Step 2: Run it.** `.venv/bin/python -m pytest tests/test_outbound.py -q -k purposes`. Expected: FAIL with `PolicyError: Unknown outbound purpose.`

**Step 3: Implement**

```python
Purpose = Literal['route', 'write', 'schedule', 'organize', 'reflect', 'embed', 'search', 'delegate',
                  'connector', 'export']
```

`connector` covers arguments Notron sends *to* a third-party MCP server. `export` covers note text
Notron hands *to* an MCP client.

In `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0"]
mcp = ["mcp==<latest 1.x at implementation time>"]
```

Run `uv lock` and `.venv/bin/pip install -e '.[mcp,dev]'`, then write the pinned version into this plan's
Evidence section.

**Step 4: Run.** The same test passes, and so does the full suite (`.venv/bin/python -m pytest tests -q`).

**Step 5: Commit.** `git commit -m "feat(mcp): optional mcp extra and connector/export outbound purposes"`

---

### Task A2: Schema subset validator (`notron/schema.py`)

Nemotron now supplies *arguments*, which is new: until now it only named tools. Code must check every
argument against the tool's schema before anything leaves the Mac. Support a deliberately small subset.
A tool whose schema uses anything else cannot be approved in v1. That is safer than half-validating it.

**Files:**
- Create: `notron/schema.py`
- Test: `tests/test_schema.py`

**Step 1: Write the failing tests**

```python
from notron import schema

S = {"type": "object", "additionalProperties": False, "required": ["q"],
     "properties": {"q": {"type": "string", "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "state": {"type": "string", "enum": ["open", "closed"]},
                    "labels": {"type": "array", "items": {"type": "string"}}}}


def test_supported_subset_is_accepted():
    assert schema.supported(S)


def test_unsupported_keywords_make_a_tool_unapprovable():
    assert not schema.supported({"type": "object", "properties": {"x": {"$ref": "#/defs/x"}}})
    assert not schema.supported({"type": "object", "properties": {"x": {"oneOf": []}}})
    assert not schema.supported({"type": "string"})  # top level must be an object


def test_valid_arguments_pass():
    assert schema.validate(S, {"q": "bug", "limit": 5, "state": "open", "labels": ["p1"]}) == []


def test_every_violation_is_reported_without_echoing_values():
    errs = schema.validate(S, {"limit": 999, "state": "nope", "extra": "sk-secret", "labels": [1]})
    assert set(errs) == {"q: required", "limit: above maximum", "state: not allowed",
                         "extra: unexpected", "labels[0]: wrong type"}
    assert not any("sk-secret" in e or "nope" in e for e in errs)


def test_booleans_are_not_integers():
    assert schema.validate(S, {"q": "x", "limit": True}) == ["limit: wrong type"]
```

**Step 2: Run.** `.venv/bin/python -m pytest tests/test_schema.py -q`. Expected: FAIL (module missing).

**Step 3: Implement**

```python
"""The small slice of JSON Schema a connector tool may use, checked in plain code.

Nemotron proposes a tool's arguments; nothing it proposes leaves the Mac until
this module agrees. A tool whose schema needs more than this slice is refused at
approval time rather than half-checked at call time. Errors name the field and
the rule, never the value: a value can be a secret the model copied from a note.
"""

from __future__ import annotations

TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
         "array": list, "object": dict}
KEYWORDS = {"type", "description", "title", "properties", "required", "additionalProperties",
            "enum", "items", "minimum", "maximum", "minLength", "maxLength", "maxItems",
            "default", "examples"}


def supported(s: dict, *, top: bool = True) -> bool:
    if not isinstance(s, dict) or set(s) - KEYWORDS:
        return False
    if top and s.get("type") != "object":
        return False
    t = s.get("type")
    if t not in TYPES:
        return False
    if t == "object":
        props = s.get("properties", {})
        return isinstance(props, dict) and all(supported(v, top=False) for v in props.values())
    if t == "array":
        return supported(s.get("items", {"type": "string"}), top=False) and \
            s.get("items", {}).get("type") not in ("array", "object")
    return True


def _is(value, t: str) -> bool:
    if t in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, TYPES[t])


def validate(s: dict, value, path: str = "") -> list[str]:
    here = path or "arguments"
    t = s.get("type")
    if not _is(value, t):
        return [f"{here}: wrong type"]
    if "enum" in s and value not in s["enum"]:
        return [f"{here}: not allowed"]
    errs: list[str] = []
    if t == "string":
        if len(value) > s.get("maxLength", 2000):
            errs.append(f"{here}: too long")
    elif t in ("integer", "number"):
        if "minimum" in s and value < s["minimum"]:
            errs.append(f"{here}: below minimum")
        if "maximum" in s and value > s["maximum"]:
            errs.append(f"{here}: above maximum")
    elif t == "array":
        if len(value) > s.get("maxItems", 50):
            errs.append(f"{here}: too many items")
        item = s.get("items", {"type": "string"})
        for i, v in enumerate(value):
            errs += validate(item, v, f"{path}[{i}]")
    elif t == "object":
        props = s.get("properties", {})
        for name in s.get("required", []):
            if name not in value:
                errs.append(f"{name}: required")
        for name, v in value.items():
            if name not in props:
                errs.append(f"{name}: unexpected")   # always closed, whatever the schema says
            else:
                errs += validate(props[name], v, name if not path else f"{path}.{name}")
    return errs
```

Note: an object is **always closed**. An unknown argument is refused even if the server's schema
allowed it, because unknown arguments are where smuggled data goes. Strings default to a 2,000-character cap.

**Step 4: Run.** The tests pass.

**Step 5: Commit.** `git commit -m "feat(mcp): schema subset validator for model-proposed tool arguments"`

---

### Task A3: MCP client seam (`notron/mcp_client.py`)

The only module that imports the SDK on the client side. It exposes two synchronous functions,
because the graph is synchronous and `brain._deadline_guard` needs the main thread. It spawns one
process per call: simple and stateless, and a server cannot hold state between requests. Startup cost
is accepted in v1 (measure it in A8).

**Files:**
- Create: `notron/mcp_client.py`
- Test: `tests/test_mcp_client.py`

**Step 1: Write the failing tests**

```python
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
```

**Step 2: Run.** It fails (module missing).

**Step 3: Implement**

```python
"""Talking to an MCP server over stdio. The only client-side importer of the SDK.

One process per call, launched from an argv the user typed at the terminal,
with a stripped environment plus exactly the secrets that server was granted.
Everything that comes back is untrusted text for the model, capped like
`tools.MAX_OUTPUT`.
"""

from __future__ import annotations

import os

from .tools import MAX_OUTPUT

TIMEOUT = 30
KEEP_ENV = ("PATH", "HOME", "USER", "LANG", "TMPDIR")


class Unavailable(RuntimeError):
    pass


def _sdk():
    try:
        import mcp  # noqa: F401
        return mcp
    except ImportError:
        return None


def child_env(secrets: dict[str, str]) -> dict[str, str]:
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    env.update(secrets)
    return env


def result_text(blocks: list[dict], *, is_error: bool) -> str:
    parts = [b.get("text", "") if b.get("type") == "text" else f"[{b.get('type', 'content')} omitted]"
             for b in blocks]
    out = "\n".join(p for p in parts if p).strip() or "(nothing)"
    if is_error:
        out = "error: " + out[:300]
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n[… {len(out) - MAX_OUTPUT} more characters not shown]"
    return out


def _run(argv, secrets, work):
    """Open a session, run `work(session)` and close it. Tests replace this."""
    if _sdk() is None:
        raise Unavailable("MCP support is not installed: pip install 'notron[mcp]'")
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=argv[0], args=list(argv[1:]), env=child_env(secrets))

    async def main():
        with anyio.fail_after(TIMEOUT):
            async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
                await session.initialize()
                return await work(session)
    return anyio.run(main)


def list_tools(argv, secrets) -> list[dict]:
    async def work(session):
        res = await session.list_tools()
        return [{"name": t.name, "description": t.description or "",
                 "inputSchema": t.inputSchema or {"type": "object"},
                 "annotations": (t.annotations.model_dump(exclude_none=True) if t.annotations else {})}
                for t in res.tools]
    return _run(argv, secrets, work)


def call_tool(argv, secrets, name: str, arguments: dict) -> str:
    async def work(session):
        res = await session.call_tool(name, arguments)
        blocks = [c.model_dump() for c in res.content]
        return result_text(blocks, is_error=bool(res.isError))
    return _run(argv, secrets, work)
```

Check `_run` against the current SDK docs. The SDK spawns the child itself through anyio, not through
`subprocess.Popen`, so the conftest subprocess block may not catch it. Add an autouse fixture to
`tests/conftest.py` that makes `mcp_client._run` raise `AssertionError("MCP servers require a fake in
unit tests")`, following the `_native_subprocesses_require_mocks` pattern.

**Step 4: Run.** The tests pass, and so does the full suite.

**Step 5: Commit.** `git commit -m "feat(mcp): stdio client seam with stripped env and capped output"`

---

### Task A4: Connector registry (`notron/connectors.py`)

**Files:**
- Create: `notron/connectors.py`
- Test: `tests/test_connectors.py`

**Behaviour:**
- `connectors.json` in `paths.data_dir()` (same pattern as `channels._path`). A damaged file raises; it never reads as empty (copy the reasoning comment from `channels.load`).
- `Server(name, argv: tuple, secrets: tuple[str, ...], tools: dict[str, ApprovedTool])`. `ApprovedTool(name, description, schema, digest)`.
- `add(name, argv, secrets=())`: validates the name (`channels.NAME`) and requires an argv whose first element is a bare command or an absolute path. It lists nothing yet and approves nothing.
- `discover(name) -> list[Offer]`: calls `mcp_client.list_tools` and returns each tool with `approvable: bool` and `why` (`"changes things: v1 is read-only"` when `readOnlyHint` is not true, or `"arguments too complex for v1"` when `schema.supported` is false).
- `approve(name, tool_names)`: re-lists the tools, refuses any tool that is not approvable, and stores each digest = `sha256(json.dumps({name, description, inputSchema, annotations}, sort_keys=True))`.
- `remove(name)`, `load()`, `get(name)`.
- `qualified(server, tool) -> "server.tool"`. The model sees this name.
- `menu_for(channel) -> list[str]` renders `- github.search_issues: <description ≤160 chars, newlines stripped> args: {"q": "string (required)", ...}`. The description is server-written text. It is capped and flattened and goes into the passage that already says the list is data.
- `call(channel, qualified, arguments) -> str`: refuses with a plain line unless the server is granted to this channel and the tool is approved. It validates the arguments with `schema.validate`. It refuses when `privacy.contains_secret` is true for any string argument (`"blocked: arguments looked like a credential"`) and redacts with `prepare_outbound("connector", [Passage(json.dumps(args), "model")])`. Before running, it re-lists the tools and compares digests: a mismatch disables that tool (`"changed since you approved it — run notron connect approve <server>"`) and records it. Then `mcp_client.call_tool`. Every failure returns a line and never raises into the graph, except `CredentialUnavailable`, `StorageError` and `PolicyError`, which propagate as they do elsewhere in `nodes.project`.

> Re-listing tools on every call doubles the process launches. In v1, correctness beats latency:
> the rug-pull check is the point. A8 measures it. If it is too slow, cache the list for 10 minutes
> in a later task.

**Step 1: Write the failing tests.** Inject a fake through `monkeypatch.setattr(mcp_client, "list_tools", ...)` and `call_tool`. The tests:

```python
def test_a_write_tool_cannot_be_approved()            # readOnlyHint missing → approvable False
def test_a_complex_schema_cannot_be_approved()        # $ref → approvable False
def test_approval_pins_a_digest_and_survives_reload()
def test_a_tool_changed_after_approval_is_disabled()  # fake list returns new description → call refused, nothing called
def test_an_ungranted_server_is_refused_for_this_channel()
def test_invalid_arguments_never_reach_the_server()   # schema error → call_tool not invoked
def test_secret_shaped_arguments_never_reach_the_server()  # "sk-abcdefghijklmnopqrstuvwx"
def test_a_damaged_registry_raises_not_empty()
def test_server_failure_is_a_line_not_an_exception()  # call_tool raises OSError → "github.x: could not run (OSError)"
def test_server_descriptions_are_capped_and_flattened_in_the_menu()
```

Name each test after the failure it prevents (house style).

**Step 2: Run them.** They fail. **Step 3:** Implement to the behaviour above, following the style of `tools.py` and `channels.py`: frozen dataclasses, module-level `_path()`, comments that explain *why*. **Step 4:** Run them. They pass. **Step 5: Commit.** `feat(mcp): connector registry with read-only approval and digest pinning`

---

### Task A5: Server secrets in the Keychain

A connector often needs a token (`GITHUB_TOKEN`, `LINEAR_API_KEY`). Never `.env`, never argv.

**Files:**
- Modify: `notron/credentials.py:207` (`PROVISIONABLE`, `provision_api_key`, `forget_api_key`)
- Check: `mac/Sources/NotronKeychainHelper/main.swift`. If the helper allowlists names, extend it to accept `connector.<server>.<VAR>`
- Test: `tests/test_credentials.py`

**Implement:** add `CONNECTOR_SECRET = re.compile(r"^connector\.[A-Za-z0-9-]{1,40}\.[A-Z][A-Z0-9_]{0,63}$")` and a
`_provisionable(name)` helper that returns `name in PROVISIONABLE or CONNECTOR_SECRET.match(name)`, used
by provision and forget. `storage-key` and `managed-refresh` stay refused; add a test that proves it.
`connectors.call` and `discover` resolve `Server.secrets` (var names) through
`credentials.get(f"connector.{server}.{var}")`. A missing secret returns `"github: needs GITHUB_TOKEN — notron connect secret github GITHUB_TOKEN"`.

**Tests:** connector names are accepted, the two reserved names are still refused, and a malformed name (`connector.x.lower`) is refused.

**Commit:** `feat(mcp): connector secrets live in the Keychain, never argv or env files`

---

### Task A6: Channels grant connectors

**Files:**
- Modify: `notron/channels.py` (`Channel`, `_validate`, `load`, `_save`, `add`, `update`)
- Modify: `notron/cli.py` (`channel set` gains `--connect a,b` and `--disconnect a`)
- Test: `tests/test_channels.py`

**Implement:** `Channel.connectors: tuple[str, ...] = ()`. Validation: each name must exist in
`connectors.load()`, and a connector grant also requires `"read"` in `allow` (connectors are reads in v1).
Older `channels.json` files without the key load as `()`.

**Tests:**
- `test_a_channel_cannot_grant_an_unregistered_connector`
- `test_old_channels_file_loads_without_connectors`
- `test_connector_grant_needs_read`

**Commit:** `feat(mcp): a channel grants connectors by name in the registry`

---

### Task A7: Nemotron picks connector calls in `project`

**Files:**
- Modify: `notron/nodes.py:268-296` (`PROJECT_SYSTEM`, `WORK_SYSTEM`) and `:335-397` (`project`)
- Test: `tests/test_channels.py` (next to `test_nemotron_super_decides_and_code_runs_only_granted_tools`)

**Prompt change.** Add to both system prompts:

```
"calls": [{"tool": "server.tool", "arguments": {...}}]  — connector tools from the list, with
arguments matching the shape shown. Use only values the user gave or the conversation shows.
```

Extend the JSON shape line to
`{"kind": ..., "tools": [...], "calls": [...], "web": ..., "why": ...}`, and raise `max_tokens` from 300 to 600
(arguments cost tokens; reasoning headroom is added by `brain.ask`).

**Code change in `project`:**
- Build the menu as `tools.menu(channel)` + `"\n# Connector tools\n" + "\n".join(connectors.menu_for(channel))` when the channel has connectors.
- After the decision: `calls = out.get("calls") if isinstance(out.get("calls"), list) else []`. Keep only dicts with a string `tool` and a dict `arguments`, deduplicate by `(tool, json.dumps(arguments, sort_keys=True))`, and count them against the same `MAX_TOOLS` budget (built-in tools first).
- For each call: `state.tools.append(f"### {tool} — {channel.name}\n{connectors.call(channel, tool, arguments)}")`.
- `checked` lists the connector tool names too. The decision line keeps "decided by Nemotron Super in Xs".
- Trace: `calls=[names]` and `refused [...]` for ungranted names (the same as built-in tools).

**Tests (fake brain `Decides`, fake `connectors.call`):**

```python
def test_nemotron_picks_a_connector_call_and_code_runs_it(monkeypatch)
def test_a_connector_call_outside_the_grant_is_refused_and_traced(monkeypatch)
def test_connector_calls_share_the_tool_budget(monkeypatch)        # 4 built-ins + 2 calls → 4 run
def test_a_hostile_line_cannot_add_a_connector_to_the_menu(monkeypatch)
def test_malformed_calls_are_dropped_not_fatal()                   # calls: "x", [{"tool": 1}]
```

Run `.venv/bin/python -m pytest tests/test_channels.py tests/test_nodes.py tests/test_graph.py -q`. **Commit:** `feat(mcp): Nemotron chooses connector calls; code validates and runs them`

---

### Task A8: CLI + live gate

**Files:**
- Modify: `notron/cli.py`: new command group `connect`
- Test: `tests/test_cli_wiring.py`

```bash
notron connect add time -- uvx mcp-server-time      # argv after `--`, stored verbatim
notron connect tools time                           # discover: name · read-only? · approvable? · why
notron connect approve time get_current_time convert_time
notron connect secret github GITHUB_TOKEN           # reads the value from stdin, like `key set`
notron connect list [--json]                        # servers, approved tools, disabled tools
notron connect remove time                          # also removes it from every channel grant
notron channel set Synqology --connect time
```

Wiring tests: each subcommand parses and calls the right `connectors` function (fakes). `connect`
is not in `cli.WRITES`, because it changes config and not Notes.

**Live gate (manual, on the Mac; record results in `docs/production/evidence/2026-10-mcp-connectors.md`):**
1. `notron connect add time -- uvx mcp-server-time` → `tools` → `approve` → grant to a test channel.
2. Hey Siri, "add what time is it in Tokyo to my Notron Test note". The reply shows `checked time.get_current_time · decided by Nemotron Super`.
3. Add the official GitHub MCP server read-only (or the filesystem server scoped to one folder). Confirm a write tool shows `changes things: v1 is read-only` and cannot be approved.
4. Edit a tool description in a local test server and confirm the next call is refused as "changed since you approved it".
5. Record the launch latency per call. If it is over 3 s, open a follow-up for a 10-minute tool-list cache.

**Commit:** `feat(mcp): notron connect — add, approve, grant and remove MCP servers`

---

## Part B — Notron's Apple bridge as an MCP server

### Task B1: Bridge functions (`notron/bridge.py`), no SDK

Plain functions return plain dicts. Every read goes through the library and outbound policy, so an MCP
client sees exactly what a Nemotron call would, and no more.

**Files:**
- Create: `notron/bridge.py`
- Test: `tests/test_bridge.py`

**Interfaces:**

```python
def notes_list(limit: int = 200) -> list[dict]        # [{id, title, folder, modified}] from library.user_notes()
def notes_search(query: str, limit: int = 8) -> list[dict]  # retrieval.search — keyword, no API key
def notes_read(note_id: str) -> dict                  # {id, title, folder, text, has_attachments_not_shown}
def agenda(days: int = 7) -> dict                     # {today: calendar.brief(), week: calendar.week(), reminders: reminders.summary()}
def ask(request: str, *, writes: bool, brain) -> dict # {answer, results} via graph.run_request(..., dry_run=not writes)
```

Rules, each with a test:
- `notes_read` re-checks `library.state_of(note_id, modified)` at call time and returns `{"error": "not available"}` for an ignored or unknown note, using the same words for both so a client cannot probe which notes exist.
- Text = `markup.to_text(notes.read_body(id))`, passed through `prepare_outbound("export", [Passage.from_note(text, note)])`, so vault and secret redaction happen for free. An `<img>` or attachment is never returned. `has_attachments_not_shown: true` when `markup.holds_media(body)` (invariant 12: never imply it showed something it did not).
- `📌 About Me` and the other `workspace.SYSTEM_NOTES` are not listed and not readable (they are instructions, not data).
- Calendar or Reminders permission missing: return the same "I cannot read your Calendar" wording `nodes.agenda` uses, never an empty "free week".
- `ask` builds an envelope with `requests.create(request, source="mcp")` (check `requests.create` accepts that source; add it if it validates a fixed set) and calls `graph.run_request(envelope, brain=brain, dry_run=not writes, trigger="mcp")`. If `graph`/the router rejects an unknown trigger, add `"mcp"` beside `"manual"` with the same never-`ignore` rule, and test it.
- Caps: `limit ≤ 50`, `text ≤ 20,000` characters with a "[… more not shown]" marker, `days ≤ 31`.

**Tests** (FakeNotesApp is autouse; the policy fixture already configures a library):

```python
def test_an_ignored_note_is_indistinguishable_from_a_missing_one()
def test_about_me_is_never_listed_or_read()
def test_read_redacts_secrets_on_the_way_out()
def test_a_note_with_a_picture_says_so_and_returns_no_image()
def test_search_uses_no_brain_and_no_network()
def test_ask_without_writes_is_a_dry_run(monkeypatch)
def test_blind_calendar_is_reported_not_empty()
```

**Commit:** `feat(bridge): policy-checked Notes/Calendar/Reminders reads for MCP clients`

---

### Task B2: The server (`notron/mcp_server.py`) + CLI

**Files:**
- Create: `notron/mcp_server.py`
- Modify: `notron/cli.py`: `notron mcp serve [--writes] [--no-ask]` and `notron mcp config`
- Test: `tests/test_mcp_server.py`

```python
"""Notron's Apple bridge as an MCP server, over stdio.

Thin on purpose: every tool is one call into `bridge`, which owns the policy.
`ask_notron` runs the normal graph, so Nemotron still decides and the Guard
still authorizes; without --writes it is a dry run.
"""

def build(*, writes: bool, ask: bool, brain_factory):
    from mcp.server.fastmcp import FastMCP
    from . import bridge
    app = FastMCP("notron")

    @app.tool(annotations={"readOnlyHint": True})
    def notes_search(query: str, limit: int = 8) -> list[dict]:
        """Search the user's Apple Notes they allowed Notron to read."""
        return bridge.notes_search(query, limit)

    # notes_list, notes_read, agenda — same pattern, all readOnlyHint True

    if ask:
        @app.tool(annotations={"readOnlyHint": not writes})
        def ask_notron(request: str) -> dict:
            """Ask Notron (NVIDIA Nemotron) — it can answer from notes, calendar and reminders,
            and, if the user enabled writes, file notes or create reminders through its own safety checks."""
            return bridge.ask(request, writes=writes, brain=brain_factory())
    return app


def serve(**kw):
    build(**kw).run()   # stdio
```

- Stdout is the MCP channel. Nothing in `serve` may `print`. Route any warning to stderr, and add a test that `build` prints nothing.
- `brain_factory` is `cli._brain`, called lazily, so the reads work with no Nebius key.
- `notron mcp config` prints a ready-to-paste Claude Desktop / Cursor JSON block with the absolute path of the current interpreter:
  `{"mcpServers": {"notron": {"command": "<sys.executable>", "args": ["-m", "notron", "mcp", "serve"]}}}`.

**Tests:** with the SDK installed (skip with `pytest.importorskip("mcp")` otherwise), `build()` registers
exactly the expected tool names; `--no-ask` removes `ask_notron`; `--writes` flips its `readOnlyHint`;
`build` writes nothing to stdout (`capsys`).

**Commit:** `feat(bridge): notron mcp serve — the Apple bridge for any MCP client`

---

### Task B3: Device gate — real client, real permissions

Manual, on the Mac. Record results in `docs/production/evidence/2026-10-mcp-bridge.md`. Do **not** mark it done with mocks.

1. `notron mcp config` → paste into Claude Desktop's config → restart Claude Desktop.
2. Ask Claude Desktop "search my notes for parking". Expect a macOS Automation prompt for Notes, attributed to **Claude Desktop** (TCC answers per responsible process, see CLAUDE.md "EventKit's speed is worthless…"). Record what actually appears.
3. Ask "what's on my calendar this week". Record whether EventKit reads real events or reports blind under Claude Desktop's identity. Either outcome is acceptable if the reply says so honestly.
4. Ignore a note in "Your notes", then ask for it by title. It must come back "not available".
5. With the listener running, issue several searches. Confirm the Notes lock queues them and nothing wedges.
6. `ask_notron "what should I focus on today"` with and without `--writes`. Confirm the dry run writes nothing (check `📊 Log`).

---

### Task B4: Docs, README, R04 note

**Files:**
- Create: `docs/connectors.md`: add, approve and grant an MCP server; what read-only means; secrets; why changed tools get disabled.
- Create: `docs/mcp-server.md`: the Claude Desktop/Cursor setup, the tool list, what is never returned (ignored notes, About Me, attachments, secrets), the `--writes` warning, and the privacy disclosure: *note text you let a client read is sent to that client's AI provider*.
- Modify: `README.md`: two short sections, "Give Notron new tools (MCP)" and "Use your Apple Notes from any AI (MCP)", plus a quickstart.
- Modify: `docs/production/plans/R04-plugin-kit.md`: one line under the Goal: *"Tools: superseded by MCP connectors (docs/plans/2026-10-01-mcp-connectors-and-apple-bridge.md). Task 2 skills remain."*

**Commit:** `docs(mcp): connectors, Apple bridge server, README quickstart`

---

## Out of scope (next plans, in order)

1. Write-capable connector tools through the existing id-bound Approve reminder.
2. Remote (streamable HTTP + OAuth) MCP servers such as Notion and Linear.
3. A tool-list cache, if A8 shows launch latency over 3 s.
4. Fix the hardcoded `/Users/m1labs/...` defaults in `mac/Sources/Notron/Core.swift:14,19` so a fresh clone works (open-source blocker, ~30 min).
5. A community connector list in the README (links only, no hosting, no endorsement).

## Evidence

- `mcp` version pinned: `mcp==2.2.0` (latest on PyPI 2026-10-01; a 2.x major, so the 1.x class names above were checked against the installed SDK in A3). `uv.lock` updated.
- A8 live gate: _link_
- B3 device gate: _link_
