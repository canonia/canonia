# Serving — the MCP server

`canonia serve` runs a stateless [Model Context Protocol](https://modelcontextprotocol.io)
server on stdio. Agents connect to it to search, read, and write concepts. Reads and
writes go straight to the git-backed files (optimistic concurrency, **no locking**),
so every session and repo inherits changes on the next read.

```bash
canonia serve --canon /path/to/canon
```

## Connecting a client

Point any MCP client at the command:

```jsonc
{
  "mcpServers": {
    "canonia": { "command": "canonia", "args": ["serve", "--canon", "/path/to/canon"] }
  }
}
```

The transport is a dependency-free implementation of the MCP stdio protocol
(newline-delimited JSON-RPC 2.0, protocol `2025-06-18`), so it runs anywhere Python
does. Messages are capped at 8M characters per line — an oversized message gets a
JSON-RPC `-32600` error and the server keeps serving. For per-client setup (Claude Code, Claude Desktop, Cursor, VS Code, …) **and**
how to instruct the agent to actually use the canon, see
[using-with-agents](using-with-agents.md).

## Tools

| Tool | Purpose |
|---|---|
| `search` | Search over id/title/summary/tags/body; ranked hits. Keyword by default; **hybrid keyword + semantic** once a [semantic index](indexing.md) is built (`mode: "hybrid"`, results gain a `semantic` score). Filter by `domain`; `include_archived` to include archived. Redirect tombstones are never results. |
| `get` | Fetch a concept: frontmatter, body, and `referenced_by` (backlinks). A merged id transparently **follows the redirect** to its canonical concept unless `follow=false`. |
| `create` | Create a new concept (fails if the id exists). |
| `update` | Update fields on an existing concept; `append_body` adds a paragraph; changing `domain` relocates the file. |
| `list_domains` | Per-domain counts plus totals of archived and redirects. |
| `deprecate` · `merge` · `archive` · `restore` · `remove` | Lifecycle — see [lifecycle](lifecycle.md). |

## Permissive writes, strict gate

Creating a concept that references one that doesn't exist yet **succeeds with a
warning** (authoring order is arbitrary and graphs have cycles). The hard invariant
is the dangling-reference gate you run out-of-band:

```bash
canonia validate            # CI / pre-commit: fails on any unresolved edge
```

So agents can build a cluster incrementally; `validate` stops you *shipping* a canon
with broken edges. See [maintaining](maintaining.md).

## The trust layer: authorship, drafts, and versions

**Autocommit is ON by default** — git is the canon's audit trail. Each write is
committed **locally** (it **never pushes**) with a message like `Create concept
'<id>'`. In a canon that isn't a git repo, writes still succeed and carry a
warning (`git init` to get history). Disable with `git.autocommit: false` or
`serve --no-autocommit`. Concurrent sessions are safe: each commit contains
exactly its own write (never a parallel session's staged files), and transient
`index.lock` collisions are retried; a lock that never clears surfaces as a
warning on the result — the write itself always lands. Concepts authored here get their provenance `repo`
from `canon.name` (default `canon`).

**Identity.** Tell the server who it writes as:

```bash
canonia serve --identity claude-code --identity-kind llm
# or via env (handy in MCP client configs):
CANONIA_IDENTITY=claude-code CANONIA_IDENTITY_KIND=llm canonia serve
```

Commits are then authored `claude-code <llm@canonia>`, so `git log` separates
agent writes from human ones. A **named identity with no explicit kind defaults
to `llm`** — serve is the agent interface, and the safe failure mode is a human
mislabeled as an agent (their creates land as drafts), not the reverse.

**Draft-by-default for agents.** Under an `llm` identity, `create` defaults to
`status: draft` — still searchable and resolvable, just marked as awaiting
human review (promote with `update`/`restore` semantics or edit the file). An
explicit `status: active` in the call is honored; hard enforcement is the
governance module's job.

**Versions (optimistic concurrency).** `get` returns a `version` token (a
content hash). Pass it back as `update`'s `expected_version` and the update is
rejected if the concept changed in between — re-read and re-apply instead of
silently overwriting a concurrent edit. Write results also return the new
`version` so agents can chain edits.

**Timestamps.** The server stamps `created` on create and `updated` (date) on
every write.

## Access control

Reads currently run **open**; the governance module is future work. Both seams
are wired as no-ops: `access.filter_concepts`/`can_access` on every read and
`access.can_write` on every write/remove. Do not expose the server or its canon
to untrusted callers yet — see [deploying](deploying.md).
