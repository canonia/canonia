# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Embedding index — local, offline semantic search + near-duplicate detection.

Concepts are embedded with a small local sentence model (all-MiniLM-L6-v2, 384-d)
run through ONNX Runtime; vectors live in a plain stdlib ``sqlite3`` database as
float32 blobs and search is brute-force cosine in NumPy. At canon scale (hundreds
to low thousands of concepts) that is instant, needs no server, and — crucially —
never leaves the machine, so a **private canon stays private**.

Everything here is *optional*. NumPy + ONNX Runtime are the ``canonia[semantic]``
extra; the model is fetched once from Hugging Face into a local cache. When any
of that is absent the index simply reports itself unavailable and callers fall
back to keyword search — the base install stays dependency-free.

Design notes
------------
* **Backend seam.** ``index.backend`` in ``canonia.yml`` selects the vector store.
  Only ``sqlite`` (this brute-force store) is implemented. ``sqlite-vec`` is left
  as a drop-in for when you run on a Python whose ``sqlite3`` allows loadable
  extensions (macOS system Python does not) and the canon outgrows brute force.
* **Tokenizer.** A dependency-free WordPiece tokenizer (reads the model's
  ``vocab.txt``) — no ``transformers``/``tokenizers`` install, matches BERT-uncased
  preprocessing closely enough for retrieval.
* **Privacy.** The only network call is a one-time fetch of the *public model*
  (never canon content), and only from the explicit ``canonia index build`` path.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import sqlite3
import unicodedata
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# --- optional heavy deps (the canonia[semantic] extra) ----------------------
# NumPy is cheap to import and used throughout, so bind it eagerly. ONNX Runtime
# costs ~1s to import, so it stays lazy (only loaded when a model is actually
# instantiated) — merely importing this module must not pay that cost.
try:  # pragma: no cover - import guard
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None  # type: ignore[assignment]

_ort = None


def _load_ort():
    """Import ONNX Runtime on first use; cache it. Raises if unavailable."""
    global _ort
    if _ort is None:
        _ort = importlib.import_module("onnxruntime")
    return _ort


DEFAULT_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM = 384
MAX_TOKENS = 256
# Where the public ONNX model + vocab are fetched from (never canon content).
_HF_REPO = "Xenova/all-MiniLM-L6-v2"
_HF_FILES = {"model": "onnx/model_quantized.onnx", "vocab": "vocab.txt"}
_HF_URL = "https://huggingface.co/{repo}/resolve/main/{file}"
# Pinned SHA-256 per fetched file, verified before a download lands in the
# cache — a tampered upstream/CDN response can never become the model we run.
# Values match the upstream git-LFS pointers for the repo above @ main.
_HF_SHA256 = {
    "model": "afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1",
    "vocab": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
}

Logger = Callable[[str], None]


def deps_available() -> bool:
    """True when the semantic extra (NumPy + ONNX Runtime) is importable."""
    return _np is not None and importlib.util.find_spec("onnxruntime") is not None


def _require_deps() -> None:
    if not deps_available():
        raise RuntimeError(
            "semantic index needs NumPy + ONNX Runtime — install the extra:\n"
            "    pip install 'canonia[semantic]'"
        )


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
# Only the brute-force ``sqlite`` store (below) is implemented. ``sqlite-vec`` is
# a capability-gated seam: it needs a Python whose ``sqlite3`` was compiled with
# loadable-extension support (a *build* property — macOS system Python lacks it
# at every version) AND the ``sqlite-vec`` package installed. Until that store is
# built, every backend resolves to ``sqlite`` — but resolution reports an honest
# reason so a config author who asked for ``sqlite-vec`` learns exactly why they
# got brute force.

BACKENDS = ("sqlite", "sqlite-vec", "auto")


