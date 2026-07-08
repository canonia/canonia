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


def test_build_retags_domain_move_without_reembedding(tmp_path):
    # A domain-only move leaves the text (hash) unchanged; the row's domain
    # drives domain-filtered search, so it must be refreshed in place.
    config = _canon(tmp_path)
    mover = _concept("mover", "some stable text")
    model = FakeModel()
    with index.EmbeddingIndex(index.index_path_for(config)) as idx:
        idx.build([mover], model)
        assert idx.search(model.embed_one("stable text"), domain="process")

        mover.domain = "infra"                      # move; text untouched
        stats = idx.build([mover], model)
        assert stats.retagged == 1
        assert stats.unchanged == 1 and stats.updated == 0  # no re-embedding

        assert idx.search(model.embed_one("stable text"), domain="infra")
        assert not idx.search(model.embed_one("stable text"), domain="process")


def test_build_status_change_is_retagged(tmp_path):
    config = _canon(tmp_path)
    c = _concept("keeper", "stable text here")
    model = FakeModel()
    with index.EmbeddingIndex(index.index_path_for(config)) as idx:
        idx.build([c], model)
        c.status = "deprecated"
        assert idx.build([c], model).retagged == 1
        row = idx.conn.execute("SELECT status FROM embeddings WHERE id='keeper'").fetchone()
        assert row[0] == "deprecated"


def test_build_model_change_forces_full_reembed(tmp_path):
    # Two embedding spaces in one matrix corrupt every similarity — a model
    # switch must wipe and re-embed, not incrementally mix.
    config = _canon(tmp_path)
    concepts = [_concept("a", "alpha text"), _concept("b", "beta text")]
    model = FakeModel()
    path = index.index_path_for(config)
    with index.EmbeddingIndex(path, model_name="old-model") as idx:
        idx.build(concepts, model)
        assert idx.stored_model() == "old-model"
    with index.EmbeddingIndex(path, model_name="new-model") as idx:
        stats = idx.build(concepts, model)          # same texts, new model
        assert stats.added == 2 and stats.unchanged == 0   # full re-embed
        assert idx.stored_model() == "new-model"


def test_searcher_degrades_on_model_mismatch(tmp_path, monkeypatch):
    # An index built with a different model than configured must not be
    # scored against — the searcher degrades to keyword-only instead.
    pytest.importorskip("onnxruntime")
    from canonia.server import CanonService

    config = _canon(tmp_path)
    c = _concept("alpha-widget", "alpha widgets and things")
    (tmp_path / "concepts" / "process" / "alpha-widget.md").write_text(
        c.to_markdown(), encoding="utf-8"
    )
    model = FakeModel()
    with index.EmbeddingIndex(index.index_path_for(config), model_name="some-other-model") as idx:
        from canonia.graph import Graph
        idx.build(list(Graph.load(config.concepts_dir).concepts.values()), model)

    monkeypatch.setattr(index.EmbeddingModel, "load", classmethod(lambda cls, *a, **k: model))
    result = CanonService(tmp_path).search("alpha", limit=5)
    assert "mode" not in result                     # fell back to keyword-only
    assert isinstance(result["results"][0]["score"], int)


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


def test_near_duplicates_within_and_vs_canon():
    model = FakeModel()
    new = [
        _concept("alpha-one", "shared shared shared body"),
        _concept("alpha-two", "shared shared shared body"),   # ~dup of alpha-one
        _concept("lonely", "totally distinct unrelated tokens"),
    ]
    existing = [
        _concept("alpha-canon", "shared shared shared body"),  # ~dup of the alphas
        _concept("alpha-one", "shared shared shared body"),    # same id ⇒ update, skip
    ]
    pairs = index.near_duplicates(new, model, existing=existing, threshold=0.85)
    within = {frozenset((p.a_id, p.b_id)) for p in pairs if p.kind == "within-import"}
    vs = [(p.a_id, p.b_id) for p in pairs if p.kind == "vs-canon"]
    assert frozenset(("alpha-one", "alpha-two")) in within
    assert ("alpha-one", "alpha-canon") in vs and ("alpha-two", "alpha-canon") in vs
    # the same-id update pairing is never reported as a duplicate
    assert all("alpha-one" not in (p.a_id, p.b_id) or p.kind == "within-import"
               for p in pairs if p.b_id == "alpha-one")


def test_near_duplicates_empty_input():
    assert index.near_duplicates([], FakeModel()) == []


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


def test_server_search_does_not_downrank_unindexed_concepts(tmp_path, monkeypatch):
    # A concept created AFTER the last index build has no vector. It must be
    # scored on keywords alone — blending in sim=0 would cap it at half the
    # reachable score, so fresh knowledge would always lose to stale knowledge.
    pytest.importorskip("onnxruntime")
    from canonia.server import CanonService

    config = _canon(tmp_path)
    indexed = _concept("alpha-widget", "alpha widgets and things")
    (tmp_path / "concepts" / "process" / "alpha-widget.md").write_text(
        indexed.to_markdown(), encoding="utf-8"
    )
    model = FakeModel()
    with index.EmbeddingIndex(index.index_path_for(config)) as idx:
        from canonia.graph import Graph
        idx.build(list(Graph.load(config.concepts_dir).concepts.values()), model)

    # ...then a new concept lands via the server, after the build.
    fresh = _concept("alpha-fresh", "alpha fresh things")
    (tmp_path / "concepts" / "process" / "alpha-fresh.md").write_text(
        fresh.to_markdown(), encoding="utf-8"
    )

    monkeypatch.setattr(index.EmbeddingModel, "load", classmethod(lambda cls, *a, **k: model))
    result = CanonService(tmp_path).search("alpha", limit=5)

    assert result.get("mode") == "hybrid"
    assert result.get("unindexed") == 1               # staleness is reported
    rows = {r["id"]: r for r in result["results"]}
    assert set(rows) == {"alpha-widget", "alpha-fresh"}
    assert "semantic" not in rows["alpha-fresh"]      # no fake sim=0 shown
    assert "semantic" in rows["alpha-widget"]
    # equal keyword hits: the fresh concept must NOT be capped below the
    # indexed one (pre-fix it scored 0.5 vs the indexed concept's 0.5 + sim/2)
    assert rows["alpha-fresh"]["score"] >= rows["alpha-widget"]["score"]


def test_server_search_keyword_only_without_index(tmp_path):
    from canonia.server import CanonService

    _canon(tmp_path)                                  # scaffold the canon on disk
    c = _concept("alpha-widget", "alpha widgets")
    (tmp_path / "concepts" / "process" / "alpha-widget.md").write_text(c.to_markdown(), encoding="utf-8")
    result = CanonService(tmp_path).search("alpha", limit=5)
    assert "mode" not in result                       # no index ⇒ plain keyword
    assert isinstance(result["results"][0]["score"], int)


def test_vocab_from_file_strips_crlf(tmp_path: Path):
    # A CRLF-checked-out vocab.txt (Windows autocrlf) must not produce
    # 'token\r' keys — that tokenizes every input to [UNK].
    vocab_path = tmp_path / "vocab.txt"
    vocab_path.write_bytes(b"[PAD]\r\n[UNK]\r\ntest\r\n")
    tok = index.WordPieceTokenizer.from_file(vocab_path)
    assert "test" in tok.vocab and "test\r" not in tok.vocab
