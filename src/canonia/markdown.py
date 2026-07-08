# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Markdown + frontmatter helpers shared by the schema, graph, and importer.

Deliberately dependency-light (PyYAML only). None of these functions raise on
malformed input — they degrade to a best-effort result and let the caller
decide, because the importer must never crash on a messy source file.
"""

from __future__ import annotations

import re
from typing import Iterator, List, Optional, Tuple

import yaml

# --- frontmatter ------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A﻿?---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def split_frontmatter(text: str) -> Tuple[dict, str]:
    """Split a document into (frontmatter dict, body).

    Returns ``({}, text)`` when there is no ``---`` fenced frontmatter block.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, text[match.end():]


def dump_frontmatter(meta: dict) -> str:
    """Serialize a frontmatter dict to a ``---`` fenced block (key order kept)."""
    body = yaml.safe_dump(
        meta,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )
    return f"---\n{body}---\n"


# --- headings & sections ----------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*\s*$")
# Inline markup we strip before slugifying a heading: links, emphasis, code.
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_STOPWORDS = {"the", "a", "an", "of", "and", "to", "in", "for", "with"}


def slugify(text: str) -> str:
    """GitHub-ish heading slug: lowercase, non-alphanumerics collapse to '-'."""
    text = _LINK_RE.sub(r"\1", text)
    # Strip emphasis/code markers, but NOT '_' — in file stems it is a word
    # separator (github_repo_management -> github-repo-management), and the
    # non-alphanumeric pass below turns it into '-' either way.
    text = re.sub(r"[`*~]", "", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _stem(token: str) -> str:
    """Light singular/plural normalization so 'realms' matches 'realm'."""
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _tokens(slug: str) -> List[str]:
    # Drop stopwords and length-1 fragments (e.g. the apostrophe 's'), then stem.
    return [_stem(t) for t in slug.split("-") if len(t) > 1 and t not in _STOPWORDS]


def _unfenced_lines(body: str) -> Iterator[Tuple[int, str]]:
    """Yield ``(line_index, line)`` for every line outside a fenced code block."""
    in_fence = False
    fence = ""
    for i, line in enumerate(body.splitlines()):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence = True, marker
            elif stripped.startswith(fence):
                in_fence = False
            continue
        if in_fence:
            continue
        yield i, line


def iter_headings(body: str) -> List[Tuple[int, str, int]]:
    """Return ``(level, text, line_index)`` for every ATX heading in ``body``.

    Fenced code blocks are skipped so a ``#`` comment inside a code sample is
    not mistaken for a heading.
    """
    headings: List[Tuple[int, str, int]] = []
    for i, line in _unfenced_lines(body):
        m = _HEADING_RE.match(line)
        if m:
            headings.append((len(m.group(1)), m.group(2).strip(), i))
    return headings


def extract_section(body: str, anchor: str) -> Optional[str]:
    """Best-effort extraction of the section a URL ``#anchor`` points at.

    The mapping's anchors are provenance hints, not literal GitHub slugs, so we
    match on token overlap and return the highest-confidence heading's section
    (from just after the heading up to the next heading of equal-or-higher
    level). Returns ``None`` when no heading clears the confidence bar, so the
    caller can fall back to a stub rather than emit the wrong prose.
    """
    headings = iter_headings(body)
    if not headings:
        return None
    want = _tokens(slugify(anchor))
    if not want:
        return None

    best: Optional[Tuple[float, int]] = None  # (score, heading list index)
    for idx, (_level, text, _line) in enumerate(headings):
        have = set(_tokens(slugify(text)))
        if not have:
            continue
        matched = sum(1 for t in want if t in have)
        # Fraction of the anchor's meaningful tokens present in the heading.
        score = matched / len(want)
        # Exact slug equality always wins outright.
        if slugify(text) == slugify(anchor):
            score = 1.0
        if best is None or score > best[0]:
            best = (score, idx)

    if best is None or best[0] < 0.6:
        return None

    heading_idx = best[1]
    level = headings[heading_idx][0]
    start_line = headings[heading_idx][2]
    # Find the next heading at the same or shallower level -> section end.
    end_line = len(body.splitlines())
    for level2, _text2, line2 in headings[heading_idx + 1:]:
        if level2 <= level:
            end_line = line2
            break
    lines = body.splitlines()
    section = "\n".join(lines[start_line + 1:end_line]).strip("\n")
    return section or None


# --- inline concept references ([[id]]) -------------------------------------

_INLINE_REF_RE = re.compile(r"\[\[\s*([a-z0-9][a-z0-9-]*)\s*(?:\|[^\]]*)?\]\]")


def extract_inline_refs(body: str) -> List[str]:
    """All concept ids referenced via ``[[id]]`` (or ``[[id|label]]``) syntax.

    Fenced code blocks are skipped (same fence semantics as
    :func:`iter_headings`): a ``[[id]]`` inside a code sample is not a
    reference, so it must not fail the dangling-reference gate or count as a
    remove-blocking dependent.
    """
    refs: List[str] = []
    for _i, line in _unfenced_lines(body):
        refs.extend(_INLINE_REF_RE.findall(line))
    return refs


# --- markdown link rewriting ------------------------------------------------

_MD_LINK_RE = re.compile(r"(?<!\!)\[([^\]]+)\]\(([^)]+)\)")


def rewrite_links(body: str, resolve) -> str:
    """Rewrite ``[text](target)`` links using ``resolve(target) -> id | None``.

    A link whose target resolves to a concept id becomes ``[[id]]``; a link that
    resolves to nothing (points outside the canon) collapses to its plain link
    text. Image links (``![...]``) and autolinks are left untouched.
    """

    def _sub(match: re.Match[str]) -> str:
        text, target = match.group(1), match.group(2).strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return match.group(0)
        concept_id = resolve(target)
        if concept_id:
            return f"[[{concept_id}]]"
        return text

    return _MD_LINK_RE.sub(_sub, body)
