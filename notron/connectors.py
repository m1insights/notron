"""Connectors — MCP servers the user registered, and the tools they approved.

What a connector may do is decided here, in plain code, never by a note or by
the model. The user types the server's argv, or its https URL, at the terminal
(`add`; a URL may need a `login` first), Notron
lists its tools (`discover`), and the user approves them one by one
(`approve`). Nemotron then sees approved tools by name (`menu_for`) and proposes
arguments; `call` checks the channel grant, the pinned schema and the arguments
before anything leaves the Mac.

v1 is read-only: only a tool the server annotates `readOnlyHint: true` can be
approved. That annotation is the server's own claim, which is why approval is
also the user's, tool by tool.

An approval pins a digest of everything the server said about the tool. A
server can change a tool's description or schema at any time after it was
approved (the MCP "rug pull"), so every call re-lists the tools first and a
tool that no longer matches loses its approval until the user approves again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import mcp_client, paths, privacy, schema
from .credentials import CredentialUnavailable
from .outbound import Passage, prepare_outbound
from .policy import PolicyError
from .securestore import StorageError

#: Stricter than `channels.NAME`: the model addresses a tool as `server.tool`,
#: and a Keychain item as `connector.<server>.<VAR>`, so a space or a dot in the
#: server name would make both ambiguous.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,39}$")
#: The secret names a server may be handed: environment-variable shaped.
VAR = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
#: Tool names are server-written and go into the model's menu verbatim. One
#: with a newline in it could forge a second menu line, so it is never approved.
TOOL = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$")
#: A bare command name, resolved on PATH, the way the user typed it.
BARE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]*$")
#: Property names are server-written too, and they reach the menu verbatim
#: (outside the description cap), so they are held to identifier shape, a count
#: and a rendered length. "IGNORE ALL PRIOR INSTRUCTIONS" is a valid JSON key.
PROPERTY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
MAX_PROPERTIES = 20
MAX_DESCRIPTION = 160
MAX_SHAPE = 600
#: How a remote server is signed in to. A local server is always "none": its
#: secrets go into its environment instead.
AUTHS = ("none", "bearer", "oauth")

CHANGED = "changed since you approved it — run notron connect approve {server}"


class ConnectorError(ValueError):
    pass


@dataclass(frozen=True)
class ApprovedTool:
    name: str
    description: str
    schema: dict
    digest: str


@dataclass(frozen=True)
class Server:
    name: str
    argv: tuple[str, ...]
    secrets: tuple[str, ...] = ()
    tools: dict[str, ApprovedTool] = field(default_factory=dict)
    #: Tools whose approval was withdrawn because the server changed them.
    #: Kept so the refusal says why, and so a server that changes the tool back
    #: does not quietly regain it: only the user's approval does that.
    changed: tuple[str, ...] = ()
    #: A remote server: an https URL instead of an argv, never both.
    url: str = ""
    auth: str = "none"
    #: The `PRESETS` entry this server was installed from, or "". What a preset
    #: may do beyond an ordinary server lives in code, never in this file.
    preset: str = ""


@dataclass(frozen=True)
class Offer:
    name: str
    description: str
    schema: dict
    annotations: dict
    approvable: bool
    why: str = ""


@dataclass(frozen=True)
class Preset:
    """An official server Notron knows how to run, and what it may do there.

    Plain code, like the Guard: a note, the model or a server can never make
    one. `binds` names arguments the model never chooses: code fills them from
    the channel (`repo` → its repository; `owner`/`name` → its GitHub slug) so a
    request cannot point a tool at another project. `vouched` names tools Notron
    itself knows are reads, for a server that never says so.
    """
    name: str
    argv: tuple[str, ...]
    grant: str                          # the channel switch that reaches it
    needs: str = ""                     # "repo" | "github" | ""
    secrets: tuple[str, ...] = ()
    optional_secrets: tuple[str, ...] = ()
    binds: tuple[tuple[str, str], ...] = ()
    #: Appended with the channel's repository when a call runs in one, so the
    #: server itself refuses any other path (`outside the allowed repository`).
    repo_flag: str = ""
    tools: tuple[str, ...] = ()
    vouched: tuple[str, ...] = ()
    #: False: code calls it (the researcher); Nemotron never picks it from a menu.
    menu: bool = True
    #: (tool, argument) pairs holding free search text the server only *prefixes*
    #: with the bound repository. Scope qualifiers in them are refused (`SCOPE`).
    scoped_queries: tuple[tuple[str, str], ...] = ()
    install: str = ""                   # how to get the binary, said when it is missing


#: Measured against the official servers on 2026-10-02
#: (`docs/plans/2026-10-02-mcp-replace-design.md`). GitHub's `list_issues` is
#: left out on purpose: its `field_filters` is a list of objects, which v1 will
#: not half-check; `search_issues` answers the same questions. Versions are pinned where
#: the launcher can pin; Homebrew's github-mcp-server is held by the approval
#: digest instead, like every other server.
PRESETS = {p.name: p for p in (
    Preset("git", ("uvx", "mcp-server-git==2026.8.18"), "read", needs="repo",
           binds=(("repo_path", "repo"),), repo_flag="--repository",
           tools=("git_status", "git_log", "git_branch", "git_diff_unstaged", "git_diff", "git_show"),
           install="install uv: https://docs.astral.sh/uv/"),
    Preset("github", ("github-mcp-server", "stdio", "--read-only",
                      "--toolsets", "repos,issues,pull_requests,actions"), "read", needs="github",
           secrets=("GITHUB_PERSONAL_ACCESS_TOKEN",),
           binds=(("owner", "owner"), ("repo", "name")),
           tools=("list_pull_requests", "pull_request_read", "search_issues", "issue_read",
                  "list_commits", "actions_list"),
           # github-mcp-server builds `repo:owner/name <query>`, and GitHub ORs
           # repeated scope qualifiers: `repo:other/private token` in the query
           # would search a repository the channel never named.
           scoped_queries=(("search_issues", "query"),),
           install="brew install github-mcp-server"),
    # tavily-mcp sets no annotations at all, so v1 would call every tool "changes
    # things". A search changes nothing; Notron says so for that one tool, and
    # only while the argv is exactly this pinned one. It runs keyless; a key is
    # optional (`--secret TAVILY_API_KEY`).
    Preset("tavily", ("npx", "-y", "tavily-mcp@0.2.22"), "research",
           optional_secrets=("TAVILY_API_KEY",), tools=("tavily_search",),
           vouched=("tavily_search",), menu=False,
           install="install Node.js (for npx): https://nodejs.org"),
)}


#: A search qualifier that widens or moves the scope. Matched anywhere, with or
#: without a leading `-` or quotes; a false refusal costs one search.
SCOPE = re.compile(r"(?i)(?:^|[^A-Za-z0-9_])(?:repo|org|user|owner)\s*:")


def preset_of(server: Server) -> Preset | None:
    """The preset behind a server, only while it still runs the preset's argv.

    The binary may be recorded as an absolute path (`install_preset`), so the
    first word compares by name; everything after it must match exactly. A hand
    edit to the version or the flags is an ordinary server again.
    """
    p = PRESETS.get(server.preset)
    if p is None or not server.argv:
        return None
    if Path(server.argv[0]).name != p.argv[0] or tuple(server.argv[1:]) != p.argv[1:]:
        return None
    return p


def _binds(server: Server) -> dict[str, str]:
    p = preset_of(server)
    return dict(p.binds) if p else {}


def _toplevel(repo: str) -> str:
    """The repository that holds `repo`: itself, or the nearest folder above with
    `.git`. mcp-server-git refuses to start on a subfolder, and Synqology lives
    inside the developer's whole `~/Dev` monorepo."""
    if not repo:
        return ""
    here = Path(repo).expanduser()
    for p in (here, *here.parents):
        if (p / ".git").exists():
            return str(p)
    return ""


