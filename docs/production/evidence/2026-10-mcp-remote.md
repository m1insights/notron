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
| `connectors.discover` on GitHub with no token | `the server refused Notron's sign-in (HTTP 401); check the token or sign in again`, 0.07 s. Before the fix this read `could not list tools (ExceptionGroup)`, and the SDK printed a full traceback to the terminal; both fixed with regression tests. |
| A name that does not resolve | `could not find that server's address`. |
| GitHub with a real token: list, approve, call | **Pending owner run**: `.venv/bin/python scripts/mcp_remote_gate.py github`. Reading the token from `gh auth token` was refused by this session's safety check. |
| Vercel browser sign-in, list, approve, call | **Pending owner run**: `.venv/bin/python scripts/mcp_remote_gate.py vercel`. |
