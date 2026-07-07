# Configuring — `canonia.yml`

One file at the canon root binds it to the framework. Every key has a sane default,
so a minimal canon just needs `canon.domains`.

```yaml
# Canonia config — binds this canon to the framework.
canon:
  root: concepts             # folder holding the domain subfolders (default: concepts)
  name: canon                # this canon's repo name; used as the provenance repo for
                             # concepts authored directly here (default: canon)
  domains: [process, infra, ops, lore]   # the top-level domains == concept subfolders

schema:
  id_pattern: "^[a-z0-9][a-z0-9-]*$"      # allowed concept ids (default shown)

git:
  autocommit: false          # when true, `canonia serve` commits each write
                             # (local only — NEVER pushes). Override per run with
                             # `serve --autocommit` / `--no-autocommit`.

mcp:
  name: canonia              # MCP server identity agents connect to

site:
  generator: builtin         # static-site backend (self-contained HTML).
                             # `mkdocs-material` is a reserved future backend.

index:
  backend: sqlite            # embedding index store. 'sqlite' = brute-force NumPy
                             # cosine (implemented). 'sqlite-vec'/'auto' are a
                             # capability-gated seam that falls back to 'sqlite'
                             # today (see indexing.md).

import:                      # source repos the importer reads (dev-time)
  sources:                   # paths are relative to this file (or absolute)
    ai-playbook: {path: ../ai-playbook}
    homelab:     {path: ../homelab}
    shared-lore: {path: ../shared-lore, prefix: canon}   # prefix prepended to mapping paths

# access:                    # RESERVED — governance module (not implemented; v1 open).
#   Future: per-domain / per-identity access control (humans AND LLM identities).
```

## Key reference

| Key | Default | Notes |
|---|---|---|
| `canon.root` | `concepts` | Directory (under the canon root) that holds domain folders. |
| `canon.name` | `canon` | Provenance repo name for concepts authored in the canon (via the MCP `create` tool). |
| `canon.domains` | `[process, lore, infra, ops]` | The valid domains; each is a subfolder of `canon.root`. A concept's `domain` must match its folder. |
| `schema.id_pattern` | `^[a-z0-9][a-z0-9-]*$` | Regex every `id` (and reference) must match. |
| `git.autocommit` | `false` | Commit each MCP write locally. Never pushes. See [serving](serving.md). |
| `mcp.name` | `canonia` | The server identity reported in the MCP handshake. |
| `site.generator` | `builtin` | Static-site backend. |
| `import.sources` | — | Repo name → `{path, prefix}`. Used by curated import; see [importing](importing.md). |

`canonia` finds `canonia.yml` by walking up from the working directory, so you can
run commands from anywhere inside the canon. Override with `--canon <dir>`.
