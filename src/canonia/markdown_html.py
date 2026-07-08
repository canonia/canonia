# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""A small, dependency-free Markdown → HTML renderer for the static site.

Not a full CommonMark implementation — it covers what concept bodies actually use
(headings, paragraphs, fenced code, pipe tables, block quotes, ordered/unordered
lists, horizontal rules, and inline emphasis/code/links) and, crucially, resolves
Canonia's ``[[id]]`` wikilinks and ``[text](path)`` links to concept pages via a
caller-supplied resolver. Everything is HTML-escaped first, so output is safe.
"""

from __future__ import annotations

import html
import re
from typing import Callable, List, Optional

# resolver(id_or_target) -> (href, label, known) | None
Resolver = Callable[[str], Optional[tuple]]

_WIKILINK = re.compile(r"\[\[\s*([a-z0-9][a-z0-9-]*)\s*(?:\|([^\]]+))?\]\]")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![\*\w])\*([^*]+)\*(?!\*)")


def render_markdown(text: str, resolver: Optional[Resolver] = None) -> str:
    """Render a markdown ``body`` to an HTML fragment."""
    return _BlockRenderer(resolver).render(text)


class _BlockRenderer:
    def __init__(self, resolver: Optional[Resolver]):
        self.resolver = resolver

    def render(self, text: str) -> str:
        lines = text.replace("\r\n", "\n").split("\n")
        out: List[str] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            # Fenced code block.
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                buf: List[str] = []
                i += 1
                while i < n and not lines[i].strip().startswith(fence):
                    buf.append(lines[i])
                    i += 1
                i += 1  # closing fence
                out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
                continue

            # Horizontal rule.
            if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
                out.append("<hr>")
                i += 1
                continue

            # ATX heading.
            m = re.match(r"(#{1,6})\s+(.*)", stripped)
            if m:
                level = min(6, len(m.group(1)) + 1)  # body h1 -> h2, keep page h1 unique
                out.append(f"<h{level}>{self._inline(m.group(2).rstrip('#').strip())}</h{level}>")
                i += 1
                continue

            # Pipe table (header row followed by a |---|--- separator).
            if "|" in line and i + 1 < n and re.search(r"\|?\s*:?-{2,}", lines[i + 1]) and "|" in lines[i + 1]:
                block, i = self._collect(lines, i, lambda s: "|" in s)
                out.append(self._table(block))
                continue

            # Block quote.
            if stripped.startswith(">"):
                block, i = self._collect(lines, i, lambda s: s.lstrip().startswith(">"))
                inner = "\n".join(re.sub(r"^\s*>\s?", "", b) for b in block)
                out.append("<blockquote>" + self.render(inner) + "</blockquote>")
                continue

            # Lists.
            if re.match(r"[-*+]\s+", stripped):
                items, i = self._list_items(lines, i, ordered=False)
                out.append("<ul>" + "".join(f"<li>{self._inline(it)}</li>" for it in items) + "</ul>")
                continue
            if re.match(r"\d+[.)]\s+", stripped):
                items, i = self._list_items(lines, i, ordered=True)
                out.append("<ol>" + "".join(f"<li>{self._inline(it)}</li>" for it in items) + "</ol>")
                continue

            # Paragraph: gather until blank or a block starter.
            para, i = self._collect(lines, i, lambda s: bool(s.strip()) and not _is_block_start(s))
            out.append("<p>" + self._inline(" ".join(b.strip() for b in para)) + "</p>")

        return "\n".join(out)

    # --- block helpers ------------------------------------------------------

    @staticmethod
    def _collect(lines, i, cond):
        buf = []
        n = len(lines)
        while i < n and cond(lines[i]):
            buf.append(lines[i])
            i += 1
        return buf, i

    def _list_items(self, lines, i, ordered):
        pat = r"\d+[.)]\s+" if ordered else r"[-*+]\s+"
        items = []
        n = len(lines)
        while i < n and re.match(pat, lines[i].strip()):
            items.append(re.sub(pat, "", lines[i].strip(), count=1))
            i += 1
        return items, i

    def _table(self, block: List[str]) -> str:
        rows = [self._split_row(r) for r in block]
        header, body = rows[0], rows[2:]  # rows[1] is the --- separator
        thead = "<tr>" + "".join(f"<th>{self._inline(c)}</th>" for c in header) + "</tr>"
        trs = [
            "<tr>" + "".join(f"<td>{self._inline(c)}</td>" for c in r) + "</tr>"
            for r in body
        ]
        return f"<table><thead>{thead}</thead><tbody>{''.join(trs)}</tbody></table>"

    @staticmethod
    def _split_row(row: str) -> List[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        return [c.strip() for c in row.split("|")]

    # --- inline -------------------------------------------------------------

    def _inline(self, text: str) -> str:
        # Tokenize code spans first so their contents aren't further formatted.
        codes: List[str] = []

        def _stash(m):
            codes.append("<code>" + html.escape(m.group(1)) + "</code>")
            return f"\x00{len(codes) - 1}\x00"

        text = _CODE.sub(_stash, text)
        text = html.escape(text)
        text = _WIKILINK.sub(self._wikilink, text)
        text = _LINK.sub(self._link, text)
        text = _BOLD.sub(r"<strong>\1</strong>", text)
        text = _ITALIC.sub(r"<em>\1</em>", text)
        # Restore code spans.
        text = re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)
        return text

    def _wikilink(self, m) -> str:
        # ``alias`` arrives already HTML-escaped (the whole text is escaped
        # before link substitution); only the resolver's raw title needs it.
        cid, alias = m.group(1), m.group(2)
        if self.resolver:
            resolved = self.resolver(cid)
            if resolved:
                href, title, known = resolved
                # An explicit [[id|alias]] wins; otherwise show the concept title.
                label = alias or (html.escape(title) if title else cid)
                cls = "" if known else ' class="broken"'
                return f'<a href="{href}"{cls}>{label}</a>'
        return f'<span class="broken">{alias or cid}</span>'

    def _link(self, m) -> str:
        # Both groups arrive already HTML-escaped; escaping the target again
        # corrupts '&' in query strings (&amp;amp; -> a literally-broken href).
        label, target = m.group(1), m.group(2).strip()
        if target.startswith(("http://", "https://", "mailto:", "#", "/")):
            return f'<a href="{target}" rel="noopener">{label}</a>'
        # Relative markdown link — try to resolve to a concept page. The
        # resolver expects the author's raw target, not the escaped form.
        if self.resolver:
            resolved = self.resolver(html.unescape(target))
            if resolved:
                href, _title, known = resolved
                cls = "" if known else ' class="broken"'
                return f'<a href="{href}"{cls}>{label}</a>'
        return label  # unresolvable internal link -> plain text


def _is_block_start(line: str) -> bool:
    s = line.strip()
    return (
        s.startswith(("#", ">", "```", "~~~"))
        or bool(re.match(r"[-*+]\s+", s))
        or bool(re.match(r"\d+[.)]\s+", s))
        or bool(re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s))
    )