def _channel_value(field: str, channel) -> str:
    if field == "repo":
        return _toplevel(getattr(channel, "repo", "") or "")
    owner, _, name = (getattr(channel, "github", "") or "").partition("/")
    return {"owner": owner, "name": name}.get(field, "")


def _bound(server: Server, channel) -> dict[str, str] | None:
    """Values for the server's bound arguments, or None when the channel lacks one."""
    out = {arg: _channel_value(field, channel) for arg, field in _binds(server).items()}
    return None if any(not v for v in out.values()) else out


def _spawn(server: Server, channel=None) -> tuple[str, ...]:
    """What a call connects to: a remote `target`, or the registered argv scoped
    to the channel's repository."""
    if server.url:
        return target(server)
    p = preset_of(server)
    if p and p.repo_flag and channel is not None:
        repo = _channel_value("repo", channel)
        if repo:
            return (*server.argv, p.repo_flag, repo)
    return server.argv


def qualified(server: str, tool: str) -> str:
    return f"{server}.{tool}"


def digest(tool: dict) -> str:
    """Everything the server said about a tool, as one value to compare later.

    It pins what the server *says*, not what it *does*: the listing and the call
    are separate requests (one process each), and nothing stops a server from
    behaving differently from its description. The check catches a changed
    promise, which is why the output stays untrusted data either way.
    """
    pinned = {k: tool.get(k) for k in ("name", "description", "inputSchema", "annotations")}
    return hashlib.sha256(json.dumps(pinned, sort_keys=True).encode()).hexdigest()


