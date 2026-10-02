"""Presets: the official git, GitHub and Tavily MCP servers, and what code lets them do.

2026-10-02 these replaced the hand-written `tools.py` and the direct Tavily
client. The servers are faked here exactly as in `test_connectors.py`; the
shapes below are the ones the real servers listed that day.
"""
import os

import pytest

from notron import channels, connectors, mcp_client

GIT_LOG = {"name": "git_log", "description": "Shows the commit logs",
           "inputSchema": {"type": "object", "required": ["repo_path"], "properties": {
               "repo_path": {"type": "string"},
               "max_count": {"type": "integer", "default": 10},
               "start_timestamp": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}}},
           "annotations": {"readOnlyHint": True}}
GIT_COMMIT = {"name": "git_commit", "description": "Records changes",
              "inputSchema": {"type": "object", "properties": {"repo_path": {"type": "string"},
                                                               "message": {"type": "string"}}},
              "annotations": {"readOnlyHint": False}}
PRS = {"name": "list_pull_requests", "description": "List pull requests",
       "inputSchema": {"type": "object", "required": ["owner", "repo"], "properties": {
           "owner": {"type": "string", "x-mcp-header": "owner"},
           "repo": {"type": "string", "x-mcp-header": "repo"},
           "state": {"type": "string", "enum": ["open", "closed", "all"]}}},
       "annotations": {"readOnlyHint": True}}
#: tavily-mcp 0.2.22 sends no annotations at all.
SEARCH = {"name": "tavily_search", "description": "Search the web",
          "inputSchema": {"type": "object", "required": ["query"], "properties": {
              "query": {"type": "string"}, "max_results": {"type": "number", "minimum": 5,
                                                           "maximum": 20}}},
          "annotations": {}}
EXTRACT = {**SEARCH, "name": "tavily_extract"}


class Servers:
    """Every preset server at once, told apart by the binary they start."""

    def __init__(self):
        self.calls, self.listed = [], []
        self.by = {"uvx": [GIT_LOG, GIT_COMMIT], "github-mcp-server": [PRS], "npx": [SEARCH, EXTRACT]}

    def _tools(self, argv):
        return [dict(t) for t in self.by[os.path.basename(argv[0])]]

    def list_tools(self, argv, secrets):
        self.listed.append(tuple(argv))
        return self._tools(argv)

    def call_tool(self, argv, secrets, name, arguments):
        self.calls.append((tuple(argv), name, arguments, dict(secrets)))
        return "Detailed Results:\n\nTitle: T\nURL: https://example.org\nContent: c"


@pytest.fixture
def servers(monkeypatch):
    fake = Servers()
    monkeypatch.setattr(mcp_client, "list_tools", fake.list_tools)
    monkeypatch.setattr(mcp_client, "call_tool", fake.call_tool)
    monkeypatch.setattr(connectors.shutil, "which", lambda cmd: f"/opt/bin/{cmd}")
    monkeypatch.setattr(connectors.os.path, "realpath", lambda p, **kw: p)
    # The fake git server lists one read, not the real six.
    monkeypatch.setitem(connectors.PRESETS, "git", connectors.replace(
        connectors.PRESETS["git"], tools=("git_log",)))
    return fake


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "Dev"
    (root / ".git").mkdir(parents=True)
    project = root / "apps" / "synqology"
    project.mkdir(parents=True)
    return root, project


def channel(project="", github="", allow=("read", "research"), names=()):
    return channels.Channel("Synqology", "note-x", str(project), github, tuple(allow), connectors=tuple(names))


# --- install ----------------------------------------------------------------

def test_a_preset_registers_the_pinned_server_and_approves_only_its_reads(servers):
    server, done = connectors.install_preset("git")
    assert server.argv == ("/opt/bin/uvx", "mcp-server-git==2026.8.18")
    assert done == ["git_log"] and set(connectors.get("git").tools) == {"git_log"}
    stored = connectors.get("git")
    assert stored.preset == "git" and connectors.preset_of(stored) is connectors.PRESETS["git"]


def test_the_git_preset_can_never_approve_a_write(servers):
    connectors.add("git", ("/opt/bin/uvx", "mcp-server-git==2026.8.18"), preset="git")
    with pytest.raises(connectors.ConnectorError, match="changes things"):
        connectors.approve("git", ["git_commit"])


