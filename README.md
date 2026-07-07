# Canonia

> A git-backed, MCP-served **knowledge graph for AI coding agents** — one canonical
> source of truth that many repositories reference instead of copying.

**Status: pre-alpha.** The concept **schema**, the graph **gates**
(dangling-reference + schema), the **importer** (`canonia import`), the **MCP
server** (`canonia serve`), and the **static site** (`canonia build`) work today;
the embedding index is the remaining stub. Build order: schema → importer →
server → site → docs.

## The idea

Knowledge that AI agents (and humans) rely on tends to get **copied** into every
repository's `CLAUDE.md`/`AGENTS.md`/docs — and then drifts out of sync. Canonia
inverts that:

- **One canon** — a git repository of small, single-concept Markdown files
  (the source of truth). Persisted on GitHub, versioned, mergeable.
- **Reference, not copy** — each consuming repo holds a concept *id*, not a
  duplicated copy.
- **Fetch on demand over MCP** — agents query the Canonia MCP server for a
  concept when they need it, and write updates back so every other session and
  repo inherits them.
- **A graph, not a tree** — concepts link to concepts; backlinks and a browsable
  web view come for free.
- **Versioning & authorship from git** — every change is a commit, so history
  and "who (human or agent) changed what" are built in.

## How it will fit together

```
 producers            serving layer                consumers
 ─────────            ─────────────                ─────────
 Claude / agents ─▶  Canonia MCP server  ─▶  reference a concept id
 you (git commit) ─▶  (search·get·write)  ─▶  read-only web view (graph)
                          │
                    your canon (git repo = source of truth) ─▶ GitHub
```

## The importer (working today)

`canonia import` seeds a canon from existing repos — **dry-run by default**,
review-then-commit, never silently mangling your docs. Two modes:

```bash
# curated: consume a reviewed mapping.yml (split / merge / dedup decisions)
canonia import --mapping migration/mapping.yml          # dry-run: shows the plan + gate result
canonia import --mapping migration/mapping.yml --commit # write the concept files

# zero-config: a folder of markdown -> one concept per file (id from the slug,
# references auto-extracted from existing links)
canonia import --zero-config ./docs --domain process --commit

# gates, any time
canonia validate
```

The importer is a pure function of `(sources + mapping)`, so it is idempotent and
free to re-run as the schema evolves. Bodies it can't safely extract (a source
shared by several concepts, or an anchor with no matching heading) are emitted as
**flagged stubs** carrying the summary + provenance — never a wrong or duplicated
body — for the human to resolve.

## The MCP server (working today)

`canonia serve` runs a stateless [Model Context Protocol](https://modelcontextprotocol.io)
server on stdio, exposing five tools to agents — `search`, `get`, `create`,
`update`, `list_domains`. Reads and writes go straight to the git-backed concept
files (optimistic concurrency, no locking), so every session and repo inherits
changes on the next read. Point an MCP client at it:

```jsonc
// e.g. an MCP client config
{
  "mcpServers": {
    "canonia": { "command": "canonia", "args": ["serve", "--canon", "/path/to/canon"] }
  }
}
```

Every read runs through a governance seam (`access.py`) that is a deliberate
no-op in v1 — the canon ships open, with the hook in place for a future RBAC
module that scopes humans *and* LLM identities. The transport is a dependency-free
implementation of the MCP stdio protocol, so it runs anywhere Python does.

Writes are **permissive**: creating a concept that references one that doesn't
exist yet succeeds with a *warning* (authoring order is arbitrary and graphs have
cycles) — `canonia validate` is the hard dangling-reference gate for CI. With
`git.autocommit` (in `canonia.yml`, or `serve --autocommit`) each write is
committed locally — it **never pushes**. Concepts authored in the canon get their
provenance repo from `canon.name` (default `canon`).

### Concept lifecycle — retire without breaking links

In a *reference* graph, deleting a concept manufactures the exact dangling links
the gate forbids (a "red link"). So the tools favour **non-breaking** retirement,
Wikipedia-style, and gate the destructive one:

| Tool | Effect | Inbound refs |
|---|---|---|
| `deprecate` | `status: deprecated` + optional `superseded_by`; content stays | still resolve |
| `merge` | concept becomes a **redirect** tombstone forwarding to a canonical id (which absorbs its provenance); `get` follows it transparently and backlinks carry through. `repoint=true` also rewrites referrers | still resolve |
| `archive` | drops out of search/active counts, stays on disk | still resolve |
| `remove` | **hard delete — refused unless zero dependents.** `force=true` deletes anyway and reports what breaks | ⚠️ breaks them |

The dangling-reference gate follows redirects, so a merged id keeps resolving; it
flags broken or cyclic redirects and dangling `superseded_by` pointers.

## The static site (working today)

`canonia build` generates a **self-contained** static site — one HTML page per
concept (rendered body, outgoing references, backlinks, provenance, and
redirect/deprecation banners), a domain index, and client-side search:

```bash
canonia build                       # -> <canon>/site/
canonia build --out ./public        # custom output dir
open <canon>/site/index.html        # no server needed — works from file://
```

It has **zero external requests** (inline CSS/JS, no CDN), is theme-aware
(light/dark), and follows redirects when linking — so it opens offline or sits
behind an auth-capable edge (e.g. Cloudflare Access) for the future governance
module to gate. It's a dependency-free backend; `site.generator` in `canonia.yml`
is a seam for adding a `mkdocs-material` backend later.

## License

[Apache-2.0](./LICENSE) © 2026 André Lopes
