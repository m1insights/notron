# Remote MCP servers (streamable HTTP + sign-in) — design

Follows `2026-10-01-mcp-connectors-and-apple-bridge.md`, out-of-scope item 2.
Session 1 owns git + Tavily as connectors; this plan touches neither.

## What the user gets

```
notron connect add github --url https://api.githubcopilot.com/mcp/ --bearer GITHUB_TOKEN
notron connect secret github GITHUB_TOKEN        # paste a PAT on stdin
notron connect add vercel --url https://mcp.vercel.com --oauth
notron connect login vercel                       # browser opens, sign in once
notron connect tools vercel / approve / channel set --connect   # unchanged
```

Everything after `add` is the same as a local server: read-only tools only,
approval pins a digest, every call re-lists first (rug-pull check), output is
untrusted and capped, grants live in the registry.

## Measured before designing (2026-10-02, unauthenticated probes)

| Server | Answer to an anonymous `initialize` | Sign-in |
|---|---|---|
| `https://api.githubcopilot.com/mcp/` | 401, resource metadata → `github.com/login/oauth` | GitHub publishes **no** dynamic client registration, so a new client cannot self-register. A PAT as a bearer header is the route. |
| `https://mcp.vercel.com` | 401, resource metadata → `vercel.com` | Dynamic registration at `api.vercel.com/login/oauth/register`: OAuth with PKCE works for a new client in principle. |

So both auth modes are needed: **bearer** (a Keychain secret sent as a header)
and **oauth** (register, browser sign-in, refresh).

## Decisions

1. **One registry row, either `argv` or `url`, never both.** `auth` is
   `none | bearer | oauth`. A row with a `url` reads as damaged to older code,
   which is the fail-closed direction.
2. **URL rules (plain code, at `add`):** https only, no username/password, no
   query or fragment (where tokens hide), not `.local`/`.internal`/a private IP
   literal (reuses `network._https_parts`), nothing `privacy` calls a secret.
3. **Every connect re-resolves the host and refuses if any address is not
   public** (`network.public_https_url`). Known gap, documented: the SDK's HTTP
   client re-resolves, so a DNS-rebinding server could swap the address between
   the check and the connect. The pinned-socket transport in `network.py` is
   synchronous and provider-only; extending it to the async MCP client is a
   follow-up.
4. **Bearer secret goes in a header, never an environment.** Same Keychain item
   naming (`connector.<name>.<VAR>`), same `connect secret` command.
5. **OAuth tokens live in the Keychain** as two items, `OAUTH_TOKENS` (with an
   absolute expiry, so a later process can refresh instead of starting over)
   and `OAUTH_CLIENT` (the registration, whose loopback redirect port is reused
   on the next login). Base64 so a scope with spaces is still one line.
6. **Only `connect login` may open a browser.** Anywhere else (listener, tools,
   approve, call) a missing or dead sign-in is one line:
   `vercel: needs sign-in — notron connect login vercel`. A background job
   waiting on a browser would hang the listener.
7. **Login callback** is a one-shot HTTP listener on `127.0.0.1`, random port,
   5-minute limit; state is checked by the SDK (PKCE + `state` + issuer).
8. **`remove` forgets the OAuth items too**, for the same reason it forgets
   secrets: a token must not be inherited by a later server with the same name.

## Not in this change

Write tools; a pre-registered OAuth client id (would give GitHub OAuth instead
of a PAT); pinned-address async transport (decision 3); SSE-only servers.
