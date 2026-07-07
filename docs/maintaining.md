# Maintaining a canon

## The gates

`canonia validate` is the hard invariant. It runs, over every concept:

- **schema** — frontmatter matches the contract (valid `id`/`domain`/`status`,
  one-line `summary`, at least one `source`, well-formed lifecycle pointers);
- **duplicate ids** — each `id` appears once;
- **dangling references** — every `references:` id, every inline `[[id]]`, and every
  redirect / `superseded_by` target resolves. References to a redirect tombstone are
  fine (non-breaking); broken or cyclic redirects are flagged.

```bash
canonia validate            # exit 0 = clean; exit 1 = issues listed on stderr
```

Run it in **CI** on the canon repo and as a **pre-commit** hook so a canon with
broken edges never merges. Example CI step:

```yaml
- run: pip install canonia && canonia validate
```

## Re-running the importer

The importer is a pure function of `(sources + mapping)`. When the schema evolves or
you refine the manifest, **re-run it** rather than hand-editing migrated files:

```bash
canonia import --mapping migration/mapping.yml --commit
canonia validate
```

Same inputs → identical output. Hand-edits would be lost on the next run; put your
decisions in the mapping instead.

## Retiring concepts

Never hand-delete a concept file — you'll strand every reference to it. Use the
lifecycle tools (`deprecate` / `merge` / `archive`, and gated `remove`). See
[lifecycle](lifecycle.md).

## Git workflow

A canon is a git repo — that's where versioning and authorship come from.

- Commit concept changes like code; the message says *why*.
- The MCP server can commit each write for you (`git.autocommit`) — **local commits
  only, it never pushes**. Push deliberately.
- Keep generated output out of history: `.canonia/` (index) and `site/` are
  gitignored by `canonia init`.
- If the canon holds sensitive material, keep the **repository private** and serve the
  site privately — see [deploying](deploying.md).

## Health checklist

- `canonia validate` is green.
- The import dry-run shows no unexpected stubs (fill them via mapping anchors).
- `canonia build` reports `0 broken wikilink(s)`.
- The site is reachable only by you (verify from an unauthenticated device).