def sqlite_loadable_extensions() -> bool:
    """True when this interpreter's ``sqlite3`` can load compiled extensions.

    Probed on a throwaway in-memory connection. On a build without support the
    method is either absent (``AttributeError``) or raises when toggled — both
    mean "no". This is a build property of the ``sqlite3`` module, not a version.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
        conn.enable_load_extension(False)
        return True
    except (AttributeError, sqlite3.OperationalError, sqlite3.NotSupportedError):
        return False
    finally:
        conn.close()


def sqlite_vec_available() -> bool:
    """True when the ``sqlite-vec`` package is importable."""
    return importlib.util.find_spec("sqlite_vec") is not None


@dataclass
class BackendChoice:
    """The backend that will actually run, plus why."""

    name: str          # what runs today — always "sqlite" until the vec store lands
    requested: str     # what canonia.yml asked for
    reason: str        # human-readable rationale (esp. on fallback)
    fell_back: bool    # True when the requested backend could not be honored


def resolve_backend(requested: Optional[str]) -> BackendChoice:
    """Resolve a configured ``index.backend`` to the store that will run.

    ``sqlite`` → brute force. ``sqlite-vec`` / ``auto`` → brute force too (the
    vec store is unimplemented), with a reason that distinguishes *why*: no
    loadable-extension support, package not installed, or store-not-built-yet.
    ``auto`` is a silent fallback (it asked us to choose); an explicit
    ``sqlite-vec`` request that we can't honor sets ``fell_back``.
    """
    requested = (requested or "sqlite").strip()

    if requested == "sqlite":
        return BackendChoice("sqlite", requested, "brute-force cosine", False)

    if requested in ("sqlite-vec", "auto"):
        if not sqlite_loadable_extensions():
            why = "this Python's sqlite3 can't load extensions"
        elif not sqlite_vec_available():
            why = "sqlite-vec not installed (pip install sqlite-vec)"
        else:
            why = "sqlite-vec store not yet implemented"
        # `auto` asked us to pick, so its fallback is expected, not a warning.
        return BackendChoice("sqlite", requested, f"{why}; using brute-force", requested != "auto")

    return BackendChoice(
        "sqlite", requested, f"unknown backend '{requested}'; using brute-force", True
    )


# ---------------------------------------------------------------------------
# Model cache + download
# ---------------------------------------------------------------------------

def default_model_dir(model: str = DEFAULT_MODEL) -> Path:
    """Cache dir for a model, overridable via ``$CANONIA_MODEL_DIR``."""
    base = os.environ.get("CANONIA_MODEL_DIR")
    root = Path(base).expanduser() if base else Path.home() / ".cache" / "canonia" / "models"
    return root / model


def ensure_model(
    model_dir: Path, *, log: Optional[Logger] = None, allow_download: bool = True
) -> Dict[str, Path]:
    """Return local paths to the model + vocab, downloading them if missing.

    The files are the *public* MiniLM model, never canon content. With
    ``allow_download=False`` (the ``serve`` path) missing files are left missing
    rather than fetched over the network — the caller degrades to keyword search.
    """
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    paths = {"model": model_dir / "model.onnx", "vocab": model_dir / "vocab.txt"}
    for key, dest in paths.items():
        if dest.exists() and dest.stat().st_size > 0:
            continue
        if not allow_download:
            raise FileNotFoundError(f"model file not cached: {dest} (run `canonia index build`)")
        url = _HF_URL.format(repo=_HF_REPO, file=_HF_FILES[key])
        if log:
            log(f"downloading {_HF_FILES[key]} → {dest} …")
        _download(url, dest, sha256=_HF_SHA256[key])
    return paths


def _download(url: str, dest: Path, sha256: Optional[str] = None) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "canonia-index"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            digest.update(chunk)
            fh.write(chunk)
    if sha256 and digest.hexdigest() != sha256:
        tmp.unlink()
        raise RuntimeError(
            f"checksum mismatch for {url}: got {digest.hexdigest()}, expected {sha256} — "
            "refusing to install the file (upstream changed or the download was tampered with)"
        )
    tmp.replace(dest)


# ---------------------------------------------------------------------------
# WordPiece tokenizer (dependency-free BERT-uncased preprocessing)
# ---------------------------------------------------------------------------

class WordPieceTokenizer:
    """A minimal BERT-uncased WordPiece tokenizer built from ``vocab.txt``."""

    def __init__(self, vocab: Dict[str, int]):
        self.vocab = vocab
        self.unk = vocab.get("[UNK]", 100)
        self.cls = vocab.get("[CLS]", 101)
        self.sep = vocab.get("[SEP]", 102)
        self.pad = vocab.get("[PAD]", 0)

    @classmethod
    def from_file(cls, path: Path) -> WordPieceTokenizer:
        vocab: Dict[str, int] = {}
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                vocab[line.rstrip("\n")] = i
        return cls(vocab)

    def encode(self, text: str, max_tokens: int = MAX_TOKENS) -> List[int]:
        """Text → token ids, wrapped in [CLS] … [SEP] and length-capped."""
        pieces: List[int] = [self.cls]
        # room for [CLS] and [SEP]
        budget = max_tokens - 2
        for word in self._basic_tokens(text):
            if len(pieces) - 1 >= budget:
                break
            pieces.extend(self._wordpiece(word))
        pieces = pieces[: max_tokens - 1]
        pieces.append(self.sep)
        return pieces

    # --- basic tokenization (clean, lowercase, strip accents, split punct) --

    def _basic_tokens(self, text: str) -> List[str]:
        out: List[str] = []
        for token in self._whitespace_split(self._clean(text)):
            token = self._strip_accents(token.lower())
            out.extend(self._split_punct(token))
        return out

    @staticmethod
    def _clean(text: str) -> str:
        out = []
        for ch in text or "":
            cp = ord(ch)
            if cp == 0 or cp == 0xFFFD or _is_control(ch):
                continue
            out.append(" " if _is_whitespace(ch) else ch)
        return "".join(out)

    @staticmethod
    def _whitespace_split(text: str) -> List[str]:
        return text.split()

    @staticmethod
    def _strip_accents(text: str) -> str:
        return "".join(
            ch for ch in unicodedata.normalize("NFD", text)
            if unicodedata.category(ch) != "Mn"
        )

    @staticmethod
    def _split_punct(token: str) -> List[str]:
        out: List[str] = []
        cur: List[str] = []
        for ch in token:
            if _is_punctuation(ch):
                if cur:
                    out.append("".join(cur))
                    cur = []
                out.append(ch)
            else:
                cur.append(ch)
        if cur:
            out.append("".join(cur))
        return out

    # --- wordpiece (greedy longest-match, ## continuation) ------------------

    def _wordpiece(self, word: str) -> List[int]:
        if len(word) > 100:
            return [self.unk]
        ids: List[int] = []
        start = 0
        n = len(word)
        while start < n:
            end = n
            cur = None
            while start < end:
                sub = word[start:end]
                if start > 0:
                    sub = "##" + sub
                if sub in self.vocab:
                    cur = self.vocab[sub]
                    break
                end -= 1
            if cur is None:
                return [self.unk]  # any unmatched piece ⇒ whole word is [UNK]
            ids.append(cur)
            start = end
        return ids


def _is_control(ch: str) -> bool:
    if ch in ("\t", "\n", "\r"):
        return False
    return unicodedata.category(ch).startswith("C")


def _is_whitespace(ch: str) -> bool:
    if ch in (" ", "\t", "\n", "\r"):
        return True
    return unicodedata.category(ch) == "Zs"


def _is_punctuation(ch: str) -> bool:
    cp = ord(ch)
    if (33 <= cp <= 47) or (58 <= cp <= 64) or (91 <= cp <= 96) or (123 <= cp <= 126):
        return True
    return unicodedata.category(ch).startswith("P")


# ---------------------------------------------------------------------------
# Embedding model (ONNX Runtime)
# ---------------------------------------------------------------------------

class EmbeddingModel:
    """Mean-pooled, L2-normalized MiniLM sentence embeddings via ONNX Runtime."""

    def __init__(self, model_path: Path, vocab_path: Path):
        _require_deps()
        ort = _load_ort()
        self.tokenizer = WordPieceTokenizer.from_file(Path(vocab_path))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
        self.session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self.session.get_inputs()}
        outs = self.session.get_outputs()
        # Prefer the token-level hidden state; fall back to the first output.
        self._output_name = next(
            (o.name for o in outs if o.name == "last_hidden_state"), outs[0].name
        )

    @classmethod
    def load(
        cls, model_dir: Path, *, log: Optional[Logger] = None, allow_download: bool = True
    ) -> EmbeddingModel:
        paths = ensure_model(Path(model_dir), log=log, allow_download=allow_download)
        return cls(paths["model"], paths["vocab"])

    def embed(self, texts: Sequence[str], batch_size: int = 32):
        """Embed ``texts`` → an (N, 384) float32 NumPy array of unit vectors."""
        _require_deps()
        vectors = []
        for i in range(0, len(texts), batch_size):
            vectors.append(self._embed_batch(list(texts[i : i + batch_size])))
        if not vectors:
            return _np.zeros((0, EMBED_DIM), dtype=_np.float32)
        return _np.vstack(vectors)

    def embed_one(self, text: str):
        return self.embed([text])[0]

    def _embed_batch(self, texts: List[str]):
        token_lists = [self.tokenizer.encode(t) for t in texts]
        maxlen = max((len(t) for t in token_lists), default=1)
        n = len(texts)
        input_ids = _np.zeros((n, maxlen), dtype=_np.int64)
        mask = _np.zeros((n, maxlen), dtype=_np.int64)
        for r, toks in enumerate(token_lists):
            input_ids[r, : len(toks)] = toks
            mask[r, : len(toks)] = 1
        feed = {"input_ids": input_ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = _np.zeros((n, maxlen), dtype=_np.int64)
        feed = {k: v for k, v in feed.items() if k in self._input_names}
        hidden = self.session.run([self._output_name], feed)[0]  # (n, seq, dim)
        return _mean_pool_normalize(hidden, mask)


def _mean_pool_normalize(hidden, mask):
    m = mask.astype(_np.float32)[..., None]           # (n, seq, 1)
    summed = (hidden.astype(_np.float32) * m).sum(axis=1)
    counts = _np.clip(m.sum(axis=1), 1e-9, None)
    pooled = summed / counts
    norms = _np.linalg.norm(pooled, axis=1, keepdims=True)
    return (pooled / _np.clip(norms, 1e-12, None)).astype(_np.float32)


# ---------------------------------------------------------------------------
# The text of a concept that gets embedded
# ---------------------------------------------------------------------------

def concept_text(concept) -> str:
    """The retrieval text for a concept: title, summary, tags, then body."""
    parts = [concept.title or "", concept.summary or ""]
    if getattr(concept, "tags", None):
        parts.append(" ".join(concept.tags))
    if concept.body:
        parts.append(concept.body)
    return "\n\n".join(p for p in parts if p).strip()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Vector store (stdlib sqlite3 + brute-force NumPy cosine)
# ---------------------------------------------------------------------------

@dataclass
class BuildStats:
    total: int
    added: int
    updated: int
    unchanged: int
    removed: int
    # Rows whose text (hash) was unchanged but whose domain/status metadata
    # moved — refreshed in place, no re-embedding.
    retagged: int = 0


class EmbeddingIndex:
    """A sqlite-backed store of concept vectors with brute-force cosine search.

    Vectors are L2-normalized on write, so cosine similarity is a plain dot
    product. The whole matrix is loaded into memory for a query — fine at canon
    scale, and the ``sqlite-vec`` backend is the seam for when it isn't.
    """

    def __init__(self, db_path: Path, model_name: str = DEFAULT_MODEL):
        _require_deps()
        self.db_path = Path(db_path)
        self.model_name = model_name
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                id      TEXT PRIMARY KEY,
                hash    TEXT NOT NULL,
                domain  TEXT,
                status  TEXT,
                vector  BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> EmbeddingIndex:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- build --------------------------------------------------------------

    def build(
        self,
        concepts: Sequence,
        model: EmbeddingModel,
        *,
        log: Optional[Logger] = None,
    ) -> BuildStats:
        """Incrementally (re)embed ``concepts``; unchanged bodies are skipped.

        Merged redirect tombstones are not indexed (they carry no real body).
        """
        prior = self.conn.execute("SELECT value FROM meta WHERE key = 'model'").fetchone()
        if prior and prior[0] != self.model_name:
            # The stored vectors came from a different model. Two embedding
            # spaces in one matrix silently corrupt every similarity, so wipe
            # and re-embed everything instead of mixing.
            if log:
                log(f"model changed ({prior[0]} → {self.model_name}) — re-embedding everything")
            self.conn.execute("DELETE FROM embeddings")

        indexable = [c for c in concepts if c.status != "merged"]
        want = {c.id: concept_text(c) for c in indexable}
        by_id = {c.id: c for c in indexable}
        have = {
            row[0]: (row[1], row[2], row[3])
            for row in self.conn.execute("SELECT id, hash, domain, status FROM embeddings")
        }

        to_embed = [
            cid for cid, text in want.items()
            if cid not in have or have[cid][0] != _content_hash(text)
        ]
        unchanged = len(want) - len(to_embed)
        added = sum(1 for cid in to_embed if cid not in have)
        updated = len(to_embed) - added

        if to_embed:
            if log:
                log(f"embedding {len(to_embed)} concept(s) ({unchanged} unchanged) …")
            vectors = model.embed([want[cid] for cid in to_embed])
            rows = []
            for cid, vec in zip(to_embed, vectors):
                c = by_id[cid]
                rows.append((cid, _content_hash(want[cid]), c.domain, c.status, _vec_to_blob(vec)))
            self.conn.executemany(
                "INSERT OR REPLACE INTO embeddings (id, hash, domain, status, vector) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )

        # A domain/status-only change (e.g. `update` relocating a concept to
        # another domain) leaves the text — and so the hash — untouched, but
        # the row's metadata drives domain-filtered search: refresh it in
        # place rather than serving stale filters until the text changes.
        embedding = set(to_embed)
        retagged = [
            (c.domain, c.status, cid)
            for cid, c in by_id.items()
            if cid in have and cid not in embedding
            and (have[cid][1], have[cid][2]) != (c.domain, c.status)
        ]
        if retagged:
            if log:
                log(f"retagging {len(retagged)} concept(s) (domain/status moved, text unchanged)")
            self.conn.executemany(
                "UPDATE embeddings SET domain = ?, status = ? WHERE id = ?", retagged
            )

        stale = [cid for cid in have if cid not in want]
        if stale:
            self.conn.executemany("DELETE FROM embeddings WHERE id = ?", [(c,) for c in stale])

        self._set_meta("model", self.model_name)
        self._set_meta("dim", str(EMBED_DIM))
        self.conn.commit()
        return BuildStats(len(want), added, updated, unchanged, len(stale), len(retagged))

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def stored_model(self) -> Optional[str]:
        """The model name the stored vectors were built with (None if unbuilt)."""
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'model'").fetchone()
        return row[0] if row else None

    # --- read ---------------------------------------------------------------

    def __len__(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    def _matrix(self, domain: Optional[str] = None) -> Tuple[List[str], Any]:
        sql = "SELECT id, vector FROM embeddings"
        args: tuple = ()
        if domain:
            sql += " WHERE domain = ?"
            args = (domain,)
        ids, rows = [], []
        for cid, blob in self.conn.execute(sql, args):
            ids.append(cid)
            rows.append(_blob_to_vec(blob))
        if not rows:
            return [], _np.zeros((0, EMBED_DIM), dtype=_np.float32)
        return ids, _np.vstack(rows)

    def search(
        self,
        query_vec,
        limit: int = 10,
        domain: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """Return ``[(concept_id, cosine)]`` for the closest ``limit`` vectors."""
        ids, matrix = self._matrix(domain)
        if not ids:
            return []
        # np.dot, not the @ operator: float32 matmul trips spurious FPE
        # RuntimeWarnings on some NumPy/BLAS builds (2.0.x); np.dot does not.
        sims = _np.dot(matrix, _np.asarray(query_vec, dtype=_np.float32))
        order = _np.argsort(-sims)[: max(1, limit)]
        return [(ids[i], float(sims[i])) for i in order]

    def duplicate_pairs(self, threshold: float = 0.9) -> List[Tuple[str, str, float]]:
        """All concept pairs whose cosine similarity is ≥ ``threshold``."""
        ids, matrix = self._matrix()
        if len(ids) < 2:
            return []
        sims = _np.dot(matrix, matrix.T)  # np.dot avoids float32-matmul FPE warnings
        pairs: List[Tuple[str, str, float]] = []
        n = len(ids)
        for i in range(n):
            for j in range(i + 1, n):
                s = float(sims[i, j])
                if s >= threshold:
                    pairs.append((ids[i], ids[j], s))
        pairs.sort(key=lambda p: -p[2])
        return pairs


def _vec_to_blob(vec) -> bytes:
    return _np.asarray(vec, dtype=_np.float32).tobytes()


def _blob_to_vec(blob: bytes):
    return _np.frombuffer(blob, dtype=_np.float32)


# ---------------------------------------------------------------------------
# High-level entry points (used by the CLI + server)
# ---------------------------------------------------------------------------

def index_path_for(config) -> Path:
    """Where a canon's embedding DB lives (``.canonia/index/embeddings.db``)."""
    custom = getattr(config, "index_path", None)
    if custom:
        p = Path(custom)
        return p if p.is_absolute() else (config.root_dir / p)
    return config.root_dir / ".canonia" / "index" / "embeddings.db"


