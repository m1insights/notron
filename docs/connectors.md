# Connectors: give Notron new tools (MCP)

A connector is an [MCP](https://modelcontextprotocol.io) server you let Notron
use inside a project channel. There are thousands of them already (time, GitHub,
a folder on disk, issue trackers), so Notron does not need its own plugin format.

The split is the same as everywhere else in Notron: **Nemotron picks the tool and
its arguments; plain code checks them before anything leaves the Mac.** You decide
which servers exist, which of their tools are approved, and which channels may use
them. A note, a Siri line or the model can never grant any of that.

## Install

The MCP support is an optional extra. Without it, Notron works as before and
`connect` says what to install.

```bash
pip install 'notron[mcp]'          # or, in a checkout: uv sync --extra mcp
```

## Presets: git, GitHub and web search

Notron's own repository and web lookups are the official MCP servers too. A
preset is one command; what it may do is fixed in code, not in a file you edit.

```bash
notron connect preset git        # mcp-server-git 2026.8.18 via uvx (needs uv)
brew install github-mcp-server   # once
notron connect preset github     # then: notron connect secret github GITHUB_PERSONAL_ACCESS_TOKEN
notron connect preset github     # run again once the token is stored, to approve
notron connect preset tavily     # tavily-mcp 0.2.22 via npx (needs Node); keyless
```

| Preset | Reached by | Approved tools |
|---|---|---|
| `git` | channels with `--allow read` and a `--repo` | `git_status`, `git_log`, `git_branch`, `git_diff_unstaged`, `git_diff`, `git_show` |
| `github` | channels with `--allow read` and a `--github owner/name` | `list_pull_requests`, `pull_request_read`, `search_issues`, `issue_read`, `list_commits`, `actions_list` |
| `tavily` | web questions anywhere, and channels with `--allow research` | `tavily_search`, called by code (the researcher), never from a menu |

What a preset adds to an ordinary connector:

- **The repository is code's choice.** `repo_path` (git) and `owner`/`repo`
  (GitHub) are filled in from the channel and hidden from Nemotron's menu;
  anything the model sends for them is replaced. The git server is also started
  with `--repository` set to that repo, so it refuses any other path itself.
  A project inside a bigger repository (Synqology in `~/Dev`) uses the
  enclosing repository: `mcp-server-git` will not start on a subfolder, so
  `git_log`/`git_show` there also reach sibling projects' commits.
- **Issue search stays in the channel's repo.** `github-mcp-server` only
  prefixes `repo:owner/name`, and GitHub ORs scope qualifiers, so a
  `search_issues` query holding `repo:`, `org:`, `user:` or `owner:` is refused.
- **Tavily is vouched read-only by Notron.** `tavily-mcp` marks none of its
  tools, so v1 would refuse them all. Notron vouches for `tavily_search` only,
  and only while the registered command is exactly the pinned one; edit the
  version and it is an ordinary server again. `--with-key` registers
  `TAVILY_API_KEY` if you would rather use your own key than the keyless tier.
- **Absolute paths.** The preset records where the binary really lives, and the
  server gets that folder first on `PATH`, so a background listener can start
  `npx` (which needs `node`) even with launchd's short `PATH`.

Measured live on 2026-10-02:
[`docs/production/evidence/2026-10-02-mcp-presets.md`](production/evidence/2026-10-02-mcp-presets.md).

## A worked example: what time is it in Tokyo?

`mcp-server-time` is a small read-only server. You need [`uv`](https://docs.astral.sh/uv/)
for `uvx`.

```bash
notron connect add time -- uvx mcp-server-time
notron connect tools time
#   get_current_time · read-only · approvable
#   convert_time · read-only · approvable
notron connect approve time get_current_time convert_time
notron channel set Test --connect time
```

Then: *"Hey Siri, add what time is it in Tokyo to my Notron Test note."* Nemotron
Super sees `time.get_current_time` on the channel's menu, proposes
`{"timezone": "Asia/Tokyo"}`, code checks it, the server answers, and the reply
ends with the receipt: `checked time.get_current_time · decided by Nemotron Super`.

## The commands

| Command | What it does |
|---|---|
| `notron connect add NAME [--secret VAR ...] -- <command>` | Register a server. Everything after `--` is the command that starts it, stored exactly as typed. Runs nothing yet. |
| `notron connect tools NAME` | Start the server, list its tools, and say which ones v1 can approve, and why not for the rest. |
| `notron connect approve NAME TOOL [TOOL ...]` | Approve tools, all or none. |
| `notron connect secret NAME VAR` | Store a secret the server needs (for example `GITHUB_TOKEN`) in the Keychain. The value is read from stdin, never from the command line. |
| `notron channel set PROJECT --connect NAME[,NAME]` | Let Nemotron use these connectors in that channel. `--disconnect NAME` takes one away. `channel add ... --connect` works too. |
| `notron connect list [--json]` | Servers, approved tools, tools switched off since approval, secrets they need, and the channels using them. |
| `notron connect remove NAME` | Unregister the server, take it out of every channel, and forget its secrets. |

The name is letters, numbers and dashes (up to 40). The command must be a bare
command name (looked up on `PATH` when the server starts, such as `uvx` or `npx`)
or an absolute path.

`connect secret` and `channel set` need Notron's secure storage, which needs the
signed Notron app installed: Notron checks the app bundle's signature before it
unlocks the Keychain. Without it they stop (`connect secret` says `Secure startup
requires a validly signed Notron bundle.`; `channel set` says `Protected processing
paused.`). A server with no secrets never touches the Keychain.

## What v1 will and will not run

**Read-only only.** A tool can be approved only if the server marks it
`readOnlyHint: true`. That mark is the server's own claim, which is why approval
is also yours, tool by tool. A tool without it shows
`changes things · not approvable: changes things: v1 is read-only` and
`connect approve` refuses it. Writing through connectors comes later, through the
same Approve reminder hand-off tasks use.

A few other tools are refused at approval with a reason: names Notron cannot show
safely, argument names that are not plain identifiers, more than 20 arguments, or
argument shapes too complex to check.

**A tool that changes after approval is switched off.** Approving pins a
fingerprint of everything the server said about the tool: its name, description,
arguments and hints. Before every call Notron asks the server again. If anything
differs, the call is refused with
`changed since you approved it — run notron connect approve NAME`, the tool leaves
the channel's menu, and `connect list` shows it under "changed since approval".
Changing it back does not bring it back; only your approval does.

**Every call is checked in code first:**

1. The connector must be granted to this channel, and the tool approved.
2. The arguments must match the tool's argument shape. A refusal names the field
   and the rule (`refused — text: wrong type`), never the value.
3. Arguments that look like a credential are blocked
   (`blocked: arguments looked like a credential`), then the rest go through the
   same outbound redaction as anything sent to a model.
4. At most four tools run per request, built-in and connector together.

Each refusal becomes one plain line in the reply. Nothing reaches the server.

## Secrets

**Tokens never go in the command.** A command line is visible to every process
on the Mac, lands in shell history and would sit in `connectors.json` in the
clear, so `connect add` refuses a command that holds anything token-shaped.
Declare the variable instead and store the value separately:

```bash
notron connect add github --secret GITHUB_PERSONAL_ACCESS_TOKEN -- /absolute/path/to/github-mcp-server stdio
printf '%s' "$TOKEN" | notron connect secret github GITHUB_PERSONAL_ACCESS_TOKEN   # or run it and paste
```

The server starts with a stripped environment (essentials such as `PATH` and
`HOME`) plus only its own declared secrets. Your Nebius key and everything else
in Notron's environment never reach it.

## What comes back

A connector's output is **untrusted data**, not instructions, exactly like the
output of a git or web lookup. It is capped in length; pictures and other non-text
content are not shown to the model, and a line says how many were left out so the
answer never implies it saw them. A tool's description is written by a third
party too: it is flattened to one line, capped at 160 characters, and the model is
told it says what the tool does, never what Nemotron must do.

Any server error comes back as its type only (`could not run (TimeoutError)`),
because a server's own error text can echo what it was handed. Calls time out
after 30 seconds.

## How fast

Measured on 2026-10-01 with `uvx mcp-server-time`: about **0.5 s per call**
(median). Each call starts the server twice, once to re-check the tool and once
to run it. That sits next to roughly 2.5 s for Nemotron Super to decide. Details:
[`docs/production/evidence/2026-10-mcp-connectors.md`](production/evidence/2026-10-mcp-connectors.md).

## Not yet

- GitHub's `list_issues` (one argument is a list of objects, which v1 will not
  half-check); `search_issues` covers it.
- Tools that change things (planned through the Approve reminder).
- Remote servers over HTTP with sign-in, such as Notion and Linear. v1 runs local
  servers over stdio.
