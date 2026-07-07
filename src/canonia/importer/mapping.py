# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Parse ``mapping.yml`` — the reviewable migration manifest — into specs.

The manifest carries fully-resolved concept frontmatter (the human already made
every split / merge / dedup call). Two shapes coexist:

* batch-1 (``concepts:``) — ``source: [{repo, path}]``;
* the lore batch (``lore:``) — ``source: [bare/path.md, ...]`` with the repo
  implied (``default_repo``).

Any top-level key whose value is a list of concept dicts is consumed, so future
batches need no code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass(frozen=True)
class SourceRef:
    """One provenance pointer: a repo, a path, and an optional ``#anchor``."""

    repo: str
    path: str
    anchor: Optional[str] = None

    @classmethod
    def parse(cls, raw, default_repo: str) -> SourceRef:
        if isinstance(raw, dict):
            repo = raw.get("repo", default_repo)
            path = raw.get("path", "")
        else:
            repo = default_repo
            path = str(raw)
        anchor = None
        if "#" in path:
            path, anchor = path.split("#", 1)
        return cls(repo=repo, path=path.strip(), anchor=(anchor.strip() or None) if anchor else None)

    @property
    def raw_path(self) -> str:
        """``path`` with the ``#anchor`` reattached (as written in the manifest)."""
        return f"{self.path}#{self.anchor}" if self.anchor else self.path


@dataclass
class ConceptSpec:
    """A resolved concept from the manifest: frontmatter fields + source refs."""

    id: str
    title: str
    domain: str
    summary: str
    references: List[str] = field(default_factory=list)
    sources: List[SourceRef] = field(default_factory=list)

    @property
    def primary(self) -> Optional[SourceRef]:
        return self.sources[0] if self.sources else None


# Maps the manifest's top-level list key -> the repo implied for bare paths.
_DEFAULT_REPO_BY_KEY = {"lore": "shared-lore"}


def load_mapping(path: Path, *, default_repo: Optional[str] = None) -> List[ConceptSpec]:
    """Load every concept entry from ``mapping.yml`` across all batch keys."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    specs: List[ConceptSpec] = []
    seen_ids = set()

    for key, value in data.items():
        if not isinstance(value, list):
            continue
        implied_repo = default_repo or _DEFAULT_REPO_BY_KEY.get(key, "unknown")
        for entry in value:
            if not isinstance(entry, dict) or "id" not in entry:
                continue
            spec = _parse_entry(entry, implied_repo)
            if spec.id in seen_ids:
                raise ValueError(f"duplicate id in mapping: {spec.id!r}")
            seen_ids.add(spec.id)
            specs.append(spec)
    return specs


def _parse_entry(entry: dict, default_repo: str) -> ConceptSpec:
    sources = [SourceRef.parse(s, default_repo) for s in (entry.get("source") or [])]
    return ConceptSpec(
        id=str(entry["id"]).strip(),
        title=str(entry.get("title", "")).strip(),
        domain=str(entry.get("domain", "")).strip(),
        summary=str(entry.get("summary", "")).strip(),
        references=[str(r).strip() for r in (entry.get("references") or [])],
        sources=sources,
    )
