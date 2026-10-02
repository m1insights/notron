"""Remote MCP servers: a URL the user typed, a bearer secret or a browser sign-in.

No network: `mcp_client._run` is shut by conftest, DNS is faked per test, and
the sign-in redirect is served from a fake socket.
"""
import asyncio
import json
import socket

import pytest

from notron import channels, connectors, credentials, mcp_client

SEARCH = {"name": "search_issues", "description": "Search issues.",
          "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}},
                          "required": ["q"]},
          "annotations": {"readOnlyHint": True}}
GITHUB = "https://api.githubcopilot.com/mcp/"
VERCEL = "https://mcp.vercel.com"


class FakeRemote:
    def __init__(self):
        self.targets, self.secrets, self.calls = [], [], []
        self.fail = None

    def list_tools(self, target, secrets):
        self.targets.append(target)
        self.secrets.append(dict(secrets))
        if self.fail:
            raise self.fail
        return [dict(SEARCH)]

    def call_tool(self, target, secrets, name, arguments):
        self.calls.append((target, name, arguments))
        return "issue #1"


@pytest.fixture
def remote(monkeypatch, _task3_storage):
    fake = FakeRemote()
    monkeypatch.setattr(mcp_client, "list_tools", fake.list_tools)
    monkeypatch.setattr(mcp_client, "call_tool", fake.call_tool)
    return fake


def granted(*names):
    return channels.Channel("Synqology", "note-x", connectors=tuple(names))


# --- registering a URL ---------------------------------------------------------

def test_a_bearer_url_server_survives_reload_and_targets_a_remote(remote):
    connectors.add("github", url=GITHUB, auth="bearer", secrets=("GITHUB_TOKEN",))
    server = connectors.get("github")
    assert (server.url, server.auth, server.argv, server.secrets) == (GITHUB, "bearer", (), ("GITHUB_TOKEN",))
    target = connectors.target(server)
    assert isinstance(target, mcp_client.Remote) and target.bearer == "GITHUB_TOKEN"
    assert target.oauth is None and target.login is False


def test_a_local_server_row_has_no_url_keys_so_older_code_still_reads_it(remote):
    connectors.add("time", ("uvx", "mcp-server-time"))
    [row] = json.loads(connectors._path().read_text())["servers"]
    assert "url" not in row and connectors.target(connectors.get("time")) == ("uvx", "mcp-server-time")


@pytest.mark.parametrize("url,auth,secrets", [
    ("http://mcp.vercel.com", "oauth", ()),                     # not https
    ("https://user:pw@mcp.vercel.com", "oauth", ()),            # a login in the URL
    ("https://mcp.vercel.com/?token=abc", "oauth", ()),         # a query is where tokens hide
    ("https://mcp.vercel.com/#x", "oauth", ()),
    ("https://127.0.0.1/mcp", "none", ()),                      # the Mac itself
    ("https://10.0.0.5/mcp", "none", ()),                       # the home network
    ("https://printer.local/mcp", "none", ()),
    (VERCEL, "magic", ()),
    (GITHUB, "bearer", ()),                                     # bearer needs its one secret
    (VERCEL, "oauth", ("GITHUB_TOKEN",)),                       # only bearer sends a secret
])
def test_an_unsafe_or_malformed_url_is_refused_at_add(remote, url, auth, secrets):
    with pytest.raises(connectors.ConnectorError):
        connectors.add("x", url=url, auth=auth, secrets=secrets)
    assert connectors.load() == []


def test_a_command_and_a_url_together_are_refused(remote):
    with pytest.raises(connectors.ConnectorError, match="not both"):
        connectors.add("x", ("npx", "s"), url=VERCEL, auth="oauth")


def test_a_token_in_the_url_path_is_refused_and_not_echoed(remote):
    token = "ghp_" + "a" * 36
    with pytest.raises(connectors.ConnectorError) as refused:
        connectors.add("github", url=f"https://api.example.com/{token}/mcp", auth="none")
    assert token not in str(refused.value)


