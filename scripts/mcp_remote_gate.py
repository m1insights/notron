"""Live check for remote MCP connectors, before the signed app (P06) exists.

Usage:
  .venv/bin/python scripts/mcp_remote_gate.py github   # paste a GitHub token when asked
  .venv/bin/python scripts/mcp_remote_gate.py vercel   # your browser opens; sign in

Uses a throwaway data folder and an in-memory stand-in for the Keychain, so the
real `connectors.json`, channels and Keychain are never touched and nothing is
kept: the token and the sign-in are gone when the script ends. Prints the tools
the server offers, approves one read-only tool and calls it twice through the
same path the listener uses.
"""
import collections
import getpass
import json
import os
import sys
import tempfile
import time

# realpath: macOS temp lives under /var, a symlink, which secure storage refuses.
os.environ["NOTRON_DATA_DIR"] = os.path.realpath(tempfile.mkdtemp(prefix="notron-gate-"))

from notron import channels, connectors, credentials, schema  # noqa: E402

SERVERS = {"github": ("https://api.githubcopilot.com/mcp/", "bearer", "get_me"),
           "vercel": ("https://mcp.vercel.com", "oauth", "list_teams")}


class Memory:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def put(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def _first_unsupported(s, path, top):
    """The first part of a schema `schema.supported` refuses, as `path: why`."""
    if not top and isinstance(s, dict) and "anyOf" in s:
        inner = schema.nullable(s)
        return f"{path or 'top'}: anyOf" if inner is None else _first_unsupported(inner, path, False)
    if not isinstance(s, dict):
        return f"{path or 'top'}: not an object"
    extra = schema._keys(s) - schema.KEYWORDS
    if extra:
        return f"keyword {sorted(extra)}"
    if not schema._well_formed(s):
        return "malformed: " + json.dumps(s)[:80]
    t = s.get("type")
    if t not in schema.TYPES or (top and t != "object"):
        return f"type {t!r}"
    for key, sub in (s.get("properties") or {}).items() if t == "object" else ():
        found = _first_unsupported(sub, f"{path}.{key}", False)
        if found:
            return found
    if t == "array":
        item = s.get("items", {"type": "string"})
        if schema.effective(item).get("type") in ("array", "object"):
            return "array of objects or arrays"
        return _first_unsupported(item, path + "[]", False)
    return ""


def main(name):
    url, auth, preferred = SERVERS[name]
    credentials.configure(Memory())
    connectors.add(name, url=url, auth=auth, secrets=("GITHUB_TOKEN",) if auth == "bearer" else ())
    if auth == "bearer":
        credentials.provision_api_key(f"connector.{name}.GITHUB_TOKEN", getpass.getpass("GitHub token: "))
    else:
        connectors.login(name)
    started = time.monotonic()
    offers = connectors.discover(name)
    ok = [o.name for o in offers if o.approvable]
    print(f"\n{len(offers)} tools in {time.monotonic() - started:.2f}s; {len(ok)} approvable read-only:")
    print("  " + ", ".join(ok))
    for o in offers:
        if not o.approvable:
            print(f"  not approvable: {o.name} ({o.why})")
    # Why a read-only tool's arguments were refused, counted by first cause, so
    # a server whose whole catalogue fails reads as one fixable line.
    causes = collections.Counter()
    for o in offers:
        if o.why == "arguments too complex for v1":
            causes[_first_unsupported(o.schema, "", True)] += 1
    for cause, n in causes.most_common(8):
        print(f"  {n:4} refused for: {cause}")
    # A tool that needs no arguments, so the call below can send `{}`.
    bare = [o.name for o in offers if o.approvable and not o.schema.get("required")]
    if not bare:
        raise SystemExit("  No read-only tool without required arguments to try.")
    tool = preferred if preferred in bare else bare[0]
    connectors.approve(name, [tool])
    # No note policy lives in a throwaway folder; credential-shaped arguments
    # are still refused by `connectors.call` before this is reached.
    connectors.prepare_outbound = lambda purpose, passages: [p.text for p in passages]
    channel = channels.Channel("Gate", "note-gate", connectors=(name,))
    for _ in range(2):
        started = time.monotonic()
        out = connectors.call(channel, f"{name}.{tool}", {})
        print(f"\n{name}.{tool} in {time.monotonic() - started:.2f}s:\n  {out[:300]}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in SERVERS:
        raise SystemExit(__doc__)
    try:
        main(sys.argv[1])
    except connectors.ConnectorError as problem:
        raise SystemExit(f"  {problem}")
