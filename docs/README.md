# Canonia — guide

Canonia is a git-backed, MCP-served **knowledge graph for AI coding agents**. One
canonical store of single-topic markdown *concepts* that many repos *reference* (by
`id`) instead of copying. Concepts link to concepts (a graph); a stateless MCP
server is the agent interface; a static site gives humans a browsable view; git
provides versioning + authorship.

> **Status: pre-alpha.** Schema, gates, importer, MCP server, static site, and the
> local semantic index all work today. Access control (governance) is a **future
> module** — see [deploying.md](deploying.md) for how to keep a canon private in the
> meantime. **This matters: the site has no built-in auth.**

## The pieces

| Command | What it does | Guide |
|---|---|---|
| `canonia init` | scaffold a new canon (`canonia.yml`, `concepts/`, `.gitignore`) | [installing](installing.md) |
| `canonia import` | seed a canon from existing repos (curated or zero-config) | [importing](importing.md) |
| `canonia validate` | run the schema + dangling-reference gates | [maintaining](maintaining.md) |
| `canonia index` | build the local semantic index; search / find duplicates | [indexing](indexing.md) |
| `canonia serve` | run the MCP server (agents read/write concepts) | [serving](serving.md) |
| `canonia build` | generate the static site (browsable graph + backlinks) | [deploying](deploying.md) |

Configuration for all of them lives in one file: [configuring.md](configuring.md).
Retiring concepts without breaking links: [lifecycle](lifecycle.md).
Connecting **and instructing** your LLM to use the canon:
[using-with-agents](using-with-agents.md).
Publishing the framework to PyPI: [releasing](releasing.md).

## Quickstart

```bash
pip install canonia

canonia init my-canon --domains process,infra,ops
cd my-canon

# seed from a folder of markdown (zero-config), or a reviewed mapping.yml (curated)
canonia import --zero-config ../some-docs --domain process --commit

canonia validate            # gates must pass
canonia index build         # optional: local semantic index (pip install 'canonia[semantic]')
canonia build               # -> ./site  (serve it PRIVATELY — see deploying.md)
canonia serve               # MCP server on stdio (hybrid search when an index exists)
```

## Core model

One concept per file. Frontmatter carries the machine-readable graph; the body is
prose.

```yaml
---
id: secrets-management          # globally-unique, kebab-case — the reference key
title: Secrets management
domain: infra                   # == the folder the file lives in
status: active                  # active | draft | deprecated | merged | archived
summary: >                      # one line; shown in search hits + neighbour previews
  Git-ignored .env; committed .env.example; day-one rotation.
references:                     # outgoing graph edges (authoritative)
  - security-baseline
source:                         # provenance — where this concept came from
  - repo: ai-playbook
    path: guidelines/secrets_management.md
---

Body markdown. Inline links to other concepts use [[security-baseline]].
```

Two link layers on purpose: `references:` is the authoritative graph (what the
gates and site read); inline `[[id]]` is for human prose. The **dangling-reference
gate** requires every `references:` id and every `[[id]]` to resolve.
