# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Tests for the markdown→HTML renderer and the static site generator."""

import json
from pathlib import Path

from canonia.markdown_html import render_markdown
from canonia.server import CanonService
from canonia.site import build_site

# --- renderer ---------------------------------------------------------------

def test_render_basic_blocks_and_inline():
    md = "# Title\n\nA **bold** and `code` and *em*.\n\n- one\n- two\n"
    html = render_markdown(md)
    assert "<h2>Title</h2>" in html          # body h1 demoted to h2
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html
    assert "<ul><li>one</li><li>two</li></ul>" in html


def test_render_table_and_code_fence():
    md = "| A | B |\n|---|---|\n| 1 | 2 |\n\n```\nx = 1\n```\n"
    html = render_markdown(md)
    assert "<table>" in html and "<th>A</th>" in html and "<td>1</td>" in html
    assert "<pre><code>x = 1</code></pre>" in html


def test_render_wikilink_resolution_and_escaping():
    def resolver(token):
        return ("foo.html", "Foo Concept", True) if token == "foo" else None
    html = render_markdown("See [[foo]] and [[missing]] and <script>.", resolver)
    assert '<a href="foo.html">Foo Concept</a>' in html
    assert '<span class="broken">missing</span>' in html
    assert "&lt;script&gt;" in html          # escaped, not executable


# --- site build -------------------------------------------------------------

def _canon(tmp_path: Path) -> CanonService:
    (tmp_path / "canonia.yml").write_text(
        "canon:\n  root: concepts\n  name: testcanon\n  domains: [process]\n"
        'schema:\n  id_pattern: "^[a-z0-9][a-z0-9-]*$"\n'
        "git:\n  autocommit: false\n",
        encoding="utf-8",
    )
    (tmp_path / "concepts" / "process").mkdir(parents=True)
    svc = CanonService(tmp_path)
    svc.create(id="ci", title="Continuous integration", domain="process",
               summary="Integrate often.", body="Body links [[testing]].")
    svc.create(id="testing", title="Testing", domain="process",
               summary="Test behaviour.", references=["ci"])
    return svc


def test_build_site_produces_pages_and_index(tmp_path: Path):
    _canon(tmp_path)                                  # scaffold the canon on disk
    out = tmp_path / "site"
    result = build_site(tmp_path, out_dir=out)

    assert (out / "index.html").exists()
    assert (out / "c" / "ci.html").exists()
    assert (out / "style.css").exists()
    assert result["live"] == 2

    ci = (out / "c" / "ci.html").read_text(encoding="utf-8")
    # backlink from testing -> ci is rendered
    assert "testing.html" in ci
    # body wikilink resolved
    assert '<a href="testing.html">Testing</a>' in ci

    index = (out / "index.html").read_text(encoding="utf-8")
    assert "testcanon" in index and "Continuous integration" in index

    records = json.loads((out / "search.json").read_text(encoding="utf-8"))
    assert {r["id"] for r in records} == {"ci", "testing"}


def test_index_page_neutralizes_script_breaking_content(tmp_path: Path):
    # Concept text is agent-writable; a title/summary containing </script>
    # must not be able to close the inline INDEX <script> block (stored XSS).
    svc = _canon(tmp_path)
    payload = "</script><img src=x onerror=alert(1)>"
    svc.create(id="evil", title="Evil", domain="process", summary=payload)

    out = tmp_path / "site"
    build_site(tmp_path, out_dir=out)
    index = (out / "index.html").read_text(encoding="utf-8")

    assert payload not in index                    # raw payload never appears
    assert "\\u003c/script" in index               # escaped inside the JSON
    # the payload still round-trips intact for the search JS
    records = json.loads((out / "search.json").read_text(encoding="utf-8"))
    assert {r["id"]: r for r in records}["evil"]["summary"] == payload


def test_build_site_banners_and_archived_handling(tmp_path: Path):
    svc = _canon(tmp_path)
    svc.create(id="cicd", title="CI/CD", domain="process", summary="dup")
    svc.merge("cicd", into="ci")          # redirect tombstone
    svc.deprecate("testing", superseded_by="ci", reason="folded")
    svc.create(id="old", title="Old", domain="process", summary="stale")
    svc.archive("old")

    out = tmp_path / "site"
    build_site(tmp_path, out_dir=out)

    assert "Merged — redirects to" in (out / "c" / "cicd.html").read_text(encoding="utf-8")
    assert "Deprecated." in (out / "c" / "testing.html").read_text(encoding="utf-8")
    assert (out / "c" / "old.html").exists()          # archived page still generated

    index = (out / "index.html").read_text(encoding="utf-8")
    assert "CI/CD" not in index                        # merged excluded from browse
    assert ">Old<" not in index                        # archived excluded from browse

    records = {r["id"]: r for r in json.loads((out / "search.json").read_text(encoding="utf-8"))}
    assert "cicd" not in records                        # tombstone not searchable
    assert records["old"]["status"] == "archived"       # archived still searchable