def open_index(config, *, create: bool = False) -> Optional[EmbeddingIndex]:
    """Open the canon's index if it exists (or ``create``); else ``None``.

    Returns ``None`` — never raises — when the semantic extra is missing or the
    index has not been built, so callers can degrade to keyword search.
    """
    if not deps_available():
        return None
    path = index_path_for(config)
    if not create and not path.exists():
        return None
    try:
        return EmbeddingIndex(path, model_name=getattr(config, "index_model", DEFAULT_MODEL))
    except Exception:  # pragma: no cover - defensive
        return None


def build_index(config, concepts, *, log: Optional[Logger] = None) -> BuildStats:
    """Build/update the canon's embedding index. Requires the semantic extra."""
    _require_deps()
    model = load_model(config, log=log)
    with EmbeddingIndex(index_path_for(config), model_name=getattr(config, "index_model", DEFAULT_MODEL)) as idx:
        return idx.build(concepts, model, log=log)


def load_model(config, *, log: Optional[Logger] = None, allow_download: bool = True) -> EmbeddingModel:
    """Load the canon's embedding model (fetching it once if needed)."""
    return EmbeddingModel.load(_model_dir_for(config), log=log, allow_download=allow_download)


@dataclass
class DupePair:
    """Two near-duplicate concepts and their cosine similarity.

    ``kind`` is ``"within-import"`` (both are being imported) or ``"vs-canon"``
    (``a_id`` is being imported, ``b_id`` already exists in the canon).
    """

    a_id: str
    b_id: str
    score: float
    kind: str


