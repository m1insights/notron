"""Connectors — MCP servers the user registered, and the tools they approved.

What a connector may do is decided here, in plain code, never by a note or by
the model. The user types the server's argv at the terminal (`add`), Notron
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
import re
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


@dataclass(frozen=True)
class Offer:
    name: str
    description: str
    schema: dict
    annotations: dict
    approvable: bool
    why: str = ""


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
    if (not s.argv or not all(isinstance(a, str) and a for a in s.argv)
            or not (BARE.match(s.argv[0]) or Path(s.argv[0]).is_absolute())):
        # A relative path would mean whatever folder she happened to start in.
        raise ConnectorError("The server command must be a bare command or an absolute path.")
    if not all(isinstance(v, str) and VAR.match(v) for v in s.secrets):
        raise ConnectorError("A secret name looks like GITHUB_TOKEN.")
    return s


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
            out.append(_validate(Server(row["name"], tuple(row["argv"]),
                                        tuple(row.get("secrets", ())), tools,
                                        tuple(row.get("changed", ())))))
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
         "changed": list(s.changed)}
        for s in servers]})


def get(name: str) -> Server | None:
    return next((s for s in load() if s.name.lower() == str(name).lower()), None)


def _require(name: str) -> Server:
    found = get(name)
    if found is None:
        raise ConnectorError(f"No connector called {name}.")
    return found


def add(name: str, argv, secrets=()) -> Server:
    """Register a server. Lists nothing and approves nothing: running it at all
    waits for `discover`, which the user asks for separately."""
    server = _validate(Server(str(name).strip(), tuple(argv), tuple(secrets)))
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
    for var in gone.secrets:
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
    return all(_safe_names(v) for v in props.values() if v.get("type") == "object")


def _offer(tool: dict) -> Offer:
    name, desc = tool.get("name"), tool.get("description") or ""
    params, notes = tool.get("inputSchema"), tool.get("annotations") or {}
    if not isinstance(name, str) or not TOOL.match(name) or not isinstance(desc, str):
        why = "name Notron cannot show safely"
    elif not isinstance(notes, dict) or notes.get("readOnlyHint") is not True:
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


def _list(server: Server) -> list[dict]:
    return mcp_client.list_tools(server.argv, _secrets(server))


def _listed(server: Server) -> list[dict]:
    """The server's tools, or plain words for the CLI. A raw exception (an
    ExceptionGroup, an OSError carrying the server's own text) never reaches the
    terminal: that text is the server's, and can echo what it was handed."""
    try:
        return [t for t in _list(server) if isinstance(t, dict)]
    except (CredentialUnavailable, StorageError, PolicyError, ConnectorError):
        raise
    except mcp_client.Unavailable as exc:
        raise ConnectorError(str(exc)) from None
    except Exception as exc:
        raise ConnectorError(f"{server.name}: could not list tools ({type(exc).__name__})") from None


def discover(name: str) -> list[Offer]:
    """Ask the server for its tools and say which of them v1 could approve."""
    return [_offer(t) for t in _listed(_require(name))]


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
        offer = _offer(listed[n])
        if not offer.approvable:
            raise ConnectorError(f"{server.name}.{n} cannot be approved: {offer.why}.")
    tools = dict(server.tools)
    for n in wanted:
        o = _offer(listed[n])
        tools[n] = ApprovedTool(n, o.description, o.schema, digest(listed[n]))
    new = replace(server, tools=tools, changed=tuple(c for c in server.changed if c not in wanted))
    _save([new if s.name == server.name else s for s in load()])
    return wanted


def _granted(channel) -> list[Server]:
    # Task A6 adds `Channel.connectors`; until then no channel grants any.
    names = {str(n).lower() for n in getattr(channel, "connectors", ())}
    return [s for s in load() if s.name.lower() in names]


def _shape(params: dict) -> str:
    required = set(params.get("required", ()))
    text = json.dumps({k: f"{v.get('type', 'any')}{' (required)' if k in required else ''}"
                       for k, v in params.get("properties", {}).items()})
    return text if len(text) <= MAX_SHAPE else text[:MAX_SHAPE] + " …"


def _flat(text: str) -> str:
    """One line, no invisible characters. Zero-width and bidi controls (Unicode
    category Cf) let a description read differently to the model than to a
    person reviewing the approval."""
    visible = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(visible.split())[:MAX_DESCRIPTION]


def menu_for(channel) -> list[str]:
    """One line per tool Nemotron may call in this channel.

    The description is server-written text: flattened to one line and capped,
    because it lands in the passage the prompt already calls data, and a
    multi-line description is how a hostile server would try to look like the
    prompt itself.
    """
    lines = []
    for s in _granted(channel):
        for t in s.tools.values():
            if t.name in s.changed:
                continue
            desc = _flat(t.description)
            lines.append(f"- {qualified(s.name, t.name)}: {desc} args: {_shape(t.schema)}")
    return lines


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
        errors = schema.validate(approved.schema, arguments)
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
        # Redaction can turn a valid value into one the schema refuses (an enum
        # member, a minimum length); what is sent must pass the same check.
        errors = schema.validate(approved.schema, arguments)
        if errors:
            return f"{label}: refused — " + "; ".join(errors[:5])
        secrets = _secrets(server)
        live = next((t for t in mcp_client.list_tools(server.argv, secrets)
                     if isinstance(t, dict) and t.get("name") == tool), None)
        if live is None or digest(live) != approved.digest:
            _disable(server, tool)
            return f"{label}: " + CHANGED.format(server=server.name)
        return mcp_client.call_tool(server.argv, secrets, tool, arguments)
    except (CredentialUnavailable, StorageError, PolicyError):
        raise
    except MissingSecret as exc:
        return str(exc)
    except (mcp_client.Unavailable, ConnectorError) as exc:
        return f"{label}: {exc}"
    except Exception as exc:
        # Startup, handshake and the 30 s timeout surface here raw (OSError,
        # TimeoutError, an ExceptionGroup from anyio). Only the type is shown:
        # a server's message can carry anything, including what it was handed.
        return f"{label}: could not run ({type(exc).__name__})"
