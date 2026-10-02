"""Talking to an MCP server. The only client-side importer of the SDK.

A local server is one process per call, launched from an argv the user typed at
the terminal, with a stripped environment plus exactly the secrets that server
was granted. A remote server (`Remote`) is one streamable-HTTP session per call
to a URL the user typed, carrying a bearer secret or an OAuth sign-in in a
header, never an environment. Everything that comes back is untrusted text for
the model, capped like `tools.MAX_OUTPUT`.

Both entry points are synchronous on purpose: the graph is synchronous and
`brain._deadline_guard` needs the main thread, so the async SDK runs inside its
own short-lived event loop here and nowhere else.

Written against `mcp==2.2.0`. The 2.x types are snake_case in Python
(`input_schema`, `read_only_hint`, `is_error`, `next_cursor`) and camelCase on
the wire; what this module hands back uses the wire names, because that is what
a server's own documentation and the connector registry talk about.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from .tools import MAX_OUTPUT

TIMEOUT = 30
#: How long `connect login` waits for the user to finish signing in.
LOGIN_TIMEOUT = 300
EXPIRY_MARGIN = 60
KEEP_ENV = ("PATH", "HOME", "USER", "LANG", "TMPDIR")
# A server that pages its tool list forever must not hold a call open forever.
MAX_PAGES = 10


class Unavailable(RuntimeError):
    pass


class NeedsLogin(Unavailable):
    """The server wants a browser sign-in, and this call is not allowed to start
    one. Only `notron connect login` may: a listener waiting on a browser nobody
    is looking at would hang every channel behind it."""


class TokenStore:
    """OAuth tokens and the client registration, kept wherever `read`/`write`
    put them (the Keychain, in `connectors`). Implements the SDK's
    `TokenStorage` protocol without importing it at module load.

    Tokens are stored with an absolute expiry. The SDK only learns `expires_in`
    from a fresh token response, so a token read back by a later process would
    otherwise look valid for ever: the call would 401, and the SDK answers a 401
    with a full browser sign-in instead of a refresh. Base64, so a scope with
    spaces in it is still one Keychain line.
    """

    TOKENS, CLIENT = "OAUTH_TOKENS", "OAUTH_CLIENT"

    def __init__(self, read, write):
        self._read, self._write = read, write
        self.expires_at: float | None = None

    def _get(self, key):
        raw = self._read(key)
        if raw is None:
            return None
        try:
            return json.loads(base64.urlsafe_b64decode(raw))
        except ValueError:
            return None  # unreadable reads as signed out: `login` repairs it

    def _put(self, key, value):
        self._write(key, base64.urlsafe_b64encode(json.dumps(value).encode()))

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        row = self._get(self.TOKENS)
        if not isinstance(row, dict) or not isinstance(row.get("token"), dict):
            return None
        at = row.get("expires_at")
        # A minute early: a token that runs out mid-call 401s, and the SDK
        # answers a 401 with a browser sign-in rather than the refresh it has.
        self.expires_at = float(at) - EXPIRY_MARGIN if isinstance(at, (int, float)) else None
        try:
            return OAuthToken.model_validate(row["token"])
        except ValueError:
            return None

    async def set_tokens(self, tokens):
        at = time.time() + tokens.expires_in if tokens.expires_in else None
        self.expires_at = at
        self._put(self.TOKENS, {"token": tokens.model_dump(mode="json", exclude_none=True),
                                "expires_at": at})

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        row = self._get(self.CLIENT)
        try:
            return OAuthClientInformationFull.model_validate(row) if isinstance(row, dict) else None
        except ValueError:
            return None

    def signed_in(self) -> bool:
        row = self._get(self.TOKENS)
        return isinstance(row, dict) and isinstance(row.get("token"), dict)

    async def set_client_info(self, info):
        self._put(self.CLIENT, info.model_dump(mode="json", exclude_none=True))

    def redirect_port(self) -> int | None:
        """The loopback port the stored registration was made with. A server
        compares the redirect URI exactly, so a second login must reuse it."""
        row = self._get(self.CLIENT)
        uris = row.get("redirect_uris") if isinstance(row, dict) else None
        try:
            port = urlsplit(str(uris[0])).port
        except (TypeError, IndexError, ValueError):
            return None
        return port if port and port > 1023 else None


@dataclass(frozen=True)
class Remote:
    """A streamable-HTTP server. `bearer` names the secret sent as
    `Authorization: Bearer …`; `oauth` holds a sign-in; `login` alone may open a
    browser."""
    url: str
    bearer: str = ""
    oauth: TokenStore | None = None
    login: bool = False


def public_url(url: str) -> str:
    """The URL, if its host resolves only to public addresses; else Unavailable.

    Checked on every connect, not only at `add`: a name can be repointed at the
    Mac's own network later. The SDK's client resolves again when it connects,
    so a server that answers DNS differently twice in a row is not stopped
    here (see docs/plans/2026-10-02-mcp-remote-design.md, decision 3).
    """
    from . import network
    host = urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise Unavailable("could not find that server's address") from None
    if not network.public_https_url(url, sorted({i[4][0] for i in infos})):
        raise Unavailable("that server is not on the public internet; Notron will not connect")
    return url


def _sdk():
    try:
        import mcp  # noqa: F401
        return mcp
    except ImportError:
        return None


def child_env(secrets: dict[str, str]) -> dict[str, str]:
    """The server's whole environment: essentials plus its own granted secrets.

    The SDK also merges its own short safe list (PATH, HOME, USER, SHELL, TERM,
    LOGNAME on macOS) under this. Nothing else from Notron's environment, such as
    the Nebius key, ever reaches a third-party process.
    """
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
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


def _run(target, secrets, work):
    """Open a session, run `work(session)` and close it. Tests replace this."""
    if isinstance(target, Remote):
        return _run_remote(target, secrets, work)
    argv = target
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=argv[0], args=list(argv[1:]), env=child_env(secrets))

    async def main():
        # A third-party server's stderr can echo the token it was handed; it
        # never reaches Notron's terminal or logs.
        with open(os.devnull, "w") as quiet, anyio.fail_after(TIMEOUT):
            async with stdio_client(params, errlog=quiet) as (r, w), \
                    ClientSession(r, w) as session:
                await session.initialize()
                return await work(session)
    return anyio.run(main)


def _loopback(port: int | None):
    """A one-shot listener on 127.0.0.1 for the sign-in redirect. Returns the
    bound socket; a remembered port that is now busy is plain words, because a
    new port would not match the registration."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port or 0))
    except OSError:
        sock.close()
        raise Unavailable(f"port {port} is busy; close what is using it and sign in again") from None
    sock.listen(1)
    return sock