def _path() -> Path:
    # Resolved at call time, not import, so NOTRON_DATA_DIR always wins.
    return paths.data_dir() / "connectors.json"


def _damaged() -> ConnectorError:
    return ConnectorError("The connector registry is unreadable; fix or remove it.")


def _validate(s: Server) -> Server:
    if not isinstance(s.name, str) or not NAME.match(s.name):
        raise ConnectorError("A connector name is letters, numbers and dashes, up to 40.")
    if not all(isinstance(v, str) and VAR.match(v) for v in s.secrets):
        raise ConnectorError("A secret name looks like GITHUB_TOKEN.")
    if s.url or s.auth != "none":
        return _validate_remote(s)
    if (not s.argv or not all(isinstance(a, str) and a for a in s.argv)
            or not (BARE.match(s.argv[0]) or Path(s.argv[0]).is_absolute())):
        # A relative path would mean whatever folder she happened to start in.
        raise ConnectorError("The server command must be a bare command or an absolute path.")
    return s


def _validate_remote(s: Server) -> Server:
    """A URL the user typed: https to a public-looking host, nothing in it that
    could carry a token (no user:password, no query, no fragment). Whether the
    host really resolves to the public internet is asked on every connect
    (`mcp_client.public_url`), because that answer can change."""
    from . import network
    if s.argv:
        raise ConnectorError("A connector is a command or a URL, not both.")
    if (not isinstance(s.url, str) or network._https_parts(s.url) is None
            or "?" in s.url or "#" in s.url or len(s.url) > 512):
        raise ConnectorError("The server URL must be https://, with no login, query or #fragment, "
                             "and not a local or private address.")
    if s.auth not in AUTHS:
        raise ConnectorError("Sign-in is none, bearer or oauth.")
    if s.auth == "bearer" and len(s.secrets) != 1:
        raise ConnectorError("A bearer connector has exactly one secret: --bearer GITHUB_TOKEN.")
    if s.auth != "bearer" and s.secrets:
        raise ConnectorError("Only --bearer sends a secret to a remote server.")
    return s