# --- calling ----------------------------------------------------------------------

def test_a_bearer_secret_is_handed_to_the_remote_not_an_environment(remote):
    credentials.provision_api_key("connector.github.GITHUB_TOKEN", "synthetic-gh-token")
    connectors.add("github", url=GITHUB, auth="bearer", secrets=("GITHUB_TOKEN",))
    connectors.approve("github", ["search_issues"])
    out = connectors.call(granted("github"), "github.search_issues", {"q": "ci"})
    assert out == "issue #1"
    [(target, name, args)] = remote.calls
    assert isinstance(target, mcp_client.Remote) and target.url == GITHUB
    assert remote.secrets[-1] == {"GITHUB_TOKEN": "synthetic-gh-token"}


def test_a_listener_call_that_needs_sign_in_is_one_line_not_a_browser(remote):
    connectors.add("vercel", url=VERCEL, auth="oauth")
    connectors.approve("vercel", ["search_issues"])
    remote.fail = mcp_client.NeedsLogin("needs sign-in")
    out = connectors.call(granted("vercel"), "vercel.search_issues", {"q": "x"})
    assert out == "vercel: needs sign-in — notron connect login vercel"
    assert remote.calls == [] and remote.targets[-1].login is False


def test_tools_on_a_signed_out_server_says_how_to_sign_in(remote):
    connectors.add("vercel", url=VERCEL, auth="oauth")
    remote.fail = mcp_client.NeedsLogin("needs sign-in")
    with pytest.raises(connectors.ConnectorError, match="notron connect login vercel"):
        connectors.discover("vercel")


def test_login_is_only_for_oauth_servers(remote):
    connectors.add("github", url=GITHUB, auth="bearer", secrets=("GITHUB_TOKEN",))
    with pytest.raises(connectors.ConnectorError, match="does not use a browser sign-in"):
        connectors.login("github")


def test_login_hands_the_oauth_store_to_the_client(remote, monkeypatch):
    seen = []
    monkeypatch.setattr(mcp_client, "login", seen.append)
    connectors.add("vercel", url=VERCEL, auth="oauth")
    connectors.login("Vercel")
    [target] = seen
    assert target.url == VERCEL and isinstance(target.oauth, mcp_client.TokenStore)


def test_removing_an_oauth_server_forgets_its_sign_in(remote, _task3_storage):
    """A later server registered under the same name must not inherit it."""
    connectors.add("vercel", url=VERCEL, auth="oauth")
    for item in ("OAUTH_TOKENS", "OAUTH_CLIENT"):
        credentials.store_connector_token(f"connector.vercel.{item}", b"e30=")
    connectors.remove("vercel")
    assert not [k for k in _task3_storage.values if k.startswith("connector.vercel.")]


def test_oauth_storage_is_limited_to_oauth_names(_task3_storage):
    with pytest.raises(credentials.CredentialUnavailable):
        credentials.store_connector_token("connector.github.GITHUB_TOKEN", b"x")
    with pytest.raises(credentials.CredentialUnavailable):
        credentials.store_connector_token("nebius-api-key", b"x")


# --- the client -------------------------------------------------------------------

def _store():
    box = {}
    return mcp_client.TokenStore(box.get, box.__setitem__), box


def test_a_stored_token_keeps_its_expiry_so_a_later_process_refreshes(monkeypatch):
    """The SDK only learns `expires_in` from a fresh response. Without the
    absolute expiry a reloaded token looks valid for ever, the call 401s, and
    the SDK answers with a browser sign-in in the listener instead of a refresh."""
    from mcp.shared.auth import OAuthToken
    monkeypatch.setattr(mcp_client.time, "time", lambda: 1000.0)
    store, box = _store()
    asyncio.run(store.set_tokens(OAuthToken(access_token="a", refresh_token="r", expires_in=60,
                                            scope="read write")))
    assert all(b" " not in v for v in box.values())
    later = mcp_client.TokenStore(box.get, box.__setitem__)
    token = asyncio.run(later.get_tokens())
    assert token.refresh_token == "r" and later.expires_at == 1060.0 - mcp_client.EXPIRY_MARGIN