def _await_redirect(sock, deadline: float) -> dict:
    """Serve one request on `sock` and hand back its query: code, state, iss."""
    while True:
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        try:
            conn, _ = sock.accept()
        except (TimeoutError, socket.timeout):
            raise Unavailable("sign-in was not finished in time") from None
        with conn:
            conn.settimeout(5)
            try:
                line = conn.recv(8192).split(b"\r\n", 1)[0].decode("latin-1")
            except OSError:
                continue
            parts = line.split(" ")
            try:
                query = parse_qs(urlsplit(parts[1]).query) if len(parts) >= 2 else {}
            except ValueError:
                query = {}  # a malformed local probe must not end the sign-in
            if "code" not in query and "error" not in query:
                conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                continue  # a favicon or a probe, not the redirect
            body = b"Signed in. You can close this tab and go back to the terminal."
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                         b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            return {k: v[0] for k, v in query.items()}


def _open_browser(url: str) -> None:
    import subprocess
    print(f"\n  Opening your browser to sign in. If it does not open, visit:\n  {url}\n")
    subprocess.run(["open", url], check=False)


def _auth(target: Remote, sock):
    """The SDK's OAuth provider, with the stored expiry restored and the browser
    reserved for `login`."""
    import anyio
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

    store = target.oauth

    class Provider(OAuthClientProvider):
        async def _initialize(self):
            await super()._initialize()
            self.context.token_expiry_time = store.expires_at

        async def _auth_flow(self, request):
            """Every request the sign-in makes on its own (metadata,
            registration, token, refresh) goes to a URL the *server* named, so
            each is held to the same public-https rule as the URL the user
            typed. Otherwise a registered server could point the Mac at a
            router or a cloud metadata address on the home network."""
            flow = super()._auth_flow(request)
            try:
                outgoing = await flow.__anext__()
                while True:
                    if outgoing is not request:
                        await anyio.to_thread.run_sync(_public_https, str(outgoing.url))
                    outgoing = await flow.asend((yield outgoing))
            except StopAsyncIteration:
                return

    async def redirect(url):
        if not target.login:
            raise NeedsLogin("needs sign-in")
        _public_https(url)
        _open_browser(url)

    async def callback():
        if sock is None:
            raise NeedsLogin("needs sign-in")
        deadline = time.monotonic() + LOGIN_TIMEOUT
        query = await anyio.to_thread.run_sync(_await_redirect, sock, deadline)
        if "error" in query:
            raise Unavailable("the server refused the sign-in")
        return AuthorizationCodeResult(code=query.get("code", ""), state=query.get("state"),
                                       iss=query.get("iss"))

    port = sock.getsockname()[1] if sock is not None else (store.redirect_port() or 8976)
    meta = OAuthClientMetadata(client_name="Notron", redirect_uris=[f"http://127.0.0.1:{port}/callback"],
                               grant_types=["authorization_code", "refresh_token"],
                               response_types=["code"], token_endpoint_auth_method="none")
    return Provider(target.url, meta, store, redirect_handler=redirect, callback_handler=callback)


