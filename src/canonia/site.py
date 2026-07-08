# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Static site — a browsable graph + backlinks for humans.

A dependency-free generator: one self-contained HTML page per concept (rendered
body, outgoing references, backlinks, provenance, and redirect/deprecation
banners), a searchable index, and a theme-aware stylesheet. No external requests,
so the output opens straight from ``file://`` or drops behind an auth-capable edge
(e.g. Cloudflare Access) for the future governance module to gate.

The ``site.generator`` config key is a backend seam; ``builtin`` (this module) is
the default. A ``mkdocs-material`` backend can be added later without changing
call sites.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import List

from canonia import __version__
from canonia.config import CanoniaConfig
from canonia.graph import Graph
from canonia.markdown_html import render_markdown
from canonia.schema import Concept

_STATUS_LABEL = {
    "active": "active",
    "draft": "draft",
    "deprecated": "deprecated",
    "merged": "redirect",
    "archived": "archived",
}


def build_site(canon_dir=".", out_dir=None, **_ignored) -> dict:
    """Generate the static site for the canon at ``canon_dir``.

    Returns a summary dict (out_dir, counts, broken-link count).
    """
    config = CanoniaConfig.load(canon_dir)
    graph = Graph.load(config.concepts_dir)
    out = Path(out_dir).resolve() if out_dir else (config.root_dir / "site")
    (out / "c").mkdir(parents=True, exist_ok=True)

    broken = 0
    pages_root = (out / "c").resolve()
    for concept in graph.concepts.values():
        page, page_broken = _concept_page(concept, graph, config)
        broken += page_broken
        target = out / "c" / f"{concept.id}.html"
        # Ids come from frontmatter the build does not gate; never let one
        # steer a write outside the site directory.
        if not target.resolve().is_relative_to(pages_root):
            raise ValueError(f"concept id {concept.id!r} escapes the site directory")
        target.write_text(page, encoding="utf-8")

    (out / "index.html").write_text(_index_page(graph, config), encoding="utf-8")
    (out / "search.json").write_text(_search_index(graph), encoding="utf-8")
    (out / "style.css").write_text(_CSS, encoding="utf-8")

    live = [c for c in graph.concepts.values() if c.is_live]
    return {
        "out_dir": str(out),
        "concepts": len(graph.concepts),
        "pages": len(graph.concepts) + 1,
        "live": len(live),
        "redirects": sum(1 for c in graph.concepts.values() if c.status == "merged"),
        "archived": sum(1 for c in graph.concepts.values() if c.status == "archived"),
        "broken_links": broken,
    }


# --- concept page -----------------------------------------------------------

def _concept_page(concept: Concept, graph: Graph, config: CanoniaConfig):
    broken = 0

    def resolver(token: str):
        nonlocal broken
        cid = token[:-3] if token.endswith(".md") else token
        cid = cid.rsplit("/", 1)[-1]
        target = graph.concepts.get(cid)
        if target is not None:
            return (f"{cid}.html", target.title, True)
        # a genuine wikilink to a missing id counts as broken
        if token and "/" not in token and "." not in token:
            broken += 1
        return None

    banners = _banners(concept, graph)
    body_html = render_markdown(concept.body, resolver) if concept.body.strip() else ""

    refs = _link_list([r for r in concept.references], graph)
    backlinks = _link_list(graph.effective_backlinks(concept.id), graph)
    sources = "".join(
        f"<li>{html.escape(s.get('repo', '?'))}: <code>{html.escape(s.get('path', ''))}</code></li>"
        for s in concept.source
    )

    sections = []
    if concept.summary:
        sections.append(f'<p class="summary">{html.escape(concept.summary)}</p>')
    for b in banners:
        sections.append(b)
    if body_html:
        sections.append(f'<div class="body">{body_html}</div>')
    sections.append(_section("References", f"<ul>{refs}</ul>" if refs else '<p class="muted">none</p>'))
    sections.append(_section("Referenced by", f"<ul>{backlinks}</ul>" if backlinks else '<p class="muted">none</p>'))
    sections.append(_section("Source", f"<ul>{sources}</ul>" if sources else '<p class="muted">none</p>'))

    badge = _STATUS_LABEL.get(concept.status, concept.status)
    header = (
        f'<nav class="crumbs"><a href="../index.html">{html.escape(config.canon_name)}</a>'
        f' <span>/</span> {html.escape(concept.domain)}</nav>'
        f'<h1>{html.escape(concept.title)} '
        f'<span class="badge s-{concept.status}">{badge}</span></h1>'
    )
    return _document(concept.title, header + "\n".join(sections), depth=1), broken