def test_the_stored_registration_fixes_the_redirect_port():
    from mcp.shared.auth import OAuthClientInformationFull
    store, _ = _store()
    assert store.redirect_port() is None
    asyncio.run(store.set_client_info(OAuthClientInformationFull(
        client_id="c", redirect_uris=["http://127.0.0.1:53123/callback"])))
    assert store.redirect_port() == 53123


def test_unreadable_stored_sign_in_reads_as_signed_out():
    store = mcp_client.TokenStore(lambda k: b"not base64 json", lambda k, v: None)
    assert asyncio.run(store.get_tokens()) is None


def _dns(monkeypatch, *addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 443)) for a in addresses])


def test_a_name_that_resolves_to_the_home_network_is_refused(monkeypatch):
    _dns(monkeypatch, "140.82.112.5", "192.168.1.10")   # one bad address spoils the set
    with pytest.raises(mcp_client.Unavailable, match="not on the public internet"):
        mcp_client.public_url(GITHUB)
    _dns(monkeypatch, "140.82.112.5")
    assert mcp_client.public_url(GITHUB) == GITHUB


def test_the_address_check_runs_before_anything_is_sent(monkeypatch):
    _dns(monkeypatch, "127.0.0.1")
    real_run = mcp_client._run.original
    with pytest.raises(mcp_client.Unavailable, match="not on the public internet"):
        real_run(mcp_client.Remote(VERCEL), {}, None)


def test_a_background_oauth_call_never_opens_a_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(mcp_client, "_open_browser", opened.append)
    store, _ = _store()
    provider = mcp_client._auth(mcp_client.Remote(VERCEL, oauth=store), None)
    with pytest.raises(mcp_client.NeedsLogin):
        asyncio.run(provider.context.redirect_handler("https://vercel.com/oauth/authorize?x"))
    with pytest.raises(mcp_client.NeedsLogin):
        asyncio.run(provider.context.callback_handler())
    assert opened == []


def test_needs_login_is_found_inside_an_exception_group():
    group = BaseExceptionGroup("tg", [RuntimeError("x"), mcp_client.NeedsLogin("needs sign-in")])
    assert isinstance(mcp_client._needs_login(group), mcp_client.NeedsLogin)
    assert mcp_client._needs_login(RuntimeError("x")) is None


class _Conn:
    def __init__(self, request):
        self.request, self.sent = request, b""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def settimeout(self, t): pass
    def recv(self, n): return self.request
    def sendall(self, data): self.sent += data


class _Sock:
    def __init__(self, *requests):
        self.conns = [_Conn(r) for r in requests]
    def settimeout(self, t): pass
    def accept(self):
        if not self.conns:
            raise socket.timeout()
        return self.conns.pop(0), ("127.0.0.1", 1)


def test_the_sign_in_redirect_skips_stray_requests_and_returns_the_code():
    sock = _Sock(b"GET /favicon.ico HTTP/1.1\r\n\r\n",
                 b"GET /callback?code=abc&state=s1&iss=https%3A%2F%2Fvercel.com HTTP/1.1\r\n\r\n")
    query = mcp_client._await_redirect(sock, deadline=float("inf"))
    assert query == {"code": "abc", "state": "s1", "iss": "https://vercel.com"}


def test_an_unfinished_sign_in_gives_up_in_plain_words():
    with pytest.raises(mcp_client.Unavailable, match="not finished in time"):
        mcp_client._await_redirect(_Sock(), deadline=0)


