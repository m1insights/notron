# Evidence: git, GitHub and Tavily presets (2026-10-02)

Branch `feature/mcp-replace`. Replaces `notron/tools.py` and the direct Tavily
client. Design: `docs/plans/2026-10-02-mcp-replace-design.md`.

## Servers

| Preset | Binary | Version |
|---|---|---|
| git | `uvx mcp-server-git==2026.8.18` | 2026.8.18 |
| github | `/opt/homebrew/bin/github-mcp-server stdio --read-only --toolsets repos,issues,pull_requests,actions` | 1.12.2 (Homebrew) |
| tavily | `npx -y tavily-mcp@0.2.22` | 0.2.22, keyless |

## Live run

Harness: fresh `NOTRON_DATA_DIR`; the GitHub token from `gh auth token`;
`connectors._secrets` and `prepare_outbound` stood in for the Keychain and the
note policy, which need the signed app (P06). Everything else is the real path:
`install_preset` → `approve` (fresh listing, digest pinned) → `connectors.call`
(grant, schema, credential block, bound arguments, digest re-check by a second
listing, call). Three calls each, median:

| Call | Median | Result |
|---|---|---|
| `git.git_status` (juno) | 0.61 s | real status |
| `git.git_log`, model sent `repo_path: /etc` | 0.61 s | juno's log: code replaced the path |
| `git.git_log`, Synqology (monorepo subfolder) | 0.60 s | `~/Dev` log (enclosing repo) |
| `github.list_pull_requests` m1insights/notron | 0.25 s | `[]` (none open) |
| `github.search_issues is:open` | 0.36 s | `total_count: 0` |
| `github.actions_list list_workflow_runs` | 0.34 s | 38 runs, latest is today's |
| researcher: `tavily_search` (6 results) | 2.30 s | 5 parsed findings, top PMC12412596 |

Install times: git 0.7 s, github 0.1 s, tavily 0.6 s (warm caches).

Before (2026-09-23): built-in git tools 0.06–0.2 s each, prefetched during the
~2.5 s Super decision. After: ~0.6 s per git call, run after the decision. A
typical one-or-two-tool channel answer is ~0.5–1 s slower. Tavily direct was not
re-measured; the MCP search is ~2.3 s including two server starts.

## Findings recorded in code

- `mcp-server-git` writes optional arguments as `anyOf [T, null]`; GitHub marks
  `owner`/`repo` with `x-mcp-header`. `schema.py` now accepts exactly those two
  shapes; real unions and nested containers are still refused.
- `tavily-mcp` sends no annotations; `tavily_search` is vouched by preset only.
- `mcp-server-git` refuses to start on a subfolder; the enclosing repo is used.
- `mcp-server-git --repository X` refuses other paths:
  `Repository path '/Users/m1labs/Dev' is outside the allowed repository`.
- GitHub `list_issues` stays unapprovable (`field_filters`: list of objects).
- Keyless Tavily answers a rate limit with a payment / "bonus credits" pitch
  addressed to agents; `research.parse` keeps only Title/URL/Content results.
