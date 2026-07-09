# Performance & behavior under load

Measured, not assumed. This page reports what canonia actually does at
different canon sizes and under concurrent writers — including the limits and
failure modes — so you can judge whether it fits your deployment before
adopting it. Numbers are a July-2026 snapshot (pre-alpha): macOS, Python 3.9,
numpy 2.0.2, onnxruntime 1.19.2. Synthetic canons were shaped to match a real
production canon (avg 3.4 KB per concept, ~5 references each); concurrency
claims were validated with real multi-process smoke tests, not mocks.

## The short version

- **At realistic canon sizes (hundreds of concepts), everything is fast:**
  hybrid search ~26 ms, `get` ~2 ms, a write including its git autocommit
  ~50 ms.
- **The comfortable envelope is up to a few thousand concepts.** The first
  wall is interactive search latency at ~5–10k concepts (a known, fixable
  hot spot — see below). For scale context: 10k concepts is roughly the size
  of a large company's entire internal wiki.
- **Concurrent readers: unlimited.** No race can corrupt a read — writes are
  atomic and readers can never observe a half-written concept.
- **Concurrent writers: a handful is safe; heavy same-concept contention is
  not.** The failure mode is never corruption or a crash — at worst a
  concurrent update is silently lost to the last writer (git history keeps
  what landed). Details and measured loss rates below.

## Read & search latency by canon size

| Operation | 1k concepts | 10k | 100k (extrapolated) |
|---|---|---|---|
| `get` (warm server) | 11 ms | 157 ms | ~1.5 s |
| `search` (keyword path) | 151 ms | 1.7 s | ~17 s |
| semantic similarity query | 3 ms | 14 ms | ~208 ms |
| graph load, warm / cold | 11 ms / 0.6 s | 134 ms / 4.3 s | ~1.4 s / ~43 s |
| memory footprint | ~9 MB | ~87 MB | ~850 MB |

Two things worth knowing behind those numbers:

- **The parse cache works.** Loading a canon re-parses only changed files
  (stat-signature keyed); warm loads are ~50× faster than cold. External edits
  (your editor, another process, `git pull`) are picked up automatically.
- **Brute-force vector search is not the bottleneck — and won't be.** The
  semantic index does a plain NumPy cosine over all vectors; that's ~7 ms of
  math even at 100k concepts. What actually gets slow first is the *keyword*
  scorer, which currently re-scans concept bodies per query. That plus the
  per-request graph reload defines the ~5–10k wall; both have straightforward
  fixes on the roadmap (an inverted keyword index and a memoized graph). If
  someone tells you this design needs a vector database, the measurements say
  otherwise.

## The semantic index

- Embedding: ~14 ms per concept (batched, local ONNX MiniLM, CPU). A 1k-concept
  index builds in ~14 s; 10k in ~2.3 min. Fully offline after the one-time
  model download (23 MB, SHA-256 pinned).
- Store: ~2 KB per concept in stdlib sqlite (~2 MB at 1k, ~20 MB at 10k).
- MCP writes keep the index in step automatically (embed-on-write). If the
  index can't keep up or isn't available, writes still succeed — search
  results carry an `unindexed` count so staleness is visible, and
  `canonia index build` reconciles.
- Known limits: `canonia index dupes` compares all pairs and is comfortable to
  ~10k concepts (3.9 s) but not far beyond; a server started *before* the
  first `canonia index build` won't pick up semantic search until restarted.

## Site build

~0.7 s at 1k concepts; 73 s at 10k (a known quadratic backlink computation —
the linear fix exists and is roadmapped). The output is static HTML, so build
cost is paid once per publish, not per reader.

## Concurrent writers: guarantees and honest limits

Everything below was validated with real multi-process smoke tests (4/8/16
simultaneous writer processes hammering one canon), not mocks.

**Guaranteed, observed at every writer count tested:**

- **No torn files, ever.** Writes are atomic (temp file + rename); a crash or
  race can never leave a half-written concept on disk or show one to a reader.
- **Create races have exactly one winner.** Two agents creating the same id
  simultaneously: one succeeds, the other gets a clean "already exists" error
  — never a silent clobber.
- **Autocommits don't cross-contaminate.** Each session's commit names exactly
  its own files; at 16 concurrent writers, zero commits swept up another
  session's work.

**Not guaranteed — know these before pointing many agents at one canon:**

- **Unversioned updates are last-writer-wins.** Two agents updating the *same
  concept* at the same time: one side is silently lost (measured: ~2% of
  contended writes at 16 writers; zero observed at 4 writers on disjoint
  concepts). Opt-in compare-and-swap (`get` returns a `version`; pass it as
  `expected_version` to `update`) shrinks this window substantially but does
  not close it under hot contention (measured: 10% of applied CAS updates
  lost when 8 processes hammer a single concept in a tight loop — an
  adversarial workload well beyond normal agent behavior).
- **Provenance can thin out under heavy contention.** Files are never lost,
  but at 16 concurrent writers ~19% of expected autocommits didn't land as
  separate commits (the content is captured by a neighboring commit instead).
  Git-lock serialization also caps sustained throughput at ~34 writes/s per
  canon regardless of writer count.

**Practical guidance:** the intended pattern — several agents across repos,
mostly touching different concepts — is well inside the safe zone (N ≤ ~4
writers measured loss-free). If you expect sustained contention on the *same*
concepts, use CAS (`expected_version`) and treat git history as the recovery
path. This is the documented no-locking design trade-off: agent sessions
never block each other, and the worst case is a recoverable lost update, never
a corrupted canon.

## What this page is not

Latency numbers say the machinery is fast; they don't prove agents retrieve
the *right* concept and do better work with a canon than without one. That
claim needs a retrieval/task eval, which is the top item on the roadmap —
until it lands, treat retrieval quality as unmeasured.