@pytest.mark.parametrize("statuses,words", [
    ([401], "refused Notron's sign-in (HTTP 401)"),
    ([200, 403], "HTTP 403"),
    ([404], "nothing answered at that URL"),
    ([503], "not answering right now"),
    ([200], None),
])
def test_a_refused_request_is_named_in_plain_words(statuses, words):
    """Live, GitHub's server without a token came back as
    "could not list tools (ExceptionGroup)": the SDK hides the 401."""
    out = mcp_client._refusal(statuses)
    assert (out is None) if words is None else (words in out)


def test_sdk_tracebacks_never_reach_the_terminal():
    """Live, a background Vercel call printed the SDK's whole OAuth traceback
    above the one line Notron meant to show."""
    import logging
    mcp_client._quiet_sdk_logs()
    mcp_client._quiet_sdk_logs()
    log = logging.getLogger("mcp")
    assert log.propagate is False
    assert sum(isinstance(h, logging.NullHandler) for h in log.handlers) == 1


# --- review fixes (2026-10-02) ---------------------------------------------------

def test_a_never_signed_in_server_is_not_registered_with_from_the_background(monkeypatch):
    """Live, a background `discover` registered Notron with Vercel and pinned
    redirect port 8976 before stopping at the browser step."""
    import anyio
    monkeypatch.setattr(anyio, "run", lambda *a: pytest.fail("connected"))
    store, _ = _store()
    with pytest.raises(mcp_client.NeedsLogin):
        mcp_client._run.original(mcp_client.Remote(VERCEL, oauth=store), {}, None)


def test_notrons_own_words_survive_the_task_group_wrapper(monkeypatch):
    """A sign-in that timed out arrived wrapped in an ExceptionGroup and was
    reported as "refused (HTTP 401)" from the first request of every OAuth flow."""
    import anyio
    _dns(monkeypatch, "76.76.21.21")

    def run(main):
        raise BaseExceptionGroup("tg", [ExceptionGroup("tg", [
            mcp_client.Unavailable("sign-in was not finished in time")])])
    monkeypatch.setattr(anyio, "run", run)
    with pytest.raises(mcp_client.Unavailable, match="not finished in time"):
        mcp_client._run.original(mcp_client.Remote(VERCEL), {}, None)


def test_sign_in_requests_to_a_private_address_are_refused(monkeypatch):
    """The server names its own sign-in URLs; one pointing at the home network
    or a cloud metadata address must never be fetched."""
    import httpx2
    from mcp.client.auth import OAuthClientProvider
    _dns(monkeypatch, "76.76.21.21")
    original = httpx2.Request("POST", VERCEL)

    async def flow(self, request):
        yield httpx2.Request("GET", "https://vercel.com/.well-known/oauth-authorization-server")
        yield httpx2.Request("POST", "http://169.254.169.254/register")
    monkeypatch.setattr(OAuthClientProvider, "_auth_flow", flow)
    store, _ = _store()
    provider = mcp_client._auth(mcp_client.Remote(VERCEL, oauth=store), None)

    async def drive():
        gen = provider._auth_flow(original)
        await gen.__anext__()                      # public https: allowed
        await gen.asend(httpx2.Response(200))      # the next one is not
    with pytest.raises(mcp_client.Unavailable, match="will not go"):
        asyncio.run(drive())


def test_a_corrupt_stored_token_reads_as_signed_out():
    import base64
    bad = base64.urlsafe_b64encode(json.dumps({"token": {"nope": 1}}).encode())
    store = mcp_client.TokenStore(lambda k: bad, lambda k, v: None)
    assert asyncio.run(store.get_tokens()) is None


def test_a_malformed_probe_does_not_end_the_sign_in():
    sock = _Sock(b"GET http://[::1 HTTP/1.1\r\n\r\n", b"GET /callback?code=c&state=s HTTP/1.1\r\n\r\n")
    assert mcp_client._await_redirect(sock, deadline=float("inf"))["code"] == "c"