def target(server: Server):
    """What `mcp_client` connects to: the argv, or a `Remote` for a URL."""
    if not server.url:
        return server.argv
    store = _oauth_store(server) if server.auth == "oauth" else None
    return mcp_client.Remote(server.url, server.secrets[0] if server.auth == "bearer" else "", store)


def _oauth_store(server: Server) -> "mcp_client.TokenStore":
    from . import credentials

    def key(item):
        return f"connector.{server.name}.{item}"

    return mcp_client.TokenStore(lambda item: credentials.get(key(item)),
                                 lambda item, value: credentials.store_connector_token(key(item), value))


def load() -> list[Server]:
    """Every registered server. A damaged file is an error, never an empty list:
    an empty list would read as "no connectors" and quietly drop every grant."""
    try:
        raw = json.loads(_path().read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise _damaged() from exc
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("servers"), list):
        raise _damaged()
    out = []
    try:
        for row in raw["servers"]:
            tools = {n: ApprovedTool(n, t["description"], t["schema"], t["digest"])
                     for n, t in row.get("tools", {}).items()}
            if not all(isinstance(t.description, str) and isinstance(t.schema, dict)
                       and isinstance(t.digest, str) for t in tools.values()):
                raise _damaged()
            out.append(_validate(Server(row["name"], tuple(row.get("argv", ())),
                                        tuple(row.get("secrets", ())), tools,
                                        tuple(row.get("changed", ())),
                                        url=row.get("url", ""), auth=row.get("auth", "none"),
                                        preset=str(row.get("preset", "")))))
    except (KeyError, TypeError, AttributeError) as exc:
        raise _damaged() from exc
    return out


def _save(servers: list[Server]) -> None:
    from .persistence import atomic_write_json
    from .securestore import private_directory
    path = _path()
    private_directory(path.parent)
    atomic_write_json(path, {"version": 1, "servers": [
        {"name": s.name, "argv": list(s.argv), "secrets": list(s.secrets),
         "tools": {n: {"description": t.description, "schema": t.schema, "digest": t.digest}
                   for n, t in s.tools.items()},
         "changed": list(s.changed), "preset": s.preset,
         **({"url": s.url, "auth": s.auth} if s.url else {})}
        for s in servers]})


def get(name: str) -> Server | None:
    return next((s for s in load() if s.name.lower() == str(name).lower()), None)


def _require(name: str) -> Server:
    found = get(name)
    if found is None:
        raise ConnectorError(f"No connector called {name}.")
    return found


def add(name: str, argv=(), secrets=(), *, url: str = "", auth: str = "none",
        preset: str = "") -> Server:
    """Register a server. Lists nothing and approves nothing: running it at all
    waits for `discover`, which the user asks for separately."""
    server = _validate(Server(str(name).strip(), tuple(argv), tuple(secrets), url=url, auth=auth,
                              preset=preset))
    if server.url and privacy.contains_secret(server.url):
        raise ConnectorError("That URL holds something that looks like a token. Register it with "
                             "--bearer VAR instead, then: notron connect secret <server> VAR")
    if any(privacy.contains_secret(a) for a in server.argv):
        # argv is visible to every process on the Mac, lands in shell history,
        # and would sit in connectors.json in the clear. The value is never
        # echoed back: this message is printed to a terminal.
        raise ConnectorError("That command holds something that looks like a token. Register it with "
                             "--secret VAR instead, then: notron connect secret <server> VAR")
    existing = load()
    if any(s.name.lower() == server.name.lower() for s in existing):
        raise ConnectorError(f"There is already a connector called {server.name}.")
    _save([*existing, server])
    return server


def remove(name: str) -> Server:
    """Unregister a server and forget its secrets.

    Secrets first: a token left behind would be handed, silently, to a
    different server later registered under the same name. A locked Keychain
    raises here and the server stays registered, so the user can retry; it
    never reads as "nothing to forget".
    """
    from . import credentials
    existing = load()
    gone = _require(name)
    for var in (*gone.secrets, *((mcp_client.TokenStore.TOKENS, mcp_client.TokenStore.CLIENT)
                                 if gone.auth == "oauth" else ())):
        credentials.forget_api_key(f"connector.{gone.name}.{var}")  # missing is fine
    _save([s for s in existing if s.name != gone.name])
    return gone