def _banners(concept: Concept, graph: Graph) -> List[str]:
    out = []
    if concept.redirect:
        target = graph.concepts.get(concept.redirect)
        label = target.title if target else concept.redirect
        out.append(
            f'<div class="banner redirect">Merged — redirects to '
            f'<a href="{html.escape(concept.redirect)}.html">{html.escape(label)}</a>.</div>'
        )
    if concept.status == "deprecated":
        extra = ""
        if concept.superseded_by:
            t = graph.concepts.get(concept.superseded_by)
            lbl = t.title if t else concept.superseded_by
            extra = f' Superseded by <a href="{html.escape(concept.superseded_by)}.html">{html.escape(lbl)}</a>.'
        out.append(f'<div class="banner deprecated">Deprecated.{extra}</div>')
    if concept.status == "archived":
        out.append('<div class="banner archived">Archived — kept for reference, outside the active set.</div>')
    return out


def _link_list(ids: List[str], graph: Graph) -> str:
    parts = []
    for cid in ids:
        c = graph.concepts.get(cid)
        if c is None:
            parts.append(f'<li><span class="broken">{html.escape(cid)}</span></li>')
        else:
            note = ' <span class="muted">(redirect)</span>' if c.status == "merged" else ""
            parts.append(
                f'<li><a href="{html.escape(cid)}.html">{html.escape(c.title)}</a>'
                f'<span class="muted"> — {html.escape(c.domain)}</span>{note}</li>'
            )
    return "".join(parts)


def _section(title: str, inner: str) -> str:
    return f'<section><h2>{html.escape(title)}</h2>{inner}</section>'


# --- index page -------------------------------------------------------------

def _index_page(graph: Graph, config: CanoniaConfig) -> str:
    live = [c for c in graph.concepts.values() if c.is_live]
    archived = sum(1 for c in graph.concepts.values() if c.status == "archived")
    redirects = sum(1 for c in graph.concepts.values() if c.status == "merged")

    stats = (
        f'<p class="muted">{len(live)} concepts · {len(config.domains)} domains'
        f' · {archived} archived · {redirects} redirects</p>'
    )

    domain_blocks = []
    for domain in config.domains:
        items = sorted(
            (c for c in live if c.domain == domain),
            key=lambda c: c.title.lower(),
        )
        if not items:
            continue
        lis = "".join(
            f'<li><a href="c/{html.escape(c.id)}.html">{html.escape(c.title)}</a>'
            f'<span class="muted"> — {html.escape(c.summary)}</span></li>'
            for c in items
        )
        domain_blocks.append(f'<section><h2>{html.escape(domain)} <span class="count">{len(items)}</span></h2><ul class="index-list">{lis}</ul></section>')

    # Concept text is agent-writable: escape `<` so no title/summary can close
    # this inline <script> block (stored XSS), and the JS line separators so
    # they can't break the parse. All three escapes are valid inside JSON.
    index_json = (
        json.dumps(_search_records(graph), ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    search = (
        '<input id="q" type="search" placeholder="Search concepts…" autocomplete="off">'
        '<ul id="results" class="index-list"></ul>'
        f'<script>const INDEX = {index_json};</script>'
        f'<script>{_SEARCH_JS}</script>'
    )
    header = f'<h1>{html.escape(config.canon_name)}</h1>{stats}'
    body = header + search + '<div id="browse">' + "".join(domain_blocks) + "</div>"
    return _document(config.canon_name, body, depth=0)


def _search_records(graph: Graph) -> List[dict]:
    records = []
    for c in graph.concepts.values():
        if c.status == "merged":
            continue  # tombstones aren't search targets
        records.append({
            "id": c.id, "title": c.title, "domain": c.domain,
            "summary": c.summary, "status": c.status,
        })
    records.sort(key=lambda r: r["title"].lower())
    return records


def _search_index(graph: Graph) -> str:
    return json.dumps(_search_records(graph), ensure_ascii=False, indent=2)


# --- document shell ---------------------------------------------------------

def _document(title: str, body: str, *, depth: int) -> str:
    prefix = "../" * depth
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)} · canon</title>\n"
        f'<link rel="stylesheet" href="{prefix}style.css">\n'
        "</head>\n<body>\n<main>\n"
        f"{body}\n"
        f'<footer class="muted">Generated by Canonia {html.escape(__version__)}</footer>\n'
        "</main>\n</body>\n</html>\n"
    )


