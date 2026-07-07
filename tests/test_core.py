# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Tests for the markdown helpers, the concept schema, and the graph gates."""

from pathlib import Path

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
        source=[{"repo": "ai-playbook", "path": "guidelines/secrets.md"}],
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