def test_running_a_preset_again_follows_a_binary_that_moved(servers, monkeypatch):
    """fnm keeps node per version; upgrading Node moves npx and the recorded
    path stops existing. Running the preset again finds it, keeping approvals."""
    connectors.add("tavily", ("/gone/v20/bin/npx", "-y", "tavily-mcp@0.2.22"), preset="tavily")
    connectors.approve("tavily", ["tavily_search"])
    server, done = connectors.install_preset("tavily")
    assert server.argv[0] == "/opt/bin/npx" and connectors.get("tavily").argv[0] == "/opt/bin/npx"
    assert done == ["tavily_search"] and "tavily_search" in connectors.get("tavily").tools


def test_a_missing_binary_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(connectors.shutil, "which", lambda cmd: None)
    with pytest.raises(connectors.ConnectorError, match="brew install github-mcp-server"):
        connectors.install_preset("github")
    assert connectors.get("github") is None


def test_a_preset_will_not_take_over_a_hand_registered_server(servers):
    connectors.add("git", ("uvx", "something-else"))
    with pytest.raises(connectors.ConnectorError, match="remove it first"):
        connectors.install_preset("git")


def test_the_github_preset_waits_for_its_token(servers):
    with pytest.raises(connectors.MissingSecret, match="GITHUB_PERSONAL_ACCESS_TOKEN"):
        connectors.install_preset("github")
    assert connectors.get("github").tools == {} and servers.listed == []


# --- tavily: vouched, and only while pinned ---------------------------------

def test_tavily_search_is_approvable_only_because_notron_vouches_for_it(servers):
    """tavily-mcp marks nothing read-only. Code vouches for `tavily_search` and
    nothing else; `tavily_extract` stays "changes things"."""
    server, done = connectors.install_preset("tavily")
    assert done == ["tavily_search"] and server.secrets == ()
    offers = {o.name: o for o in connectors.discover("tavily")}
    assert offers["tavily_search"].approvable
    assert not offers["tavily_extract"].approvable


def test_a_hand_edited_tavily_version_loses_the_vouch(servers):
    connectors.add("tavily", ("/opt/bin/npx", "-y", "tavily-mcp@latest"), preset="tavily")
    assert connectors.preset_of(connectors.get("tavily")) is None
    with pytest.raises(connectors.ConnectorError, match="changes things"):
        connectors.approve("tavily", ["tavily_search"])


def test_tavily_is_never_on_a_channel_menu(servers, repo):
    connectors.install_preset("tavily")
    assert connectors.offered(channel(repo[1])) == {}


def test_the_researcher_search_goes_through_the_connector_checks(servers):
    connectors.install_preset("tavily")
    assert connectors.web_ready()
    out = connectors.web_search("magnesium sleep", 6)
    assert "https://example.org" in out
    argv, name, arguments, _ = servers.calls[-1]
    assert name == "tavily_search" and arguments == {"query": "magnesium sleep", "max_results": 6}
    # The digest was re-checked against a fresh listing just before the call.
    assert servers.listed[-1] == argv


def test_a_credential_shaped_query_never_reaches_tavily(servers):
    connectors.install_preset("tavily")
    with pytest.raises(connectors.ConnectorError, match="looked like a credential"):
        connectors.web_search("sk-abcdefghijklmnopqrstuvwx", 6)
    assert servers.calls == []


# --- the channel's repository is code's choice -------------------------------

def test_the_repository_argument_is_hidden_from_nemotron(servers, repo):
    connectors.install_preset("git")
    menu = connectors.offered(channel(repo[1]))
    assert "git.git_log" in menu and "repo_path" not in menu["git.git_log"]
    assert '"start_timestamp": "string or null"' in menu["git.git_log"]


def test_a_repository_the_model_names_is_replaced_by_the_channels(servers, repo):
    """"Show me the log of ~/.ssh" — the model may copy a path from the request.
    Code overwrites it, and the server is started fenced to the channel's repo."""
    root, project = repo
    connectors.install_preset("git")
    out = connectors.call(channel(project), "git.git_log", {"repo_path": "/Users/x/.ssh", "max_count": 3})
    argv, name, arguments, _ = servers.calls[-1]
    assert arguments == {"max_count": 3, "repo_path": str(root)}
    assert argv[-2:] == ("--repository", str(root))
    assert "Detailed" in out


def test_a_monorepo_subfolder_uses_its_enclosing_repository(repo):
    """mcp-server-git refuses to start on a subfolder (measured 2026-10-02), and
    Synqology lives inside the developer's whole ~/Dev monorepo."""
    root, project = repo
    assert connectors._toplevel(str(project)) == str(root)
    assert connectors._toplevel(str(root.parent)) == ""


def test_a_channel_without_a_repository_never_sees_git(servers, tmp_path):
    connectors.install_preset("git")
    assert connectors.offered(channel("")) == {}
    assert connectors.offered(channel(tmp_path)) == {}       # a folder that is not a repo
    out = connectors.call(channel(""), "git.git_log", {})
    assert "not granted" in out and servers.calls == []


