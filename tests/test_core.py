# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Tests for the markdown helpers, the concept schema, and the graph gates."""

from pathlib import Path

import pytest

from canonia import markdown
from canonia.graph import Graph
from canonia.schema import Concept, validate_concept

# --- markdown ---------------------------------------------------------------

def test_split_frontmatter_roundtrip():
    text = "---\nid: x\ntitle: X\n---\nbody here\n"
    meta, body = markdown.split_frontmatter(text)
    assert meta == {"id": "x", "title": "X"}
    assert body == "body here\n"


def test_split_frontmatter_absent():
    meta, body = markdown.split_frontmatter("# just a heading\n")
    assert meta == {} and body == "# just a heading\n"


def test_slugify_treats_underscore_as_separator():
    assert markdown.slugify("github_repo_management") == "github-repo-management"
    assert markdown.slugify("The realm's exchange — oath") == "the-realm-s-exchange-oath"


def test_extract_section_by_exact_and_fuzzy_anchor():
    body = "intro\n\n## Branches & protection\nkeep it green\n\n## Next\nother\n"
    # Exact-ish slug (the '&' collapses) and a fuzzy token match both resolve.
    assert markdown.extract_section(body, "branches-protection") == "keep it green"
    assert markdown.extract_section(body, "no-such-heading") is None


def test_extract_section_stops_at_same_level_heading():
    body = "## Alpha\nalpha body\n### Alpha detail\nsub\n## Beta\nbeta\n"
    assert markdown.extract_section(body, "alpha") == "alpha body\n### Alpha detail\nsub"


def test_inline_refs_and_link_rewrite():
    body = "See [[foo]] and [Bar](bar.md) and [Ext](https://x)."
    assert markdown.extract_inline_refs(body) == ["foo"]
    fenced = "See [[real]].\n\n```md\nlink: [[not-a-ref]]\n```\n\n~~~\n[[also-not]]\n~~~\n"
    assert markdown.extract_inline_refs(fenced) == ["real"]  # fences skipped
    rewritten = markdown.rewrite_links(
        body, lambda t: "bar" if t == "bar.md" else None
    )
    assert "[[bar]]" in rewritten
    assert "[Ext](https://x)" in rewritten  # external link untouched


# --- schema -----------------------------------------------------------------

def _good() -> Concept:
    return Concept(
        id="secrets-management",
        title="Secrets management",
        domain="infra",
        summary="Never commit secrets.",
        references=["security-baseline"],
        source=[{"repo": "team-playbook", "path": "guidelines/secrets.md"}],
        body="Body.",
    )


def test_concept_markdown_roundtrip():
    c = _good()
    reparsed = Concept.from_markdown(c.to_markdown())
    assert reparsed.id == c.id
    assert reparsed.references == c.references
    assert reparsed.source == c.source
    assert reparsed.body.strip() == "Body."


def test_validate_good_concept_has_no_issues():
    assert validate_concept(_good(), domains=("infra",)) == []


def test_created_updated_frontmatter_round_trip():
    text = (
        "---\nid: x\ntitle: X\ndomain: process\nstatus: active\nsummary: s\n"
        "created: 2026-01-01\nupdated: 2026-06-30\nreferences: []\n"
        "source:\n- repo: r\n  path: x.md\n---\nBody.\n"
    )
    c = Concept.from_markdown(text)
    assert c.created is not None and c.updated is not None
    # timestamps are known keys, not "unknown frontmatter"
    assert not [i for i in validate_concept(c, domains=("process",)) if i.field == "frontmatter"]
    # ...and they survive a serialize → parse round trip (the server rewrite path)
    c2 = Concept.from_markdown(c.to_markdown())
    assert str(c2.created) == "2026-01-01"
    assert str(c2.updated) == "2026-06-30"


def test_concept_save_is_atomic_and_leaves_no_temp(tmp_path: Path):
    c = _good()
    target = tmp_path / "x.md"
    c.save(target)
    assert Concept.load(target).id == c.id
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*")) == []  # no stray dotfile temps either


def test_validate_flags_bad_id_domain_and_multiline_summary():
    bad = Concept(
        id="Bad ID",
        title="",
        domain="nope",
        summary="line1\nline2",
        source=[],
    )
    fields = {i.field for i in validate_concept(bad, domains=("infra",))}
    assert {"id", "title", "domain", "summary", "source"} <= fields


# --- graph ------------------------------------------------------------------

def test_graph_dangling_reference_and_backlinks():
    a = Concept(id="a", title="A", domain="process", summary="a",
                source=[{"repo": "r", "path": "a.md"}], references=["b", "ghost"])
    b = Concept(id="b", title="B", domain="process", summary="b",
                source=[{"repo": "r", "path": "b.md"}], body="links [[a]]")
    g = Graph()
    g.add(a)
    g.add(b)
    assert g.backlinks("b") == ["a"]
    issues = g.dangling_references()
    assert any("ghost" in i.message for i in issues)
    assert not any("[[a]]" in i.message for i in issues)  # a exists


def test_graph_load_and_validate(tmp_path: Path):
    root = tmp_path / "concepts" / "process"
    root.mkdir(parents=True)
    (root / "a.md").write_text(_process_concept("a", ["b"]), encoding="utf-8")
    (root / "b.md").write_text(_process_concept("b", []), encoding="utf-8")
    g = Graph.load(tmp_path / "concepts")
    assert len(g) == 2
    assert g.validate(domains=("process",)) == []


def _process_concept(cid: str, refs) -> str:
    c = Concept(
        id=cid, title=cid.upper(), domain="process", summary="s",
        source=[{"repo": "r", "path": f"{cid}.md"}], references=list(refs),
    )
    return c.to_markdown()


