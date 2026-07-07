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
does.

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

## Authorship via git

With `git.autocommit` (in `canonia.yml`) or `serve --autocommit`, each write is
committed **locally** — it **never pushes**. Commit messages read `Create concept
'<id>'` / `Update concept '<id>'`, etc. Concepts authored here get their provenance
`repo` from `canon.name` (default `canon`).

## Access control

The server currently runs **open** (every read allowed). Access control is a future
governance module; the seam (`access.py`) is wired but a no-op. Do not expose the
server or its canon to untrusted callers yet — see [deploying](deploying.md).
