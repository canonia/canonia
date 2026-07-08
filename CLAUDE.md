# CLAUDE.md — Canonia

Context for Claude Code sessions in this repo. Read this first.

## What Canonia is

A git-backed, **MCP-served knowledge graph for AI coding agents**. One canonical
store of single-topic markdown "concepts" that many repos *reference* (by `id`)
instead of copying — killing cross-repo documentation duplication + staleness.
Concepts link to concepts (a graph); a stateless MCP server is the agent
interface; a static site gives humans a browsable graph + backlinks; git provides
versioning + authorship.

**Status: pre-alpha, v0.1 feature-complete.** Identity reserved (GitHub org
`canonia`, npm + PyPI `canonia`, Apache-2.0). **schema + graph gates + importer +
MCP server + static site + semantic index + docs guide are functional** (`canonia
import` / `validate` / `index` / `serve` / `build`, curated + zero-config, 78 tests
passing); access.py a no-op seam. MCP tools: search / get / create / update /
list_domains + lifecycle (deprecate / merge / archive / restore / remove). MCP
transport and the site are dependency-free stdlib impls (no `mcp` SDK — needs Python
≥3.10, env is 3.9; site is self-contained HTML, not MkDocs). The **semantic index**
(`canonia index build|search|dupes|stats`) is an optional `canonia[semantic]` extra
(numpy + onnxruntime): local all-MiniLM-L6-v2 ONNX embeddings, a pure-Python
WordPiece tokenizer, float32 vectors in a stdlib `sqlite3` store with brute-force
NumPy cosine — fully offline (a private canon never leaves the box; model is fetched
once). `search` goes hybrid keyword+semantic once an index exists; degrades to
keyword-only without the extra/index. `canonia import --check-dupes` runs the same
near-duplicate detection over an import dry-run (within-import + vs-existing-canon,
advisory) before anything is written. **Deviation from the sqlite-vec plan:** macOS
system Python's `sqlite3` lacks loadable-extension support, so sqlite-vec can't load
— `index.backend: sqlite` (brute force) is the working default; `sqlite-vec` is a
reserved seam for large canons on an extension-capable Python. Docs in `docs/` (see
`docs/indexing.md`). **Security:** the site has NO built-in auth (governance is
future) — serve it privately (tailnet/loopback) or behind an auth edge; `.canonia/`
(holds the derived index) is git-ignored. See docs/deploying.md.

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

One concept per file: frontmatter `id, title, domain, status, source[], summary,
references[]` + markdown body. `id` = globally-unique kebab-case. `references[]` is
the authoritative graph; a **dangling-reference gate** must pass (every referenced
id resolves). `status` ∈ active·draft·deprecated·merged·archived; retirement is
non-breaking (Wikipedia-style): `merged` concepts carry a `redirect` the gate
follows, `deprecated` carry an optional `superseded_by`. Hard delete is gated on
zero dependents.

## v0.1 scope + module layout

```
canonia/
  cli.py            # canonia init | import | validate | serve | build
  config.py         # canonia.yml loader (domains, id pattern, sources, git, canon name)
  schema.py         # concept model + validation (incl. lifecycle fields)
  graph.py          # load concepts, backlinks, redirect resolution, dangling-ref gate
  markdown.py       # frontmatter + slug/section/link helpers (importer + schema)
  markdown_html.py  # dependency-free markdown -> HTML (static site)
  importer/         # canonia import — curated (mapping.yml) + zero-config
  server.py         # MCP server (stdlib stdio): search/get/create/update + lifecycle
  index.py          # semantic index: ONNX MiniLM + WordPiece + sqlite/NumPy cosine
  site.py           # static site — self-contained HTML (not MkDocs; generator seam kept)
  access.py         # SEAM: no-op access filter (governance module later)
docs/               # install / configure / maintain / use guide (dogfooded)
```

Build order: **schema → importer** (seed + validate on real data) → server → site → docs.

## The importer must

- Support two modes: **zero-config** (a folder of md files → one concept per file,
  `id` from the slug, `references` auto-extracted from existing links) and
  **curated** (an optional `mapping.yml` for split / merge / dedup / fork).
- Be **dry-run + review-then-commit** — never silently mangle a user's docs. A
  failing gate **blocks `--commit`** (nothing written; `--force` overrides, exit
  stays 1); the gate validates the predicted post-commit canon (emitted ∪
  existing without `--prune`, emitted alone with it).
- Reconcile deletions on demand: `canonia import --prune` removes concept files
  the sources no longer produce (opt-in, listed in the dry-run; the post-prune
  canon equals the emitted set, so the gate previews the result). Default is
  add/update-only.

## Known issues — READ docs/audit-2026-07.md before changing code

A full July-2026 audit (security + correctness + architecture, findings
reproduced, not speculative) lives in **`docs/audit-2026-07.md`** — read it
before non-trivial work; keep its statuses current when fixing items. Highlights
still true until marked fixed there: concurrency is last-writer-wins (the
"optimistic concurrency" stance has no conflict detection yet); identity/git
attribution is dead code (server always ANONYMOUS, autocommit off by default);
the semantic index goes stale on MCP writes (fresh concepts stay keyword-only,
reported via `"unindexed": N` in search results, until `canonia index build`);
flat ids make future namespacing a migration — reserve a separator before
users exist.

## Working agreements

- Keep this file current and concise (<200 lines).
- The maintainer's private reference canon + curated migration mapping live in a
  sibling checkout outside this repo — **never commit private content here.**