_SEARCH_JS = """
const q = document.getElementById('q');
const results = document.getElementById('results');
const browse = document.getElementById('browse');
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
q.addEventListener('input', () => {
  const t = q.value.trim().toLowerCase();
  if (!t) { results.innerHTML=''; browse.style.display=''; return; }
  browse.style.display='none';
  const hits = INDEX.filter(r =>
    r.id.includes(t) || r.title.toLowerCase().includes(t) || r.summary.toLowerCase().includes(t)
  ).slice(0, 50);
  results.innerHTML = hits.map(r =>
    `<li><a href="c/${r.id}.html">${esc(r.title)}</a>`
    + `<span class="muted"> — ${esc(r.domain)}${r.status!=='active'?' · '+r.status:''}</span>`
    + `<div class="muted">${esc(r.summary)}</div></li>`
  ).join('') || '<li class="muted">no matches</li>';
});
"""


_CSS = """
:root {
  --bg:#ffffff; --fg:#1a1a1a; --muted:#6b7280; --border:#e5e7eb;
  --link:#2563eb; --code-bg:#f3f4f6; --accent:#2563eb;
  --amber:#b45309; --amber-bg:#fef3c7; --slate:#475569; --slate-bg:#f1f5f9;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0f1115; --fg:#e5e7eb; --muted:#9ca3af; --border:#272b33;
    --link:#60a5fa; --code-bg:#1a1d23; --accent:#60a5fa;
    --amber:#fbbf24; --amber-bg:#3a2e12; --slate:#94a3b8; --slate-bg:#1a1d23;
  }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
a.broken, .broken { color: var(--amber); border-bottom: 1px dotted var(--amber); cursor: help; }
h1 { font-size: 1.9rem; margin: .2rem 0 1rem; line-height:1.2; }
h2 { font-size: 1.15rem; margin: 2rem 0 .5rem; border-bottom:1px solid var(--border); padding-bottom:.3rem; }
h3,h4 { margin: 1.3rem 0 .4rem; }
.crumbs { color: var(--muted); font-size:.9rem; margin-bottom:.5rem; }
.crumbs span { opacity:.5; }
.summary { font-size:1.1rem; color:var(--fg); }
.muted { color: var(--muted); }
.count { font-size:.8rem; color:var(--muted); font-weight:normal; }
code { background:var(--code-bg); padding:.1em .35em; border-radius:4px; font-size:.9em;
  font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
pre { background:var(--code-bg); padding:1rem; border-radius:8px; overflow-x:auto; }
pre code { background:none; padding:0; }
blockquote { margin:1rem 0; padding:.2rem 1rem; border-left:3px solid var(--border); color:var(--muted); }
table { border-collapse:collapse; width:100%; margin:1rem 0; display:block; overflow-x:auto; font-size:.92rem; }
th,td { border:1px solid var(--border); padding:.4rem .6rem; text-align:left; vertical-align:top; }
th { background:var(--code-bg); }
ul.index-list { list-style:none; padding:0; }
ul.index-list li { padding:.35rem 0; border-bottom:1px solid var(--border); }
.badge { font-size:.7rem; text-transform:uppercase; letter-spacing:.04em; padding:.15em .5em;
  border-radius:999px; vertical-align:middle; border:1px solid var(--border); color:var(--muted); }
.s-active { color:#047857; border-color:#047857; }
.s-deprecated { color:var(--amber); border-color:var(--amber); }
.s-archived, .s-merged { color:var(--slate); border-color:var(--slate); }
.banner { padding:.6rem .9rem; border-radius:8px; margin:1rem 0; font-size:.95rem; }
.banner.redirect, .banner.archived { background:var(--slate-bg); color:var(--fg); }
.banner.deprecated { background:var(--amber-bg); color:var(--amber); }
#q { width:100%; padding:.6rem .8rem; font-size:1rem; margin:1rem 0; border:1px solid var(--border);
  border-radius:8px; background:var(--bg); color:var(--fg); }
footer { margin-top:3rem; padding-top:1rem; border-top:1px solid var(--border); font-size:.85rem; }
"""
