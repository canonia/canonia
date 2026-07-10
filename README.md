# Canonia

> A git-backed, MCP-served **knowledge graph for AI coding agents** — one canonical
> source of truth that many repositories reference instead of copying.

**Status: pre-alpha (v0.1 feature-complete).** Working today: the concept
**schema**, the graph **gates** (schema + dangling-reference), the **importer**
(`canonia import`), the **MCP server** (`canonia serve`), the optional local
**semantic index** (`canonia index`), and the **static site** (`canonia build`).
Access control is deliberately left out of the open core — serve a canon
**privately** (see [docs/deploying.md](docs/deploying.md)).

## The idea

Knowledge that AI agents (and humans) rely on tends to get **copied** into every
repository's `CLAUDE.md` / `AGENTS.md` / docs — and then drifts out of sync.
Canonia inverts that:

- **One canon** — a git repository of small, single-concept Markdown files
  (the source of truth). Versioned, mergeable, authored by humans *and* agents.
- **Reference, not copy** — each consuming repo holds a concept *id*, not a
  duplicated copy.
- **Fetch on demand over MCP** — agents query the Canonia MCP server for a concept
  when they need it, and write updates back so every other session and repo
  inherits them.
- **A graph, not a tree** — concepts link to concepts; backlinks and a browsable
  web view come for free.
- **Versioning & authorship from git** — every change is a commit, so history and
  "who (human or agent) changed what" are built in.

```
 producers            serving layer                consumers
 ─────────            ─────────────                ─────────
 Claude / agents ─▶  Canonia MCP server  ─▶  reference a concept id
 you (git commit) ─▶  (search·get·write)  ─▶  read-only web view (graph)
                          │
                    your canon (git repo = source of truth) ─▶ GitHub
```

## Quickstart

The end-to-end path: install → create a canon → seed it from docs you already
have → make it searchable → point your agent at it → browse it. Each step links to
its full guide.

```bash
# 1. Install (the [semantic] extra adds local embedding search; omit for a lean install)
pip install 'canonia[semantic]'

# 2. Create a canon (a git repo of concepts) and version it
canonia init my-canon --domains process,infra,ops
cd my-canon && git init

# 3. Seed it from markdown you already have — dry-run first, then --commit
canonia import --zero-config ../my-existing-notes --domain process        # preview
canonia import --zero-config ../my-existing-notes --domain process --commit
canonia validate            # schema + dangling-reference gates must pass

# 4. Build the semantic index (enables hybrid keyword+semantic search)
canonia index build

# 5. Point your LLM/agent at the canon over MCP (see the guide for your client)
canonia serve --canon .     # stdio MCP server: search / get / create / update / …

# 6. Browse the graph as a static site (open offline, or serve privately)
canonia build && open site/index.html
```

- **[installing](docs/installing.md)** — requirements, install options, `canonia init`.
- **[importing](docs/importing.md)** — zero-config vs curated `mapping.yml`, dry-run,
  duplicate detection, and `--prune` reconciliation.
- **[using with agents](docs/using-with-agents.md)** — connect *and instruct* your
  LLM (Claude Code, Claude Desktop, Cursor, and other MCP clients) to actually use
  the canon.
- **[indexing](docs/indexing.md)** — the offline semantic index and hybrid search.
- **[performance](docs/performance.md)** — measured latency by canon size and
  behavior under concurrent agents: real numbers, honest limits.
- **[evaluation](docs/evaluation.md)** — does it actually help? 135 real agent
  runs, canon-over-MCP vs "just grep the same files" vs nothing: **+5 points
  success (never worse) at −21% tokens vs grep; +31 points vs no knowledge.**
- **[deploying](docs/deploying.md)** — serve the canon **privately** (no built-in
  auth).

## What each command does

| Command | Purpose | Guide |
|---|---|---|
| `canonia init` | scaffold a canon (`canonia.yml`, `concepts/`, `.gitignore`) | [installing](docs/installing.md) |
| `canonia import` | seed a canon from existing repos (curated or zero-config; `--prune` to reconcile) | [importing](docs/importing.md) |
| `canonia validate` | run the schema + dangling-reference gates | [maintaining](docs/maintaining.md) |
| `canonia index` | build the local semantic index; search / find duplicates | [indexing](docs/indexing.md) |
| `canonia serve` | run the MCP server agents read/write concepts through | [using with agents](docs/using-with-agents.md) |
| `canonia build` | generate the static site (browsable graph + backlinks) | [deploying](docs/deploying.md) |

Configuration for all of them lives in one file, `canonia.yml`
([configuring](docs/configuring.md)). Concepts retire **without breaking links**
(Wikipedia-style deprecate / merge / archive) — see [lifecycle](docs/lifecycle.md).

## Design notes

- **Reference, not copy** — two link layers: `references:` frontmatter (the
  authoritative graph the gate enforces) + `[[id]]` inline links in prose.
- **No locking** — git merge / optimistic concurrency instead, so async agent
  sessions never block each other. Measured under real concurrent writers —
  guarantees and limits in [performance](docs/performance.md).
- **Permissive writes, strict gate** — agents can create a concept that references
  one that doesn't exist yet (a warning); `canonia validate` is the hard gate for CI.
- **Fully offline & private** — the semantic index runs a local ONNX model; a
  private canon never leaves your machine. The site makes **zero external requests**.
- **Open by design, private by deployment** — the core ships without access
  control; a no-op access seam (`access.py`) is wired on every read and write so
  an access layer can attach without forking the core.

## Contributing / from source

```bash
git clone https://github.com/canonia/canonia && cd canonia
pip install -e ".[dev]"     # editable install + test/lint deps
pytest -q                   # test suite
ruff check src tests && mypy # lint + type gate (also enforced in CI)
```

## License

[Apache-2.0](./LICENSE) © 2026 André Lopes