class MissingSecret(ConnectorError):
    pass


def _secrets(server: Server) -> dict[str, str]:
    """The server's own secrets, from the Keychain, by the names it was given.

    A missing one is a plain line that says how to set it. A locked or
    unavailable Keychain is not "missing": `CredentialUnavailable` propagates,
    because only one of those two is fixed by pasting a token.
    """
    from . import credentials
    out = {}
    for var in server.secrets:
        value = credentials.get(f"connector.{server.name}.{var}")
        if value is None:
            raise MissingSecret(f"{server.name}: needs {var} — "
                                f"notron connect secret {server.name} {var}")
        try:
            out[var] = value.decode("utf-8")
        except UnicodeDecodeError:
            raise CredentialUnavailable("Keychain unavailable; protected processing paused.") from None
    return out


def _safe_names(params: dict) -> bool:
    props = params.get("properties", {})
    if len(props) > MAX_PROPERTIES or not all(isinstance(k, str) and PROPERTY.match(k) for k in props):
        return False
    return all(_safe_names(schema.effective(v)) for v in props.values()
               if schema.effective(v).get("type") == "object")


def _offer(tool: dict, vouched=()) -> Offer:
    name, desc = tool.get("name"), tool.get("description") or ""
    params, notes = tool.get("inputSchema"), tool.get("annotations") or {}
    if not isinstance(name, str) or not TOOL.match(name) or not isinstance(desc, str):
        why = "name Notron cannot show safely"
    elif not isinstance(notes, dict) or (notes.get("readOnlyHint") is not True
                                         and name not in vouched):
        why = "changes things: v1 is read-only"
    elif not isinstance(params, dict) or not schema.supported(params):
        why = "arguments too complex for v1"
    elif not _safe_names(params):
        # After `supported`, so every nested schema is already a well-formed dict.
        why = "argument names Notron cannot show safely"
    else:
        why = ""
    return Offer(str(name), str(desc), params if isinstance(params, dict) else {},
                 notes if isinstance(notes, dict) else {}, not why, why)


def _vouched(server: Server) -> tuple[str, ...]:
    p = preset_of(server)
    return p.vouched if p else ()


def _list(server: Server) -> list[dict]:
    return mcp_client.list_tools(target(server), _secrets(server))


def login(name: str) -> Server:
    """Sign in to an OAuth server, interactively. The one path that may open a
    browser; everything else answers "needs sign-in" instead."""
    server = _require(name)
    if server.auth != "oauth":
        raise ConnectorError(f"{server.name} does not use a browser sign-in.")
    try:
        mcp_client.login(target(server))
    except (CredentialUnavailable, StorageError, PolicyError, ConnectorError):
        raise
    except mcp_client.Unavailable as exc:
        raise ConnectorError(f"{server.name}: {exc}") from None
    except Exception as exc:
        raise ConnectorError(f"{server.name}: sign-in failed ({type(exc).__name__})") from None
    return server


def _needs_login(server: Server) -> str:
    return f"{server.name}: needs sign-in — notron connect login {server.name}"


def _listed(server: Server) -> list[dict]:
    """The server's tools, or plain words for the CLI. A raw exception (an
    ExceptionGroup, an OSError carrying the server's own text) never reaches the
    terminal: that text is the server's, and can echo what it was handed."""
    try:
        return [t for t in _list(server) if isinstance(t, dict)]
    except (CredentialUnavailable, StorageError, PolicyError, ConnectorError):
        raise
    except mcp_client.NeedsLogin:
        raise ConnectorError(_needs_login(server)) from None
    except mcp_client.Unavailable as exc:
        raise ConnectorError(f"{server.name}: {exc}" if server.url else str(exc)) from None
    except Exception as exc:
        raise ConnectorError(f"{server.name}: could not list tools ({type(exc).__name__})") from None


