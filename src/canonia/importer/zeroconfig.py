# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Zero-config import — a folder of markdown becomes one concept per file.

No manifest required: ``id`` comes from the file slug, ``title`` from the first
H1 (or a humanized slug), ``summary`` from existing frontmatter or the first
paragraph, and ``references`` are auto-extracted from existing links — both
inline ``[[id]]`` and markdown links to sibling ``.md`` files. Still dry-run +
review-then-commit like the curated path.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Dict, List, Optional

from canonia import markdown
from canonia.importer.plan import WHOLE_FILE, EmittedConcept, ImportPlan
from canonia.schema import Concept

_PARAGRAPH_SKIP = ("#", ">", "-", "*", "+", "|", "```", "~~~")


def import_zeroconfig(
    folder,
    *,
    domain: str,
    repo: str = "local",
) -> ImportPlan:
    """Produce an :class:`ImportPlan` from a flat/nested folder of markdown."""
    folder = Path(folder)
    files = sorted(
        p for p in folder.rglob("*.md")
        if not any(part.startswith(".") for part in p.relative_to(folder).parts)
    )

    # First pass: assign ids and index by relative path + basename for links.
    by_rel: Dict[str, str] = {}
    by_base: Dict[str, str] = {}
    entries: List[tuple] = []  # (path, rel, concept_id)
    for path in files:
        rel = path.relative_to(folder).as_posix()
        concept_id = markdown.slugify(path.stem)
        entries.append((path, rel, concept_id))
        by_rel[rel] = concept_id
        by_base.setdefault(path.name, concept_id)

    plan = ImportPlan()
    ids = {cid for _p, _r, cid in entries}

    for path, rel, concept_id in entries:
        text = path.read_text(encoding="utf-8")
        meta, body = markdown.split_frontmatter(text)

        title = meta.get("title") or _first_h1(body) or _humanize(concept_id)
        summary = str(meta.get("summary") or _first_paragraph(body) or title).strip()

        def resolve(target: str, _rel=rel) -> Optional[str]:
            path_part = target.partition("#")[0]
            if not path_part.endswith(".md"):
                return None
            base_dir = posixpath.dirname(_rel)
            resolved = posixpath.normpath(posixpath.join(base_dir, path_part))
            return by_rel.get(resolved) or by_base.get(posixpath.basename(resolved))

        new_body = markdown.rewrite_links(_strip_leading_h1(body), resolve).strip("\n")

        # references = inline [[id]] present + links that resolved to a concept.
        refs = []
        for r in markdown.extract_inline_refs(new_body):
            if r in ids and r != concept_id and r not in refs:
                refs.append(r)

        concept = Concept(
            id=concept_id,
            title=str(title).strip(),
            domain=domain,
            summary=summary.splitlines()[0] if summary else title,
            references=refs,
            source=[{"repo": repo, "path": rel}],
            status=str(meta.get("status") or "active"),
            body=new_body,
        )
        plan.add(EmittedConcept(concept=concept, body_strategy=WHOLE_FILE, body_source=f"{repo}:{rel}"))

    return plan


def _first_h1(body: str) -> Optional[str]:
    for level, text, _line in markdown.iter_headings(body):
        if level == 1:
            return text
    return None


def _first_paragraph(body: str) -> Optional[str]:
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(_PARAGRAPH_SKIP):
            continue
        # Strip markdown links down to their text for a clean one-liner.
        return markdown._LINK_RE.sub(r"\1", line)
    return None


def _strip_leading_h1(body: str) -> str:
    lines = body.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("# "):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
    return "\n".join(lines[i:]).strip("\n")


def _humanize(slug: str) -> str:
    return re.sub(r"-+", " ", slug).strip().capitalize()
