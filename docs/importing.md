# Importing

`canonia import` seeds a canon from existing repos. It is **dry-run by default**
and **review-then-commit** — it never silently mangles your docs — and it is a
**pure function** of `(sources + manifest)`, so re-running with the same inputs
yields byte-identical files (idempotent). Two modes.

The gate check validates the **predicted post-commit canon**: with `--prune`
that is exactly the emitted set; without it, the emitted concepts overlaid on
what is already on disk (so a reference to an existing concept is not
"dangling", and an id clash with an existing concept at another path is
caught). A failing gate **blocks `--commit`** — nothing is written and the
command exits 1. `--force` writes anyway (the exit code stays 1) for when you
deliberately want the broken intermediate state on disk.

## Zero-config

A folder of markdown → one concept per file. `id` from the file slug, `title` from
the first H1 (or a humanised slug), `summary` from existing frontmatter or the first
paragraph, `references` auto-extracted from `[[id]]` and links to sibling `.md`
files.

When two files' stems slugify to the same id (`setup.md` and `sub/setup.md`),
the colliding files get path-derived ids instead (`setup`, `sub-setup`) — links
follow the final ids, and each rename shows up as a warning in the dry-run plan
so you can rename the source files if you'd rather pick the ids yourself.

```bash
canonia import --zero-config ./some-docs --domain process            # dry-run: shows the plan
canonia import --zero-config ./some-docs --domain process --commit   # write the files
```

Use `--repo NAME` to set the provenance repo recorded in `source:` (default `local`).

## Curated

Consume a reviewed `mapping.yml` manifest — one entry per concept, carrying the
fully-resolved frontmatter (id, title, domain, summary, references, source) plus
your split / merge / dedup decisions.

```bash
canonia import --mapping migration/mapping.yml            # dry-run + gate check
canonia import --mapping migration/mapping.yml --commit   # write the concept files
```

Source repos come from `import.sources` in `canonia.yml` (see
[configuring](configuring.md)), or from the CLI:

```bash
canonia import --mapping mapping.yml \
  --source team-playbook=../team-playbook \
  --source product-docs=../product-docs:canon      # name=path[:prefix]
```

### Manifest shape

```yaml
concepts:
  - id: secrets-management
    title: Secrets management
    domain: infra
    summary: Git-ignored .env; committed .env.example; day-one rotation.
    references: [security-baseline]
    source:                                  # multiple entries when several were DEDUPED into one
      - {repo: team-playbook,  path: guidelines/secrets_management.md}
      - {repo: platform-infra, path: docs/architecture.md#configuration-secrets}
```

Any top-level key whose value is a list of concept entries is consumed (so batches
can live under different keys). A bare-string `source` (e.g. `characters/x.md`) is
allowed for a batch with an implied repo.

### Where bodies come from

The importer extracts each concept's body from its **primary** (first) source:

- **whole file** — a dedicated source file used verbatim (its leading H1 dropped);
- **section** — the heading a `path#anchor` points at, matched by slug/token overlap;
- **stub** — when neither is safe (a source shared by several concepts, or an anchor
  with no matching heading), it emits an honest stub carrying the summary + a
  `<!-- canonia:body-pending -->` marker. **It never fabricates or duplicates prose.**

The dry-run report lists every stub and warning. Fill stubs by adding a
distinguishing `#anchor` to the mapping and re-running (free), or by authoring the
body directly. Markdown links inside extracted bodies are rewritten `[text](path)`
→ `[[id]]` when they point at another concept.

### Catching duplicates before you commit

With the [semantic extra](indexing.md) installed, add `--check-dupes` to any import
to have the dry-run flag **near-duplicate concepts** — before anything is written:

```bash
canonia import --mapping migration/mapping.yml --check-dupes
canonia import --zero-config ./docs --domain process --check-dupes --dupe-threshold 0.85
```

It reports two kinds of overlap (default cosine ≥ 0.9, tune with `--dupe-threshold`):

- **within this import** — two incoming concepts that say nearly the same thing
  (candidates to merge or `dedup` in your `mapping.yml`);
- **vs. existing canon** — an incoming concept that overlaps one already in the
  canon (maybe already covered — review before committing). A concept whose `id`
  matches an existing one is an *update*, not a duplicate, and is never flagged.

It is advisory: it never fails the import, and it is skipped (with a note) if the
extra isn't installed.

### Reconciling deletions (`--prune`)

By default the importer only **adds and updates** — a concept that used to be
produced but no longer is (its source doc was removed, or its `id` changed) is
left behind as an orphan. Add `--prune` to **reconcile**: existing concept files
the sources no longer produce are removed.

```bash
canonia import --mapping migration/mapping.yml --prune             # dry-run: lists what would be removed
canonia import --mapping migration/mapping.yml --prune --commit    # write updates + delete orphans
```

The dry-run lists every file it would delete (`orphan` = id gone; `moved` = same
id re-emitted to a different file, so the old one is stale) — nothing is removed
until `--commit`. Because pruning makes the committed canon **equal to the import
output**, the dry-run's schema + dangling-reference gate is an accurate preview of
the result: if pruning a concept would orphan a reference some surviving concept
still points at, the gate fails *before* anything is written.

`--prune` is opt-in and unforgiving: it deletes any concept under the output dir
the import doesn't produce, **including ones you authored by hand** if they aren't
in your sources. Review the dry-run, and rely on git to recover if needed.

After importing, always run the gates:

```bash
canonia validate            # schema + dangling-reference must pass
```