def test_default_id_pattern_rejects_malformed_kebab():
    for bad in ("foo-", "-foo", "foo--bar", "foo\n", "foo bar"):
        c = _good()
        c.id = bad
        assert any(i.field == "id" for i in validate_concept(c, domains=["infra"])), bad
    assert validate_concept(_good(), domains=["infra"]) == []  # kebab still fine


def test_reserved_namespace_chars_rejected_despite_loose_pattern():
    # '.'/':' are reserved for future id namespacing. A loosened
    # schema.id_pattern must not be able to mint them into any id position —
    # ids land in filenames/URLs/sqlite PKs, so a retrofit is a migration.
    loose = r"^[a-z0-9.:/-]+$"

    for bad in ("docs.v2", "infra:deploy", "a.b:c"):
        c = _good()
        c.id = bad
        issues = validate_concept(c, domains=["infra"], id_pattern=loose)
        assert any(i.field == "id" and "reserved" in i.message for i in issues), bad

    c = _good()
    c.references = ["infra:deploy"]
    issues = validate_concept(c, domains=["infra"], id_pattern=loose)
    assert any(i.field == "references" and "reserved" in i.message for i in issues)

    c = _good()
    c.status = "merged"
    c.redirect = "docs.v2"
    issues = validate_concept(c, domains=["infra"], id_pattern=loose)
    assert any(i.field == "redirect" and "reserved" in i.message for i in issues)

    c = _good()
    c.status = "deprecated"
    c.superseded_by = "a.b"
    issues = validate_concept(c, domains=["infra"], id_pattern=loose)
    assert any(i.field == "superseded_by" and "reserved" in i.message for i in issues)

    # Ids the loose pattern admits WITHOUT reserved chars still pass.
    c = _good()
    c.id = "still/odd-but-allowed"
    issues = validate_concept(c, domains=["infra"], id_pattern=loose)
    assert not any(i.field == "id" for i in issues)


def test_concept_save_exclusive_refuses_existing(tmp_path: Path):
    c = _good()
    dst = tmp_path / "x.md"
    c.save(dst, exclusive=True)              # nothing there yet: lands
    c.title = "Changed"
    with pytest.raises(FileExistsError):
        c.save(dst, exclusive=True)          # already there: loses loudly
    assert "Changed" not in dst.read_text(encoding="utf-8")
    c.save(dst)                              # non-exclusive still replaces
    assert "Changed" in dst.read_text(encoding="utf-8")
    assert list(tmp_path.glob("*.tmp")) == []


def test_graph_load_skips_reparse_of_unchanged_files(tmp_path: Path, monkeypatch):
    root = tmp_path / "concepts" / "process"
    root.mkdir(parents=True)
    (root / "a.md").write_text(_process_concept("a", []), encoding="utf-8")
    (root / "b.md").write_text(_process_concept("b", []), encoding="utf-8")

    from canonia import markdown
    calls = []
    real = markdown.split_frontmatter
    monkeypatch.setattr(
        markdown, "split_frontmatter", lambda text: (calls.append(1), real(text))[1]
    )

    assert set(Graph.load(tmp_path / "concepts").concepts) == {"a", "b"}
    parsed_cold = len(calls)
    assert parsed_cold == 2
    assert set(Graph.load(tmp_path / "concepts").concepts) == {"a", "b"}
    assert len(calls) == parsed_cold  # warm load: stat only, no re-parse


def test_graph_load_cache_sees_changes_and_deletions(tmp_path: Path):
    root = tmp_path / "concepts" / "process"
    root.mkdir(parents=True)
    (root / "a.md").write_text(_process_concept("a", []), encoding="utf-8")
    (root / "b.md").write_text(_process_concept("b", []), encoding="utf-8")
    g = Graph.load(tmp_path / "concepts")
    assert g.concepts["a"].title == "A"

    changed = _process_concept("a", []).replace("title: A", "title: A but newer")
    (root / "a.md").write_text(changed, encoding="utf-8")
    (root / "b.md").unlink()
    g = Graph.load(tmp_path / "concepts")
    assert g.concepts["a"].title == "A but newer"
    assert "b" not in g.concepts

    # The deleted file's cache entry is pruned, not retained forever.
    from canonia.graph import _parse_cache
    assert root / "b.md" not in _parse_cache


def test_graph_load_hands_out_fresh_objects(tmp_path: Path):
    # Server tools mutate loaded Concepts and validate afterwards — a rejected
    # mutation must never leak into the next load via a cached object.
    root = tmp_path / "concepts" / "process"
    root.mkdir(parents=True)
    (root / "a.md").write_text(_process_concept("a", []), encoding="utf-8")
    g = Graph.load(tmp_path / "concepts")
    g.concepts["a"].title = "mutated in memory"
    g.concepts["a"].references.append("ghost")
    g = Graph.load(tmp_path / "concepts")
    assert g.concepts["a"].title == "A"
    assert g.concepts["a"].references == []


def test_graph_load_skips_hidden_directories(tmp_path: Path):
    root = tmp_path / "concepts"
    ok = Concept(id="ok", title="Ok", domain="process", summary="s",
                 source=[{"repo": "r", "path": "ok.md"}])
    (root / "process").mkdir(parents=True)
    (root / "process" / "ok.md").write_text(ok.to_markdown(), encoding="utf-8")
    (root / ".canonia").mkdir()
    (root / ".canonia" / "sneaky.md").write_text(
        ok.to_markdown().replace("id: ok", "id: sneaky"), encoding="utf-8"
    )
    (root / "process" / ".draft.md").write_text(ok.to_markdown(), encoding="utf-8")
    assert set(Graph.load(root).concepts) == {"ok"}
