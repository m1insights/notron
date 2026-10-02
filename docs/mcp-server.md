# Use your Apple Notes from any AI (MCP server)

Notron's Apple bridge runs as an [MCP](https://modelcontextprotocol.io) server,
so Claude Desktop, Cursor and any other MCP client can search and read the notes
you let Notron read, see your calendar and reminders, and ask Notron itself.

It shows a client exactly what Notron's own model would see, and no more: your
"Your notes" choices, privacy redaction and the "never return a picture" rule
all apply.

> **Privacy: note text a client reads is sent to that client's AI provider.**
> If Claude Desktop reads a note, Anthropic receives it; if Cursor does, Cursor's
> model provider does. Notron cannot control what happens to it after that. Only
> connect clients whose provider you are comfortable with, and mark anything you
> would not send them as **ignore** in "Your notes".

## Set it up

You need the optional extra and a configured note policy (`notron library`).

```bash
pip install 'notron[mcp]'          # or, in a checkout: uv sync --extra mcp
notron mcp config
```

`mcp config` prints a block like this, with the full path of the Python that runs
Notron:

```json
{
  "mcpServers": {
    "notron": {
      "command": "/path/to/notron/.venv/bin/python",
      "args": ["-m", "notron", "mcp", "serve"]
    }
  }
}
```

- **Claude Desktop:** Settings → Developer → Edit Config, paste the `notron` entry
  into `mcpServers`, then quit and reopen Claude Desktop.
- **Cursor:** paste the same entry into `~/.cursor/mcp.json` (or the project's
  `.cursor/mcp.json`).

Then ask the client something like *"search my notes for parking"*.

## The tools

All four read tools are marked read-only. None of them needs a Nebius key.

| Tool | Arguments | Returns |
|---|---|---|
| `notes_list` | `limit` (default 50, max 50) | `{"notes": [{"id", "title", "folder", "modified"}]}`, newest first. Metadata only, no text. |
| `notes_search` | `query`, `limit` (default 8, max 50) | `{"notes": [{"id", "title", "folder", "modified", "excerpt"}]}`. Keyword match: titles first, then body matches only among the top title candidates; no model, no index, no network. |
| `notes_read` | `note_id` | `{"id", "title", "folder", "modified", "text", "has_attachments_not_shown"}`. Text is capped at 20,000 characters, then `[… more not shown]`. |
| `agenda` | `days` (default 7, max 31) | `{"today", "week", "reminders"}`, each plain text. |
| `ask_notron` | `request` | `{"answer", "results", "dry_run"}`. Runs Notron's normal graph: NVIDIA Nemotron decides, Notron's Guard authorizes. Needs the Nebius key. |

Anything that goes wrong comes back as `{"error": "..."}` in plain words, never a
crashed session. Unknown errors are named by type only, so a note title cannot
leak into another provider's logs.

## What a client never gets

- **Ignored notes.** A note you marked ignore, a note Notron's policy does not
  allow, and a note that does not exist all answer `{"error": "not available"}`,
  the same words, so a client cannot probe which notes exist. The check runs
  again on every read, not just when the list was made.
- **📌 About Me and Notron's own notes.** They are instructions, not data: never
  listed, searched or read, wherever you have moved them.
- **Attachments.** Pictures, recordings and files are never returned. When a note
  holds one (or Notron could not tell), `has_attachments_not_shown` is `true`, and
  the tool tells the client not to describe them.
- **Secrets.** Note text, titles, excerpts and agenda items go through the same
  redaction as anything Notron sends to a model, so supported credential patterns
  are blanked. Redaction cannot recognise every secret; see `SECURITY.md`.

## `ask_notron`, and `--writes`

By default `ask_notron` is a **dry run**: Nemotron answers, nothing is written,
and the result says `"dry_run": true`.

```bash
notron mcp serve --writes     # ask_notron may file notes and create reminders
notron mcp serve --no-ask     # only the four read tools; no Nebius key involved
```

To use either flag, add it to `args` in the client's config, for example
`["-m", "notron", "mcp", "serve", "--writes"]`.

**`--writes` lets a client's AI ask Notron to change your notes and reminders.**
Every write still goes through Notron's own checks: the Guard, the startup checks
a terminal write runs, and the same receipts. But the request now comes from
another company's model, which can be steered by whatever it read. The tool's
"may be destructive" hint is deliberately left unset, which clients read as
"possibly destructive", so a well-behaved client asks you before each call. Leave
it off unless you need it.

## Known limits

- **`ask_notron` answers "busy" while the listener runs.** Only one thing may act
  for you at a time. Ask in 📥 Ask Notron instead, or stop the listener
  (`notron listen --off`). The read tools still work.
- **Pausing Notron blocks `ask_notron`,** dry runs included. Resume in the Notron
  app.
- **The first Notes read after Notes has been idle can take about 40 seconds.**
  Later reads are fast.
- **macOS asks permission for the client, not for Notron.** The first read makes
  macOS ask whether, for example, Claude Desktop may control Notes. That approval
  belongs to the client app.
- **Calendar and Reminders may read as blind under the client.** macOS grants
  them per app, and a client often has no grant. Notron then says *"I cannot read
  your Calendar"* rather than showing an empty week.
- **Notes and the listener share one queue.** Reads wait their turn behind the
  listener rather than failing; a long one can delay her.

The on-device check with a real client is tracked as the B3 device gate in
[`docs/plans/2026-10-01-mcp-connectors-and-apple-bridge.md`](plans/2026-10-01-mcp-connectors-and-apple-bridge.md).
