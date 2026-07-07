# CLAUDE.md — Canonia

Context for Claude Code sessions in this repo. Read this first.

## What Canonia is

A git-backed, **MCP-served knowledge graph for AI coding agents**. One canonical
store of single-topic markdown "concepts" that many repos *reference* (by `id`)
instead of copying — killing cross-repo documentation duplication + staleness.
Concepts link to concepts (a graph); a stateless MCP server is the agent
interface; a static site gives humans a browsable graph + backlinks; git provides
versioning + authorship.

**Status: pre-alpha.** Identity reserved (GitHub org `canonia`, npm + PyPI
`canonia`, Apache-2.0). Building the importer first — not yet functional.

## Key decisions

- **Language: Python** — maintainability + AI/data ecosystem fit (`pip install canonia`).
- **License: Apache-2.0** (explicit patent grant).
- **No pessimistic locking** — git merge / optimistic concurrency instead (locking
  judged an anti-pattern for async agent sessions).
- **Reference, not copy.** Two link layers: `references:` frontmatter (the
  authoritative graph) + `[[id]]` inline (human prose).
- **Governance (RBAC) is a FUTURE MODULE.** v1 ships open. Leave seams now: `domain`
  on every concept, a no-op access filter in the server, a reserved `access:` config
  namespace, web view behind an auth-capable edge (e.g. Cloudflare Access). Scope
  **LLM identities too**, not just humans.

## Concept schema

One concept per file: frontmatter `id, title, domain, source[], summary,
references[]` + markdown body. `id` = globally-unique kebab-case. `references[]` is
the authoritative graph; a **dangling-reference gate** must pass (every referenced
id resolves).

## v0.1 scope + module layout

```
canonia/
  cli.py        # canonia init | import | serve | build
  schema.py     # concept model + validation
  graph.py      # load concepts, backlinks, dangling-reference gate
  importer/     # canonia import — zero-config heuristics + optional mapping.yml
  server.py     # MCP server: search / get / create / update
  index.py      # embedding index (sqlite-vec)
  site.py       # static site (MkDocs Material)
  access.py     # SEAM: no-op access filter (governance module later)
docs/           # install / configure / maintain / use guide (dogfooded)
```

Build order: **schema → importer** (seed + validate on real data) → server → site → docs.

## The importer must

- Support two modes: **zero-config** (a folder of md files → one concept per file,
  `id` from the slug, `references` auto-extracted from existing links) and
  **curated** (an optional `mapping.yml` for split / merge / dedup / fork).
- Be **dry-run + review-then-commit** — never silently mangle a user's docs.

## Working agreements

- Keep this file current and concise (<200 lines).
- The maintainer's private reference canon + curated migration mapping live in a
  sibling checkout outside this repo — **never commit private content here.**