def near_duplicates(
    new_concepts: Sequence,
    model: EmbeddingModel,
    *,
    existing: Optional[Sequence] = None,
    threshold: float = 0.9,
) -> List[DupePair]:
    """Flag near-duplicate concept pairs at/above ``threshold`` (cosine).

    Compares the ``new_concepts`` against each other and, if ``existing`` is
    given, each new concept against the already-in-canon concepts. A new concept
    whose id matches an existing one is an *update*, not a duplicate, so that
    pairing is skipped. Pure/dry-run: computes embeddings in memory, writes
    nothing.
    """
    _require_deps()
    new_ids = [c.id for c in new_concepts]
    if not new_ids:
        return []
    new_vecs = model.embed([concept_text(c) for c in new_concepts])
    pairs: List[DupePair] = []

    n = len(new_ids)
    if n >= 2:
        sims = _np.dot(new_vecs, new_vecs.T)
        for i in range(n):
            for j in range(i + 1, n):
                s = float(sims[i, j])
                if s >= threshold:
                    pairs.append(DupePair(new_ids[i], new_ids[j], s, "within-import"))

    if existing:
        new_id_set = set(new_ids)
        ex = [c for c in existing if c.id not in new_id_set]  # same id ⇒ update
        if ex:
            ex_ids = [c.id for c in ex]
            ex_vecs = model.embed([concept_text(c) for c in ex])
            cross = _np.dot(new_vecs, ex_vecs.T)
            for i in range(n):
                for k in range(len(ex_ids)):
                    s = float(cross[i, k])
                    if s >= threshold:
                        pairs.append(DupePair(new_ids[i], ex_ids[k], s, "vs-canon"))

    pairs.sort(key=lambda p: -p.score)
    return pairs