def test_github_owner_and_repo_come_from_the_channel(servers, repo, monkeypatch):
    connectors.add("github", ("/opt/bin/github-mcp-server", *connectors.PRESETS["github"].argv[1:]),
                   preset="github")
    connectors.approve("github", ["list_pull_requests"])
    ch = channel(repo[1], github="m1/synq")
    assert '"state": "string"' in connectors.offered(ch)["github.list_pull_requests"]
    connectors.call(ch, "github.list_pull_requests", {"owner": "attacker", "state": "open"})
    _, _, arguments, _ = servers.calls[-1]
    assert arguments == {"state": "open", "owner": "m1", "repo": "synq"}
    assert "github.list_pull_requests" not in connectors.offered(channel(repo[1]))   # no slug


# --- grants ------------------------------------------------------------------

def test_the_read_switch_reaches_git_and_research_reaches_nothing_on_the_menu(servers, repo):
    connectors.install_preset("git")
    assert "git.git_log" in connectors.offered(channel(repo[1], allow=("read",)))
    assert connectors.offered(channel(repo[1], allow=("research",))) == {}


def test_an_ordinary_server_still_needs_its_name_granted(servers, repo):
    connectors.add("git", ("/opt/bin/uvx", "mcp-server-git==2026.8.18"))   # not a preset
    connectors.approve("git", ["git_log"])
    assert connectors.offered(channel(repo[1])) == {}
    assert "git.git_log" in connectors.offered(channel(repo[1], names=("git",)))


# --- the child process -------------------------------------------------------

def test_a_server_by_absolute_path_finds_its_own_runtime(monkeypatch):
    """npx is `#!/usr/bin/env node`; a launchd listener's PATH has never heard
    of the folder node lives in."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = mcp_client.child_env({}, ("/Users/u/.fnm/node/bin/npx", "-y", "tavily-mcp@0.2.22"))
    assert env["PATH"].split(":")[0] == "/Users/u/.fnm/node/bin"
    assert mcp_client.child_env({}, ("npx",))["PATH"] == "/usr/bin:/bin"


# --- review findings, 2026-10-02 ----------------------------------------------

SEARCH_ISSUES = {"name": "search_issues", "description": "Search issues",
                 "inputSchema": {"type": "object", "required": ["query"], "properties": {
                     "query": {"type": "string"}, "owner": {"type": "string"}, "repo": {"type": "string"}}},
                 "annotations": {"readOnlyHint": True}}
SEARCH_CODE = {"name": "search_code", "description": "Search code",
               "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
               "annotations": {"readOnlyHint": True}}


def _github(servers, *tools):
    servers.by["github-mcp-server"] = [PRS, SEARCH_ISSUES, SEARCH_CODE]
    connectors.add("github", ("/opt/bin/github-mcp-server", *connectors.PRESETS["github"].argv[1:]),
                   preset="github")
    connectors.approve("github", list(tools))


@pytest.mark.parametrize("query", ["repo:other-org/private token", "token org:acme", "-user:me x",
                                   'x "repo:a/b"', "OWNER: someone"])
def test_a_search_query_cannot_name_another_repository(servers, repo, query):
    """github-mcp-server only prefixes `repo:owner/name`, and GitHub ORs scope
    qualifiers, so `repo:other/private` in the query reached any repo the token sees."""
    _github(servers, "search_issues")
    out = connectors.call(channel(repo[1], github="m1/synq"), "github.search_issues", {"query": query})
    assert "names a repository" in out and servers.calls == []


def test_an_ordinary_issue_search_still_runs_scoped(servers, repo):
    _github(servers, "search_issues")
    connectors.call(channel(repo[1], github="m1/synq"), "github.search_issues", {"query": "is:open crash"})
    assert servers.calls[-1][2] == {"query": "is:open crash", "owner": "m1", "repo": "synq"}


def test_a_tool_without_owner_arguments_is_not_handed_them(servers, repo):
    _github(servers, "search_code")
    out = connectors.call(channel(repo[1], github="m1/synq"), "github.search_code", {"query": "TODO"})
    assert "unexpected" not in out and servers.calls[-1][2] == {"query": "TODO"}


def test_with_key_adds_the_tavily_key_to_an_existing_preset(servers):
    connectors.install_preset("tavily")
    with pytest.raises(connectors.MissingSecret, match="notron connect secret tavily TAVILY_API_KEY"):
        connectors.install_preset("tavily", with_key=True)
    assert connectors.get("tavily").secrets == ("TAVILY_API_KEY",)
