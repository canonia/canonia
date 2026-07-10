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
import` / `validate` / `index` / `serve` / `build`, curated + zero-config, 127 tests
passing); access.py a no-op seam (reads + writes). MCP tools: search / get / create / update /
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
`docs/indexing.md`). **Security:** the site has NO built-in auth (access
control is deliberately out of the open core) — serve it privately (tailnet/loopback) or behind an auth edge; `.canonia/`
(holds the derived index) is git-ignored. See docs/deploying.md.

## Key decisions

- **Language: Python** — maintainability + AI/data ecosystem fit (`pip install canonia`).
- **License: Apache-2.0** (explicit patent grant).
- **No pessimistic locking** — git merge / optimistic concurrency instead (locking
  judged an anti-pattern for async agent sessions).
- **Reference, not copy.** Two link layers: `references:` frontmatter (the
  authoritative graph) + `[[id]]` inline (human prose).
- **Access control is deliberately OUT of the open core.** It ships open;
  protection is the deployment's job (private serving / auth-capable edge, e.g.
  Cloudflare Access). Keep the seams intact: `domain` on every concept, the no-op
  access filter, the reserved `access:` config namespace. Any access layer built
  on the seams must scope **LLM identities too**, not just humans.

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
  access.py         # SEAM: no-op access filter (core ships without access control)
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

## Behavior notes — measured limits live in docs/performance.md

Measured behavior (July 2026, pre-alpha), stated as facts to design against:
unversioned updates are last-writer-wins; opt-in CAS exists (`get`'s
`version` → `update`'s `expected_version`) and narrows but does not close the
lost-update window under hot same-concept contention — numbers and practical
guidance in `docs/performance.md`. MCP writes embed-on-write into the semantic
index (degradations warn on the write result; `"unindexed": N` in search flags
only external edits/degraded writes until `canonia index build`). Flat ids
make future namespacing a migration — the separator is reserved (`.`/`:`
hard-rejected in every id regardless of `id_pattern`; the namespacing design
itself is future work). **Trust layer (2026-07-09):** autocommit default ON;
`serve --identity NAME --identity-kind llm|human` (or `$CANONIA_IDENTITY`) →
git author `name <kind@canonia>`; named identities default to `llm`; LLM
creates land as `status: draft`; server stamps `created`/`updated`.

## Working agreements

- Keep this file current and concise (<200 lines).
- The maintainer's private reference canon + curated migration mapping live in a
  sibling checkout outside this repo — **never commit private content here.**
