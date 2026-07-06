# Canonia

> A git-backed, MCP-served **knowledge graph for AI coding agents** — one canonical
> source of truth that many repositories reference instead of copying.

**Status: pre-alpha.** This release only reserves the name across registries; the
framework is in active design. Watch this repo for the first working version.

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

## License

[Apache-2.0](./LICENSE) © 2026 André Lopes
