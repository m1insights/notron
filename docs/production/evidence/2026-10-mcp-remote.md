# Remote MCP connectors — live gate

Run 2026-10-02 on the developer's Mac, branch `feature/mcp-remote`, `mcp` 2.2.0,
scratch `NOTRON_DATA_DIR`, in-memory Keychain stand-in (the signed app, P06, does
not exist yet, so `connect secret`/`login` from the CLI still stop at secure
startup, as for local connectors).

| Step | Result |
|---|---|
| Anonymous `initialize` to `https://api.githubcopilot.com/mcp/` | 401; resource metadata names `github.com/login/oauth`, which publishes no dynamic client registration. A token (`--bearer`) is the route. |
| Anonymous `initialize` to `https://mcp.vercel.com` | 401; resource metadata names `vercel.com`, registration endpoint `api.vercel.com/login/oauth/register`. |
| `connectors.discover("vercel")` (auth `oauth`, background) | Notron registered itself with Vercel (client id stored, redirect `http://127.0.0.1:8976/callback`), reached the browser step and stopped: `vercel: needs sign-in — notron connect login vercel`, 0.77 s. No browser opened. |
| Same, after review fixes | Stops before any request: `vercel: needs sign-in — notron connect login vercel`, 0.18 s, no client registered. Registration now happens only inside `connect login`. Sign-in requests to server-named URLs are held to public https (a test feeds `169.254.169.254`). |
| `connectors.discover` on GitHub with no token | `the server refused Notron's sign-in (HTTP 401); check the token or sign in again`, 0.07 s. Before the fix this read `could not list tools (ExceptionGroup)`, and the SDK printed a full traceback to the terminal; both fixed with regression tests. |
| A name that does not resolve | `could not find that server's address`. |
| GitHub with a real token: list, approve, call | **Pass.** 46 tools listed in 0.80 s; 7 approvable read-only (`get_me`, `get_team_members`, `get_teams`, `search_code`, `search_commits`, `search_repositories`, `search_users`); 18 refused as write tools, 21 read-only tools refused as "arguments too complex for v1" (e.g. `search_issues`, `search_pull_requests`). `github.get_me` answered with the real profile, 1.55 s and 1.51 s per call (re-list + call). |
| Vercel browser sign-in, list, approve, call | **Pending owner run**: `.venv/bin/python scripts/mcp_remote_gate.py vercel`. |

## Vercel, with a real sign-in (2026-10-02, after restart)

| Step | Result |
|---|---|
| Browser sign-in (owner clicked Authorize) | **Pass.** Tokens returned to the loopback listener; tools listed in 2.6–3.9 s. |
| Tools | 244 listed; 111 refused as write tools; **all 133 read-only tools refused** for one cause: a top-level `$schema` dialect label. Fixed in `schema.supported` (label accepted at the top, as a string only), regression test named after it. |
| GitHub re-run on current `main` | 46 tools, **26 approvable** read-only (Session 1's schema work); 2 still refused (array of objects; a non-nullable `anyOf`). |
| Noise | Login prints an asyncio "not holding this lock" traceback from the SDK's task teardown; the run continues. Open item. |