def _model_dir_for(config) -> Path:
    custom = getattr(config, "index_model_dir", None)
    model = getattr(config, "index_model", DEFAULT_MODEL)
    if custom:
        p = Path(custom).expanduser()
        return p if p.is_absolute() else (config.root_dir / p)
    return default_model_dir(model)


class SemanticSearcher:
    """Server-side helper: embed a query and score concepts by cosine.

    Loads the model lazily and caches it (loading ONNX is the slow part); reopens
    the — static, built-offline — index per call so it never holds a stale handle.
    Never downloads at serve time: a missing model just disables semantic scoring.
    Returns an empty mapping (not an error) whenever anything is unavailable, so
    the server always has keyword search to fall back on.
    """

    def __init__(self, config):
        self.config = config
        self._model: Optional[EmbeddingModel] = None
        # Only viable if the extra is installed AND an index has been built.
        self._ok = deps_available() and index_path_for(config).exists()

    @property
    def available(self) -> bool:
        return self._ok

    def _model_or_none(self) -> Optional[EmbeddingModel]:
        if self._model is None and self._ok:
            try:
                self._model = EmbeddingModel.load(_model_dir_for(self.config), allow_download=False)
            except Exception:
                self._ok = False
        return self._model

    def scores(self, query: str, domain: Optional[str] = None) -> Dict[str, float]:
        """``{concept_id: cosine}`` for the query, or ``{}`` if unavailable."""
        if not self._ok or not (query or "").strip():
            return {}
        model = self._model_or_none()
        if model is None:
            return {}
        idx = open_index(self.config)
        if idx is None:
            return {}
        try:
            stored = idx.stored_model()
            mine = getattr(self.config, "index_model", DEFAULT_MODEL)
            if stored is not None and stored != mine:
                # The index was built with a different model; scoring a query
                # from `mine` against those vectors compares two embedding
                # spaces — nonsense numbers. Degrade to keyword until rebuilt.
                return {}
            qv = model.embed_one(query)
            hits = idx.search(qv, limit=10_000_000, domain=domain)
        except Exception:  # pragma: no cover - defensive
            return {}
        finally:
            idx.close()
        return {cid: score for cid, score in hits}