def discover(name: str) -> list[Offer]:
    """Ask the server for its tools and say which of them v1 could approve."""
    server = _require(name)
    return [_offer(t, _vouched(server)) for t in _listed(server)]


def approve(name: str, tool_names) -> list[str]:
    """Approve tools from a fresh listing, all or none.

    Fresh, not from an earlier `discover`: the digest must pin what the server
    says now, not what it said when the user was reading the list.
    """
    server = _require(name)
    listed = {}
    for t in _listed(server):
        listed.setdefault(t.get("name"), t)
    wanted = list(dict.fromkeys(tool_names))
    for n in wanted:
        if n not in listed:
            raise ConnectorError(f"{server.name} has no tool called {n}.")
        offer = _offer(listed[n], _vouched(server))
        if not offer.approvable:
            raise ConnectorError(f"{server.name}.{n} cannot be approved: {offer.why}.")
    tools = dict(server.tools)
    for n in wanted:
        o = _offer(listed[n], _vouched(server))
        tools[n] = ApprovedTool(n, o.description, o.schema, digest(listed[n]))
    new = replace(server, tools=tools, changed=tuple(c for c in server.changed if c not in wanted))
    _save([new if s.name == server.name else s for s in load()])
    return wanted


def _granted(channel) -> list[Server]:
    """The servers this channel reaches: those granted by name, and the presets
    its own switches cover (`read` → git and GitHub, `research` → web search).

    `Channel.connectors` is checked against this registry when granted, not
    when channels load; a name that has since left the registry matches
    nothing here and grants nothing. A preset whose channel value is missing (no
    repository, no GitHub slug) is not reached at all.
    """
    names = {str(n).lower() for n in channel.connectors}
    allow = set(getattr(channel, "allow", ()))
    out = []
    for s in load():
        p = preset_of(s)
        if s.name.lower() in names or (p is not None and p.grant in allow):
            if _bound(s, channel) is not None:
                out.append(s)
    return out


def _shape(params: dict, hidden=()) -> str:
    required = set(params.get("required", ()))

    def kind(v):
        t = schema.effective(v).get("type", "any")
        return f"{t} or null" if schema.nullable(v) else t
    text = json.dumps({k: f"{kind(v)}{' (required)' if k in required else ''}"
                       for k, v in params.get("properties", {}).items() if k not in hidden})
    return text if len(text) <= MAX_SHAPE else text[:MAX_SHAPE] + " …"


def _flat(text: str) -> str:
    """One line, no invisible characters. Zero-width and bidi controls (Unicode
    category Cf) let a description read differently to the model than to a
    person reviewing the approval."""
    visible = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(visible.split())[:MAX_DESCRIPTION]


def offered(channel) -> dict[str, str]:
    """Each tool Nemotron may call in this channel, by qualified name, with its
    menu line. One registry read gives `project` both the menu and the names it
    holds a decision to, so the two can never disagree.

    The description is server-written text: flattened to one line and capped,
    because it lands in the passage the prompt already calls data, and a
    multi-line description is how a hostile server would try to look like the
    prompt itself.
    """
    out = {}
    for s in _granted(channel):
        p = preset_of(s)
        if p is not None and not p.menu:
            continue
        for t in s.tools.values():
            if t.name in s.changed:
                continue
            name = qualified(s.name, t.name)
            out[name] = f"- {name}: {_flat(t.description)} args: {_shape(t.schema, _binds(s))}"
    return out


def menu_for(channel) -> list[str]:
    """One line per tool Nemotron may call in this channel (see `offered`)."""
    return list(offered(channel).values())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _disable(server: Server, tool: str) -> None:
    tools = {n: t for n, t in server.tools.items() if n != tool}
    new = replace(server, tools=tools, changed=tuple(dict.fromkeys((*server.changed, tool))))
    try:
        _save([new if s.name == server.name else s for s in load()])
    except (OSError, ConnectorError):
        pass  # the call is refused either way; the next one re-checks


