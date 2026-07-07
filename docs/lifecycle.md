# Concept lifecycle — retiring without breaking links

In a *reference* graph, other concepts (and other repos) hold a concept's `id`.
Deleting one manufactures the exact dangling links the gate forbids — a "red link".
So Canonia favours **non-breaking** retirement (Wikipedia-style) and gates the one
destructive operation. All are MCP tools (see [serving](serving.md)).

| Tool | Effect | Inbound references |
|---|---|---|
| `deprecate` | `status: deprecated` + optional `superseded_by`; content stays | still resolve |
| `merge` | concept becomes a **redirect tombstone** forwarding to a canonical id | still resolve (through the redirect) |
| `archive` | drops out of search / active counts; stays on disk | still resolve |
| `restore` | brings an archived/deprecated concept back to a live status | — |
| `remove` | **hard delete — refused unless nothing depends on it** | ⚠️ breaks them |

## deprecate

```jsonc
{ "name": "deprecate", "arguments": { "id": "old-way", "superseded_by": "new-way", "reason": "folded" } }
```
Marks it deprecated (still resolves, still linked), records the replacement, and
prepends a short note to the body. Nothing breaks.

## merge (redirect)

```jsonc
{ "name": "merge", "arguments": { "id": "cicd", "into": "continuous-integration", "repoint": false } }
```
`cicd` becomes a redirect: `status: merged`, `redirect: continuous-integration`. The
target **absorbs `cicd`'s provenance** (`source` union). Inbound references keep
resolving because the gate follows redirects, and `get("cicd")` transparently returns
the canonical concept (with `redirected_from`). Backlinks carry through: the target's
`referenced_by` now includes everything that pointed at `cicd`.

With `repoint: true`, referencing concepts are also rewritten to point straight at
the target (avoids a redirect hop). Merging into a concept that is itself a redirect
is refused (no double redirects).

## archive / restore

`archive` hides a concept from search and the site's browse listing but keeps it on
disk and resolvable (references stay valid). `restore` returns it to `active` (or
`draft`). Archiving is for "not part of the active set" without losing the node.

## remove (hard delete — the gated exception)

```jsonc
{ "name": "remove", "arguments": { "id": "typo-dup", "force": false } }
```
Refuses if **anything depends on the concept** — a direct reference, a redirect
target, or a `superseded_by`. The error lists the dependents; prefer `deprecate` or
`merge`. `force: true` deletes anyway and reports exactly what it broke (so you can
fix those referrers, then re-run `validate`).

## The gate follows redirects

`canonia validate` accepts references to redirect tombstones (non-breaking), but
flags **broken or cyclic redirects** and dangling `superseded_by` pointers. So a
merge keeps the canon green; a careless `remove --force` turns the resulting red
links into gate failures you can't miss.
