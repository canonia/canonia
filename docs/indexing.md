# Indexing — semantic search

`canonia index` builds a local **embedding index** so agents can find concepts by
*meaning*, not just keywords. It powers hybrid search in the MCP server and finds
near-duplicate concepts during maintenance.

Everything here is **optional and local**. Embeddings are computed on your machine
with a small ONNX model; vectors live in a plain SQLite file inside the canon's
git-ignored `.canonia/` directory. **No canon content ever leaves the machine** —
the only network call is a one-time download of the *public* model. Without the
extra installed, search silently falls back to keyword-only.

## Install the extra

```bash
pip install 'canonia[semantic]'      # numpy + onnxruntime
```

The first `canonia index build` downloads the model (all-MiniLM-L6-v2, ~23 MB
quantized ONNX) into `~/.cache/canonia/models/` (override with `$CANONIA_MODEL_DIR`).
After that it runs fully offline.

## Build the index

```bash
canonia index build --canon /path/to/canon
```

Builds are **incremental**: a concept is re-embedded only when its text changes
(tracked by a content hash), and removed concepts are pruned. Re-run it after
importing or editing concepts — or wire it into a git hook / CI step.

```
Index built → /path/to/canon/.canonia/index/embeddings.db
  153 concepts · +4 new · ~2 changed · 147 unchanged · -0 removed
```

Merged redirect tombstones are not indexed (they carry no real body).

## Query it

```bash
canonia index search "how do we keep docs from going stale" --canon .
canonia index search "deploy a service" --domain infra --k 5
canonia index stats
canonia index dupes --threshold 0.9      # near-duplicate concept pairs
```

`dupes` is the maintenance companion to the importer's exact-match dedup: it
surfaces concepts that are *semantically* close (candidates to `merge`).

## Hybrid search in the MCP server

Once an index exists, the server's `search` tool automatically blends keyword and
semantic scores (results gain a `semantic` field and `mode: "hybrid"`). Concepts
that match on meaning but share no keywords now surface. With no index — or without
the extra — `search` is exactly the keyword-only behavior it always was.

The blend is tunable in `canonia.yml`:

```yaml
index:
  semantic: true          # false ⇒ keyword-only even when an index exists
  hybrid_weight: 0.5      # semantic share of the score (0 = keyword, 1 = semantic)
  model: all-MiniLM-L6-v2
  # model_dir: ./models   # override the model cache location
  # path: ./.canonia/index/embeddings.db
  backend: sqlite         # see "backends" below
```

The server reads the index as of the last **build**; concepts written via MCP are
found by keyword immediately but only join semantic results after the next
`canonia index build`.

## Backends

| `backend` | Status | Notes |
|---|---|---|
| `sqlite` | **implemented** | Vectors as float32 blobs in stdlib `sqlite3`; brute-force NumPy cosine. Instant at canon scale (hundreds–thousands of concepts). |
| `sqlite-vec` | reserved seam | For very large canons on a Python whose `sqlite3` allows loadable extensions. macOS system Python does **not** — hence `sqlite` is the default. |

## Privacy

The index is derived from — and reconstructs — your concepts, so treat it like the
canon itself: `.canonia/` is git-ignored by `canonia init` and must never be
committed or published. See [deploying.md](deploying.md).
