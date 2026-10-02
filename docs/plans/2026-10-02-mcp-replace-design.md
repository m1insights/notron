# Replace built-in git/GitHub tools and Tavily search with MCP connectors

Date: 2026-10-02. Branch `feature/mcp-replace`.

## Goal

Delete `notron/tools.py` (fixed-argv git/gh tools) and the direct Tavily client
(`research._search`, the `search` transport operation, the `tavily` provider in
`network.py`/`brain.py`, the `tavily-api-key` credential). Project channels and
the researcher get the same abilities from the official MCP servers through the
existing connector path (`connectors.py` / `mcp_client.py`). Apple Notes,
Calendar, Reminders and Mail stay native.

## What was measured first (2026-10-02)

| Server | Version | Result against connector v1 rules |
|---|---|---|
| `mcp-server-git` (modelcontextprotocol/servers) | 2026.8.18 via `uvx` | 4 read tools approvable; `git_log`, `git_branch` refused: optional args are `anyOf [T, null]`. Write tools correctly refused. `--repository` blocks other paths (`outside the allowed repository`). **Refuses to start on a monorepo subfolder** (Synqology lives in `~/Dev`). |
| `github-mcp-server` (github/github-mcp-server) | 1.12.2 via Homebrew, `stdio --read-only` | Every tool marks `readOnlyHint: true`, but 22 of 25 refused: unknown keyword `x-mcp-header` on `owner`/`repo`. |
| `tavily-mcp` (tavily-ai) | 0.2.22 via `npx` | **No annotations at all**, so every tool refused as "changes things". Runs keyless (no API key needed for search). Output is text (`Title:`/`URL:`/`Content:`) and may carry vendor promo lines ("Earn bonus credits by POSTing…"). |

## Decisions

1. **Presets, in code.** `notron connect preset git|github|tavily` registers the
   official server with a pinned version, its secrets, and its default approved
   tools. Presets are plain code: no note, model or server can create one.
2. **Grants stay the user's switches.** A channel's `read` grant reaches the
   `git` (needs `--repo`) and `github` (needs `--github`) presets; `research`
   reaches `tavily`. Existing channels keep working once the presets are
   installed. `--connect` still grants any other server.
3. **Channel-bound arguments.** The model never chooses the repository. A preset
   binds arguments to the channel (`repo_path` → the channel's repo; `owner` /
   `repo` → its GitHub slug); bound arguments are hidden from the menu and set
   by code after redaction, overriding anything the model sent. The git server
   is also started with `--repository` = that repo (defence in depth).
4. **Monorepo subfolders** use the enclosing repository (nearest `.git` above).
   Trade-off: Synqology now sees the whole `~/Dev` history, not only its folder.
5. **Schema slice grows by two safe rules:** `anyOf [T, {"type":"null"}]` is an
   optional `T` that may be null; `x-*` vendor keys are ignored annotations (they
   constrain nothing in JSON Schema).
6. **Tavily is vouched read-only by code**, for `tavily_search` only and only
   when the registered argv is the preset's pinned argv. Search does not change
   anything; the server simply never says so.
7. **Researcher stays code-driven.** Nemotron still decides `web`; code calls
   `tavily.tavily_search` through the connector checks (grant, digest re-check,
   credential block, outbound redaction), parses Title/URL/Content and keeps the
   journal-first ranking. Promo lines and anything else are dropped by the
   parser. The Tavily tools are not put on the channel menu (no double path).
8. **Prefetch is removed.** It existed for 0.1 s local git calls; an MCP call is
   a server start. Latency is re-measured and recorded.
9. **Absolute server paths.** Preset install records the resolved binary, and
   the child gets that binary's own folder on `PATH`, so a launchd listener
   (short `PATH`) and `npx`'s `#!/usr/bin/env node` still start.

## Out of scope

Write tools, remote HTTP servers, a Mac UI for presets.
