# Using Canonia with your LLM / agent

Getting an agent to *use* the canon is two steps, and both matter:

1. **Connect** your MCP client to `canonia serve` so the agent *can* reach the
   canon's tools (`search`, `get`, `create`, `update`, …).
2. **Instruct** the agent — in its project instructions — *when and how* to use
   them. A connected server the agent is never told to consult just sits idle.

This guide covers both, plus the read/write loop and a first-run checklist.

## Prerequisites

- `canonia` on your `PATH` (`pip install canonia`; verify with `canonia --version`).
- A canon directory with a `canonia.yml` (`canonia init …` — see
  [installing](installing.md)), ideally already seeded ([importing](importing.md)).
- Optional but recommended: `canonia index build` so `search` goes **hybrid**
  keyword + semantic (needs the `[semantic]` extra — see [indexing](indexing.md)).

Use the **absolute path** to your canon everywhere below.

---

## 1. Connect your client

Every client launches the same stdio command — `canonia serve --canon <ABSOLUTE_PATH>`.
What differs is the config file location and (for VS Code) the top-level key.

### Claude Code (CLI)

Add it with one command — `--scope project` writes a shareable `.mcp.json`:

```bash
claude mcp add --scope project canonia -- canonia serve --canon /abs/path/to/canon
```

The `--` separator is required; everything after it is the server command. Or write
`.mcp.json` in the repo root yourself:

```json
{
  "mcpServers": {
    "canonia": {
      "type": "stdio",
      "command": "canonia",
      "args": ["serve", "--canon", "/abs/path/to/canon"]
    }
  }
}
```

Scopes: `--scope local` (default, project-private, in `~/.claude.json`), `project`
(the `.mcp.json` above, shareable), `user` (all your projects). Claude Code asks for
approval the first time it launches a project-scoped server.

### Claude Desktop

Edit the config file (create it if missing), then **restart** the app:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "canonia": {
      "command": "canonia",
      "args": ["serve", "--canon", "/abs/path/to/canon"]
    }
  }
}
```

Paths must be absolute (no `~`). MCP logs: `~/Library/Logs/Claude/mcp.log` (macOS).

### VS Code (Copilot / MCP)

VS Code uses `.vscode/mcp.json` and the key is **`servers`**, not `mcpServers`:

```json
{
  "servers": {
    "canonia": {
      "type": "stdio",
      "command": "canonia",
      "args": ["serve", "--canon", "/abs/path/to/canon"]
    }
  }
}
```

### Cursor

Cursor reads a project `.cursor/mcp.json` (and a global `~/.cursor/mcp.json`) using
the standard `mcpServers` shape below. Confirm the current location in Cursor's MCP
docs (<https://cursor.com/docs/context/mcp>) if it doesn't pick up the server.

```json
{
  "mcpServers": {
    "canonia": {
      "command": "canonia",
      "args": ["serve", "--canon", "/abs/path/to/canon"]
    }
  }
}
```

### Any other MCP client

The stdio server shape is standard — a named entry with `command` + `args`:

```json
{
  "mcpServers": {
    "canonia": { "command": "canonia", "args": ["serve", "--canon", "/abs/path/to/canon"] }
  }
}
```

Two things vary by client: **where** the config file lives, and the **root key**
(most use `mcpServers`; VS Code uses `servers`). Consult your client's MCP docs for
the path; the inner object is the same everywhere.

### Verify the connection

In a fresh agent session, the canon's tools should now be available. A quick check:
ask the agent to run the canon's `list_domains`, or `search` for a term you know is
in the canon. If nothing shows up, see [Troubleshooting](#troubleshooting).

---

## 2. Instruct the agent

Connecting only makes the tools *available*. To make the agent *reach for them*, add
guidance to wherever that agent reads project instructions:

- **Claude Code / Claude Desktop** → the repo's `CLAUDE.md`
- **Cursor** → `.cursor/rules` (or `.cursorrules`)
- **Other agents** → `AGENTS.md` or the system prompt

Paste and adapt this block (rename `canonia` to match your server name, and fill in
the domains/topics your canon actually owns):

```markdown
## Canonical knowledge — use the `canonia` MCP server

