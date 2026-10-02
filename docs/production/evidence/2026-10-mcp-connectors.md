# MCP connectors — live gate (Task A8)

Run 2026-10-01 on the developer's Mac (macOS 26.2, Python 3.14.2, `mcp` 2.2.0,
uvx 0.9.22), branch `feature/mcp-connectors`. The registry lived in a scratch
`NOTRON_DATA_DIR`, so the real `connectors.json` and `channels.json` were never
touched. No Siri, no Notes, no Nebius key: the steps that need those are marked
**pending**.

## Results

| # | Step | Result |
|---|---|---|
| 1 | `notron connect add time -- uvx mcp-server-time` | Registered; argv stored verbatim. 0.46 s (no server launched). |
| 1 | `notron connect tools time` | `get_current_time · read-only · approvable`, `convert_time · read-only · approvable`. 2.7 s wall, first run, including uvx resolving the package. |
| 1 | `notron connect approve time get_current_time convert_time` | Approved both; digests pinned. |
| 1 | Grant to a channel | `channels.update("Test", connect=("TIME",))` stored `time` (registered casing). `notron channel set Test --connect time` from the CLI stops at "Protected processing paused": `cmd_channel` calls `credentials.startup()`, which pauses until the P06 signed-startup gate. That predates this work. `connect secret` has the same dependency. |
| 1 | `connectors.call(Test, "time.get_current_time", {"timezone": "Asia/Tokyo"})` | Real JSON answer from the server (Tokyo time). |
| 2 | Siri: "add what time is it in Tokyo to my Notron Test note". The reply shows `checked time.get_current_time · decided by Nemotron Super` | **Pending owner device test** (needs Siri, Notes, the listener and a Nebius key). |
| 3 | A write tool cannot be approved | A local test server (built on the SDK's `MCPServer`) offered `delete_file` with `readOnlyHint: false`. `connect tools` printed `changes things · not approvable: changes things: v1 is read-only`. `connect approve gate delete_file` exited 1 with `gate.delete_file cannot be approved: changes things: v1 is read-only.`. A later `call` returns `not approved`. The official GitHub server was not tried: it needs a token and the Keychain path is behind P06. |
| 4 | A tool description changed after approval is refused | Edited the local server's `echo` description, then called again: `gate.echo: changed since you approved it — run notron connect approve gate`. Changing the description back **stays** refused, and the tool leaves the menu (`menu_for` → `[]`). `connect list` shows `changed since approval: echo`. Re-approving restores it (`echo: hi`). |
| 5 | Launch latency | See below. Well under the 3 s threshold, so no tool-list cache follow-up is needed. |
| — | Refusals in code, live | Wrong argument type → `refused — text: wrong type`. `sk-…`-shaped argument → `blocked: arguments looked like a credential`. Channel without the grant → `not granted to this channel`. Nothing reached the server in any of these cases. |
| — | `notron connect remove time` | Removed the server, and the `Test` channel's grant went with it (`connectors == ()`). |

## Latency (5 runs each; the first run is the warm-up)

| Server | `list_tools` | `call_tool` | `connectors.call` (re-list + call) |
|---|---|---|---|
| `uvx mcp-server-time` | 0.25 s median (0.50 s first) | 0.25 s median | 0.51 s median |
| Local Python server (`mcp` 2.x) | 0.38 s median | 0.38 s median | 0.73 s median (1.98 s first) |

Each call launches the server twice (once for the rug-pull re-list, once for the
call), so a connector call costs about 2× one launch. That is ~0.5–0.75 s here,
next to ~2.5 s for Super to decide. The 4-tool budget caps the worst case at
about 3 s of connector time per request.

## Notes

- Outbound policy fails closed. In the scratch data dir, with no note policy,
  `connectors.call` raised `PolicyError: Note policy unconfigured; AI paused`,
  which is the designed propagation. For the gate, a ready, empty policy was
  supplied in-process; redaction still ran.
- `mcp` 2.x renamed `FastMCP` to `MCPServer` (`mcp.server.mcpserver`); importing
  `mcp.server.fastmcp` raises with a migration link. Part B (`mcp_server.py`)
  must use the 2.x name or pin `mcp<2`.
- The listener-side check is still pending: step 2 above, and a connector
  answer appearing in a real channel reply.
