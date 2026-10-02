# MCP replace — implementation plan

Design: `2026-10-02-mcp-replace-design.md`. Test: `.venv/bin/python -m pytest tests -q`.

1. `schema.py`: nullable `anyOf [T, null]` and ignored `x-*` keys (tests in `tests/test_connectors.py`).
2. `connectors.py`: `Server.preset`/`binds`, `PRESETS`, `install_preset`, preset grants via `read`/`research`, bound args, vouched read-only, `search_web`.
3. `mcp_client.py`: own `MAX_OUTPUT`; binary's folder on child `PATH`.
4. `research.py`: Tavily via connector + text parser; keep `quality`.
5. `nodes.py`: drop built-in tools, prefetch and the `tools` field from the decision prompt.
6. Delete `tools.py`, direct Tavily (`transport` search op, `network`/`brain` provider, `credentials.SEARCH_KEY`); fix tests.
7. `cli.py`: `connect preset`, `channel list` shows connector tools; docs (`docs/connectors.md`, CLAUDE.md module table).
8. Live gate: real git/GitHub/Tavily calls through `connectors.call`, latency recorded.