def _unbound(params: dict, bound) -> dict:
    """The schema the model's own arguments are checked against: bound ones gone."""
    if not bound:
        return params
    return {**params, "properties": {k: v for k, v in params.get("properties", {}).items()
                                     if k not in bound},
            "required": [r for r in params.get("required", []) if r not in bound]}


def _checked(server: Server, approved: ApprovedTool, arguments, label: str, channel=None) -> str:
    """Check, redact, bind, re-verify and run one approved tool. Every refusal is a line.

    Bound arguments are removed from whatever the model sent and set by code
    after redaction: redaction must never rewrite a repository path, and the
    model must never choose one.
    """
    if not isinstance(arguments, dict):
        return f"{label}: refused — arguments: wrong type"
    bound = (_bound(server, channel) if channel is not None else None) or {}
    if channel is not None and _binds(server) and not bound:
        return f"{label}: not available in this channel"
    # Bound per tool: a tool without an `owner` argument must not be handed one
    # (the schema is closed, so it would be refused as unexpected).
    bound = {k: v for k, v in bound.items() if k in approved.schema.get("properties", {})}
    p = preset_of(server)
    for tool, arg in (p.scoped_queries if p else ()):
        value = arguments.get(arg) if tool == approved.name else None
        if isinstance(value, str) and SCOPE.search(value):
            return f"{label}: refused — {arg}: names a repository; this channel searches its own"
    arguments = {k: v for k, v in arguments.items() if k not in bound}
    loose = _unbound(approved.schema, bound)
    errors = schema.validate(loose, arguments)
    if errors:
        # Errors name the field and the rule, never the value.
        return f"{label}: refused — " + "; ".join(errors[:5])
    if any(privacy.contains_secret(s) for s in _strings(arguments)):
        return f"{label}: blocked: arguments looked like a credential"
    [safe] = prepare_outbound("connector", [Passage(json.dumps(arguments), "model")])
    try:
        arguments = json.loads(safe)
    except ValueError:
        return f"{label}: blocked: arguments looked like a credential"
    arguments = {**arguments, **bound}
    # Redaction can turn a valid value into one the schema refuses (an enum
    # member, a minimum length); what is sent must pass the same check.
    errors = schema.validate(approved.schema, arguments)
    if errors:
        return f"{label}: refused — " + "; ".join(errors[:5])
    secrets = _secrets(server)
    argv = _spawn(server, channel)
    live = next((t for t in mcp_client.list_tools(argv, secrets)
                 if isinstance(t, dict) and t.get("name") == approved.name), None)
    if live is None or digest(live) != approved.digest:
        _disable(server, approved.name)
        return f"{label}: " + CHANGED.format(server=server.name)
    return mcp_client.call_tool(argv, secrets, approved.name, arguments)


def call(channel, name, arguments) -> str:
    """Run one approved tool for this channel, or say in one line why not.

    Every failure is a line, never an exception into the graph, except the
    three that pause protected processing everywhere else in `nodes.project`.
    """
    label = name if isinstance(name, str) else "connector"
    server_name, _, tool = label.partition(".")
    try:
        if not server_name or not tool:
            return f"{label}: not a connector tool"
        server = next((s for s in _granted(channel) if s.name.lower() == server_name.lower()), None)
        if server is None:
            return f"{label}: not granted to this channel"
        if tool in server.changed:
            return f"{label}: " + CHANGED.format(server=server.name)
        approved = server.tools.get(tool)
        if approved is None:
            return f"{label}: not approved"
        return _checked(server, approved, arguments, label, channel)
    except (CredentialUnavailable, StorageError, PolicyError):
        raise
    except MissingSecret as exc:
        return str(exc)
    except mcp_client.NeedsLogin:
        return _needs_login(server)
    except (mcp_client.Unavailable, ConnectorError) as exc:
        return f"{label}: {exc}"
    except Exception as exc:
        # Startup, handshake and the 30 s timeout surface here raw (OSError,
        # TimeoutError, an ExceptionGroup from anyio). Only the type is shown:
        # a server's message can carry anything, including what it was handed.
        return f"{label}: could not run ({type(exc).__name__})"