This project's durable, cross-repo knowledge lives in a **Canonia canon**, reachable
through the `canonia` MCP tools. The canon is the source of truth — prefer it over
your own memory or copied notes.

**Before** answering a question or making a decision about a topic the canon may
cover (e.g. our conventions, architecture, ops runbooks, domains: process / infra /
ops), **consult the canon first**:
1. `search` the canon for the topic (hybrid keyword + semantic).
2. `get` the most relevant concept(s) by `id` to read the full body, its
   `references`, and its backlinks (`referenced_by`).
3. Ground your answer in what you find and **cite concepts by their `id`** rather
   than pasting their content around.

**When you learn something durable and reusable** (a convention, a fix worth
remembering, a decision), **write it back** instead of only putting it in a local
file:
- `create` a new single-topic concept (kebab-case `id`, correct `domain`, one-line
  `summary`, `references` to related concepts). Referencing an id that doesn't exist
  yet is allowed (it warns) — create the neighbour next.
- `update` an existing concept when the knowledge already has a home (`append_body`
  to add a note; change fields as needed).

**Don't delete** a concept to retire it — that breaks inbound links. Use the
lifecycle tools: `deprecate` (with `superseded_by`), `merge` (redirect into a
canonical id), or `archive`. See the maintainer for `remove`.

Keep concepts **single-topic and small**; link them with `references` /
`[[id]]` rather than growing one big note.
```

Tune the wording to your canon: name the real domains, and if the canon is
authoritative for specific things ("all deployment runbooks live in the canon"), say
so explicitly — agents follow concrete instructions far better than vague ones.

---

## The read/write loop

Canonia is bidirectional on purpose:

- **Read** — an agent `search`/`get`s a concept mid-task instead of relying on a
  copy that may have drifted.
- **Write** — when it learns something durable, it `create`/`update`s a concept.
  Writes go straight to the git-backed files, so **the next read in any session or
  repo sees them** — no re-syncing copies across repos.

Autocommit is **on by default**: each write is committed **locally** (never
pushed) with a message like `Create concept '<id>'`, so you get history for
free. Run the server with `--identity <name> --identity-kind llm` (or
`$CANONIA_IDENTITY`) and commits are authored as the agent — and agent-created
concepts land as **drafts** for human review. Agents should pass `get`'s
`version` back as `update`'s `expected_version` so concurrent edits fail
cleanly instead of overwriting each other. Run `canonia validate` before
shipping — it's the hard gate that catches any dangling edge the permissive
writes allowed. See [maintaining](maintaining.md) and [serving](serving.md).

---

## First-run checklist (end-to-end)

1. `pip install 'canonia[semantic]'` and `canonia --version`.
2. `canonia init my-canon --domains …` → `cd my-canon && git init`.
3. Seed it: `canonia import --zero-config ../notes --domain process --commit`.
4. `canonia validate` (gates pass) and `canonia index build` (searchable).
5. Connect your client (section 1) with the **absolute** canon path; verify the
   tools appear.
6. Add the instruction block (section 2) to the agent's instructions file.
7. In a fresh session, ask something the canon covers → confirm the agent `search`es
   and cites a concept `id`. Teach it something new → confirm it `create`s/`update`s
   a concept and that a follow-up `search` finds it.
8. `canonia build` and open `site/index.html` to see the graph a human can browse.

## Troubleshooting

- **Tools don't appear** — check the canon path is absolute and correct; restart the
  client (Claude Desktop reads config on startup); confirm `canonia serve --canon
  <path>` runs without error in a terminal.
- **Agent has tools but ignores them** — strengthen the instruction block: name the
  exact topics/domains the canon owns and say "consult the canon before answering."
- **`search` returns only keyword hits** — build the index (`canonia index build`)
  and install the `[semantic]` extra; without them search degrades to keyword-only.

## Security

The server currently runs **open** — no built-in auth, and the governance seam is a
no-op in v1. Only connect agents/clients you trust, and keep the canon private. See
[deploying](deploying.md).