def _public_https(url: str) -> str:
    from . import network
    if network._https_parts(url) is None:
        raise Unavailable("the server's sign-in points somewhere Notron will not go")
    return public_url(url)


def _find(exc: BaseException, kind, seen=None):
    """The first `kind` inside whatever anyio wrapped it in, if any."""
    seen = seen if seen is not None else set()
    if exc is None or id(exc) in seen:
        return None
    seen.add(id(exc))
    if isinstance(exc, kind):
        return exc
    for inner in getattr(exc, "exceptions", ()) or ():
        found = _find(inner, kind, seen)
        if found is not None:
            return found
    return _find(exc.__cause__ or exc.__context__, kind, seen)


def _needs_login(exc: BaseException) -> NeedsLogin | None:
    return _find(exc, NeedsLogin)


def _refusal(statuses) -> str | None:
    """Plain words for the HTTP answer that ended a session. The SDK reports a
    refused request as a bare "error response", which tells the user nothing
    about whether to fix the token, the URL, or wait."""
    for code in statuses[-1:]:
        if code in (401, 403):
            return f"the server refused Notron's sign-in (HTTP {code}); check the token or sign in again"
        if code == 404:
            return "nothing answered at that URL (HTTP 404); check it"
        if code == 429 or code >= 500:
            return f"the server is not answering right now (HTTP {code}); try again later"
    return None


def _quiet_sdk_logs() -> None:
    """The SDK logs a failed sign-in with a full traceback, and with no logging
    configured Python prints that to the terminal. Server text can ride along in
    those messages, so they go nowhere; the caller turns the failure into one
    line instead."""
    import logging
    log = logging.getLogger("mcp")
    if not any(isinstance(h, logging.NullHandler) for h in log.handlers):
        log.addHandler(logging.NullHandler())
    log.propagate = False


def _run_remote(target: Remote, secrets, work):
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    if target.oauth is not None and not target.login and not target.oauth.signed_in():
        # Before any request: otherwise the SDK registers Notron with the
        # server (and pins a redirect port) from a background call nobody is
        # watching, only to stop at the browser step anyway.
        raise NeedsLogin("needs sign-in")
    public_url(target.url)
    headers = {}
    if target.bearer:
        headers["Authorization"] = f"Bearer {secrets[target.bearer]}"
    sock = _loopback(target.oauth.redirect_port()) if (target.oauth and target.login) else None
    limit = LOGIN_TIMEOUT + TIMEOUT if target.login else TIMEOUT
    statuses: list[int] = []
    _quiet_sdk_logs()

    async def noted(response):
        statuses.append(response.status_code)

    async def main():
        auth = _auth(target, sock) if target.oauth is not None else None
        http = create_mcp_http_client(headers=headers, auth=auth)
        http.event_hooks["response"] = [noted]
        with anyio.fail_after(limit):
            async with http, \
                    streamable_http_client(target.url, http_client=http) as (r, w), \
                    ClientSession(r, w) as session:
                await session.initialize()
                return await work(session)
    try:
        return anyio.run(main)
    except BaseException as exc:
        found = _needs_login(exc)
        if found is not None:
            raise NeedsLogin(str(found)) from None
        own = _find(exc, Unavailable)
        if own is not None:
            # Notron's own words (a sign-in that timed out or was denied, an
            # address it refused) win over the last HTTP status seen.
            raise Unavailable(str(own)) from None
        refused = _refusal(statuses) if isinstance(exc, Exception) else None
        if refused:
            raise Unavailable(refused) from None
        raise
    finally:
        if sock is not None:
            sock.close()


def login(target: Remote) -> None:
    """Sign in once, interactively: opens the browser, waits for the redirect,
    stores the tokens through `target.oauth`."""
    _require_sdk()

    async def work(session):
        return None
    _run(Remote(target.url, target.bearer, target.oauth, login=True), {}, work)


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