# --------------------------------------------------------------- presets

def _resolve(command: str) -> str:
    """The command as an absolute path the listener can start, or "" if missing.

    A launchd job has a short PATH, and a version manager's shim folder (fnm's
    `fnm_multishells/<pid>`) dies with the shell that made it, so the folder is
    resolved to where the binary really lives; the file name is kept, because
    `preset_of` compares by it.
    """
    found = shutil.which(command)
    if not found:
        return ""
    return str(Path(os.path.realpath(os.path.dirname(found))) / os.path.basename(found))


def install_preset(name: str, *, with_key: bool = False) -> tuple[Server, list[str]]:
    """Register an official server from `PRESETS` and approve its default tools.

    Returns the server and the tools approved. A server that needs a secret not
    yet stored is registered and approves nothing: `MissingSecret` says which
    command stores it, and running the preset again finishes the approval.
    """
    p = PRESETS.get(str(name).lower())
    if p is None:
        raise ConnectorError(f"No preset called {name}. Presets: {', '.join(PRESETS)}.")
    existing = get(p.name)
    if existing is not None and preset_of(existing) is None:
        raise ConnectorError(f"There is already a connector called {p.name}; "
                             f"remove it first: notron connect remove {p.name}")
    if existing is not None and with_key:
        missing = tuple(v for v in p.optional_secrets if v not in existing.secrets)
        if missing:
            existing = replace(existing, secrets=(*existing.secrets, *missing))
            _save([existing if s.name == existing.name else s for s in load()])
    stale = (existing is not None and Path(existing.argv[0]).is_absolute()
             and not Path(existing.argv[0]).exists())
    if existing is None or stale:
        exe = _resolve(p.argv[0])
        if not exe:
            raise ConnectorError(f"{p.argv[0]} is not installed: {p.install}")
        if stale:
            # The binary moved (a Node upgrade under fnm, a Homebrew reinstall).
            # Same preset, same secrets and approvals; only where it lives changes.
            existing = replace(existing, argv=(exe, *p.argv[1:]))
            _save([existing if s.name == existing.name else s for s in load()])
        else:
            secrets = p.secrets + (p.optional_secrets if with_key else ())
            existing = add(p.name, (exe, *p.argv[1:]), secrets, preset=p.name)
    return existing, approve(p.name, p.tools)


def web_search(query: str, limit: int) -> str:
    """Run the `tavily` preset's search for the researcher, through every check a
    channel call gets. Raises ConnectorError when web search is not set up."""
    server = next((s for s in load() if s.preset == "tavily" and preset_of(s)), None)
    if server is None:
        raise ConnectorError("web search is not set up — notron connect preset tavily")
    approved = server.tools.get("tavily_search")
    if approved is None or "tavily_search" in server.changed:
        raise ConnectorError("web search is not approved — notron connect preset tavily")
    label = "tavily.tavily_search"
    out = _checked(server, approved, {"query": query, "max_results": limit}, label)
    if out.startswith(label + ":"):
        # A refusal line, not results: say why instead of "0 sources".
        raise ConnectorError(out[len(label) + 1:].strip())
    return out


def web_ready() -> bool:
    """True when the researcher could search: the preset is installed and approved."""
    try:
        server = next((s for s in load() if s.preset == "tavily" and preset_of(s)), None)
    except ConnectorError:
        return False
    return (server is not None and "tavily_search" in server.tools
            and "tavily_search" not in server.changed)
