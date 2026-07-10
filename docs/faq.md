# FAQ

## Why git as the store — and is GitHub okay with this?

**Why git.** Canonia needs versioning, authorship, atomic-ish multi-file
changes, offline operation, and a merge story for concurrent edits — git gives
all of that for free, battle-tested, with tooling every developer and CI
system already has. Concepts are plain markdown files in a repo; there is no
schema migration, no server to run for the data itself, and your canon
remains readable with `cat` twenty years from now. The documented no-locking
design (optimistic concurrency, CAS via `expected_version`, git history as
the recovery path) leans directly on git's model — see
[performance.md](performance.md) for the measured behavior under concurrent
writers.

**GitHub is an optional, swappable remote — not the store.** The persistence
layer is the local git repository. Autocommit (when enabled) commits locally
and **never pushes**. Nothing in canonia knows or cares about GitHub: the
remote can be GitHub, GitLab, Gitea, a bare repo over SSH on your NAS, or
nothing at all. Pushing is how *you* choose to back up and share the canon,
at whatever cadence you choose.

**Is GitHub okay with a canon?** Yes, comfortably. A canon is markdown
documentation — squarely the kind of content GitHub hosts by design (docs
repos, wikis, note vaults). Two common worries, against GitHub's published
limits ([repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits),
[large files](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github),
current as of July 2026):

- **"Won't the commit volume be a problem?"** There is **no commit-count
  limit** — the figures that look like one (e.g. 10,000 commits on the
  Commits tab) are UI display caps, not repository limits. The real activity
  limits are rate-based: 6 pushes/minute and 15 git read ops/second per
  repository. A canon doesn't approach them: writes commit locally, and even
  a busy multi-agent day produces pushes measured in single digits.
- **"Won't the repo get too big?"** The published size limits are
  100 MiB per file (1 MiB recommended), 2 GiB per push, ~10 GB per
  repository on disk. A real 153-concept canon is ~0.5 MB; a concept edit is
  ~2 KB of history. Those limits are three to five orders of magnitude away,
  and a canon that somehow approached them would long since have hit
  canonia's own interactive-search wall (~5–10k concepts, see
  [performance.md](performance.md)) first.

GitHub's acceptable-use line is drawn at *excessive automated bulk activity*
and undue infrastructure strain
([acceptable use policies](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies)) —
spam-scale machine traffic, not a curated knowledge repo that happens to be
written by agents. Canonia's own measured ceiling makes the point: git-lock
serialization caps a canon at ~34 local writes/second regardless of writer
count, and the intended write pattern is a handful of agents making
deliberate, reviewed contributions.

**The rule of thumb:** if your canon ever genuinely needs thousands of
commits a day, that's not a hosting problem — it's a signal the content is
**telemetry, not knowledge**. Event streams, metrics, and machine-generated
records belong in a database. A canon holds the curated, durable knowledge
*about* your systems; its natural write rate is bounded by how fast humans
and agents actually learn things worth keeping.
