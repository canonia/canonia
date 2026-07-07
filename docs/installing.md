# Installing

## Requirements

- **Python ≥ 3.9** for the schema, gates, importer, static site, and MCP server.
  The MCP server ships as a dependency-free implementation of the MCP stdio
  protocol, so it runs on 3.9. (The official `mcp` SDK, if you later prefer it,
  needs Python ≥ 3.10 — the tool logic is transport-agnostic and portable to it.)
- **git**, if you want authorship/versioning or `serve --autocommit`.
- Runtime dependency: **PyYAML** only.

## Install

```bash
pip install canonia
```

From source (development):

```bash
git clone https://github.com/canonia/canonia
cd canonia
pip install -e ".[dev]"     # editable install + pytest
pytest -q                   # run the test suite
```

## Create a canon

A *canon* is a git repository holding your concepts and a `canonia.yml` at its root.

```bash
canonia init my-canon --domains process,infra,ops
```

This writes:

```
my-canon/
  canonia.yml            # binds the canon to Canonia (see configuring.md)
  concepts/
    process/ infra/ ops/ # one folder per domain
  .gitignore             # ignores generated output (.canonia/, site/)
```

Then `git init` inside it (a canon is meant to be versioned). Keep it **private**
if it holds sensitive material — see [deploying.md](deploying.md).

Next: [seed it from existing repos](importing.md), or start authoring concepts via
the [MCP server](serving.md).
