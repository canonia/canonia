# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Tests for the embedding index — tokenizer, vector store, and hybrid search.

The real ONNX model is never downloaded here: store/search/build tests inject a
deterministic hashing ``FakeModel``, and the server-hybrid test monkeypatches the
model loader. Only the pure-Python tokenizer tests run without NumPy.
"""

import hashlib
from pathlib import Path

import pytest

from canonia import index
from canonia.config import CanoniaConfig
from canonia.schema import Concept

np = pytest.importorskip("numpy")  # store/search need NumPy; tokenizer does not


# --- tokenizer (pure Python, no heavy deps) --------------------------------

def _tok():
    vocab = {
        "[PAD]": 0, "[UNK]": 100, "[CLS]": 101, "[SEP]": 102,
        "continuous": 1, "integration": 2, "test": 3, "##ing": 4,
        "deploy": 5, "##ment": 6, "cafe": 7,
    }
    return index.WordPieceTokenizer(vocab)


def test_tokenizer_wraps_and_wordpieces():
    ids = _tok().encode("Continuous integration testing")
    assert ids[0] == 101 and ids[-1] == 102          # [CLS] … [SEP]
    assert ids[1:-1] == [1, 2, 3, 4]                 # test + ##ing


def test_tokenizer_lowercases_strips_accents_splits_punct():
    tok = _tok()
    assert tok._basic_tokens("Café, TEST!") == ["cafe", ",", "test", "!"]
    # 'cafe' is in vocab; the punctuation and unknown 'test!' pieces resolve too
    assert tok.encode("café")[1] == 7


def test_tokenizer_unknown_word_is_unk():
    # 'xyzzy' has no wordpiece cover -> single [UNK]
    assert _tok().encode("xyzzy") == [101, 100, 102]


# --- a deterministic stand-in for the ONNX model ---------------------------

class FakeModel:
    """Hashing bag-of-words embedder: shared words ⇒ higher cosine. NumPy only."""

    dim = 64

    def _vec(self, text):
        v = np.zeros(self.dim, dtype=np.float32)
        for word in (text or "").lower().split():
            h = int(hashlib.sha1(word.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed(self, texts):
        return np.vstack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_one(self, text):
        return self._vec(text)


def _concept(cid, summary, domain="process", status="active", body=""):
    return Concept(id=cid, title=cid.replace("-", " ").title(), domain=domain,
                   summary=summary, source=[{"repo": "r", "path": f"{cid}.md"}],
                   status=status, body=body)


def _canon(tmp_path: Path) -> CanoniaConfig:
    (tmp_path / "canonia.yml").write_text(
        "canon:\n  root: concepts\n  domains: [process, infra]\n", encoding="utf-8"
    )
    (tmp_path / "concepts" / "process").mkdir(parents=True)
    return CanoniaConfig.load(tmp_path)


# --- vector store ----------------------------------------------------------

def test_store_build_search_and_incremental(tmp_path):
    config = _canon(tmp_path)
    concepts = [
        _concept("alpha", "alpha alpha widgets"),
        _concept("beta", "beta gadgets"),
        _concept("gone", "old merged one", status="merged"),
    ]
    concepts[2].redirect = "alpha"
    model = FakeModel()
    with index.EmbeddingIndex(index.index_path_for(config)) as idx:
        stats = idx.build(concepts, model)
        assert stats.total == 2 and stats.added == 2   # merged tombstone excluded
        hits = idx.search(model.embed_one("alpha widgets"), limit=2)
        assert hits[0][0] == "alpha" and hits[0][1] > hits[1][1]

        # Re-build unchanged ⇒ nothing re-embedded; a body edit ⇒ one update.
        assert idx.build(concepts, model).unchanged == 2
        concepts[0].body = "totally different content now"
        again = idx.build(concepts, model)
        assert again.updated == 1 and again.unchanged == 1

        # Dropping a concept removes its row.
        assert idx.build(concepts[:1], model).removed == 1
        assert len(idx) == 1


def test_duplicate_pairs(tmp_path):
    config = _canon(tmp_path)
    model = FakeModel()
    concepts = [
        _concept("a", "shared shared shared words"),
        _concept("b", "shared shared shared words"),   # identical text ⇒ cosine 1
        _concept("c", "utterly unrelated tokens here"),
    ]
    with index.EmbeddingIndex(index.index_path_for(config)) as idx:
        idx.build(concepts, model)
        pairs = idx.duplicate_pairs(threshold=0.85)   # a,b share all but their title word
    assert len(pairs) == 1 and set(pairs[0][:2]) == {"a", "b"}


def test_concept_text_includes_fields():
    c = _concept("x", "the summary", body="the body")
    c.tags = ["tagged"]
    text = index.concept_text(c)
    assert "summary" in text and "body" in text and "tagged" in text


def test_open_index_absent_returns_none(tmp_path):
    config = _canon(tmp_path)
    assert index.open_index(config) is None       # not built yet
    assert index.index_path_for(config).name == "embeddings.db"


# --- server hybrid wiring (offline: model loader is monkeypatched) ---------

def test_server_search_goes_hybrid_with_index(tmp_path, monkeypatch):
    pytest.importorskip("onnxruntime")   # deps_available() gates the searcher
    from canonia.server import CanonService

    config = _canon(tmp_path)
    for c in [_concept("alpha-widget", "alpha widgets and things"),
              _concept("beta-gadget", "beta gadgets only")]:
        (tmp_path / "concepts" / "process" / f"{c.id}.md").write_text(c.to_markdown(), encoding="utf-8")

    model = FakeModel()
    with index.EmbeddingIndex(index.index_path_for(config)) as idx:
        from canonia.graph import Graph
        idx.build(list(Graph.load(config.concepts_dir).concepts.values()), model)

    # SemanticSearcher would load the real ONNX model; hand it the fake instead.
    monkeypatch.setattr(index.EmbeddingModel, "load", classmethod(lambda cls, *a, **k: model))

    svc = CanonService(tmp_path)
    result = svc.search("alpha widgets", limit=5)
    assert result.get("mode") == "hybrid"
    assert result["results"][0]["id"] == "alpha-widget"
    assert "semantic" in result["results"][0]


def test_server_search_keyword_only_without_index(tmp_path):
    from canonia.server import CanonService

    config = _canon(tmp_path)
    c = _concept("alpha-widget", "alpha widgets")
    (tmp_path / "concepts" / "process" / "alpha-widget.md").write_text(c.to_markdown(), encoding="utf-8")
    result = CanonService(tmp_path).search("alpha", limit=5)
    assert "mode" not in result                       # no index ⇒ plain keyword
    assert isinstance(result["results"][0]["score"], int)
