# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Load ``canonia.yml`` — the file that binds a canon to the framework.

A canon repo carries a ``canonia.yml`` at its root declaring where concepts live,
the domain set, the id pattern, and (optionally) where the importer's source
repos are. Everything has a sane default so a bare canon still loads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from canonia.schema import DEFAULT_DOMAINS, DEFAULT_ID_PATTERN

CONFIG_FILENAME = "canonia.yml"


@dataclass
class SourceRepo:
    """A repo the importer reads from: filesystem root + optional path prefix.

    ``prefix`` is prepended to every mapping ``path`` before it hits disk — e.g.
    the lore canon lives under ``shared-lore/canon/`` while the mapping paths are
    written relative to ``canon/``.
    """

    path: Path
    prefix: str = ""

    def resolve(self, rel_path: str) -> Path:
        return self.path / self.prefix / rel_path if self.prefix else self.path / rel_path


@dataclass
class CanoniaConfig:
    """Parsed ``canonia.yml`` plus the directory it was found in."""

    root_dir: Path
    concepts_dir_name: str = "concepts"
    # The canon's own repo name, used as the provenance repo for concepts
    # authored directly in the canon. Configurable because a user may already
    # have a repo by another name; defaults to "canon".
    canon_name: str = "canon"
    domains: List[str] = field(default_factory=lambda: list(DEFAULT_DOMAINS))
    id_pattern: str = DEFAULT_ID_PATTERN
    # Embedding index (canonia[semantic]). `backend`: "sqlite" (brute-force NumPy
    # cosine) is the only implemented store; "sqlite-vec" is a capability-gated
    # seam and "auto" picks the best available — both fall back to brute force
    # today (see index.resolve_backend). `semantic` toggles hybrid search in the
    # server; `hybrid_weight` is the semantic share (0 = keyword only, 1 =
    # semantic only). `model_dir`/`path` override caches.
    index_backend: str = "sqlite"
    index_model: str = "all-MiniLM-L6-v2"
    index_model_dir: Optional[str] = None
    index_path: Optional[str] = None
    index_semantic: bool = True
    index_hybrid_weight: float = 0.5
    mcp_name: str = "canonia"
    # 'builtin' = the self-contained HTML backend (the only one implemented);
    # 'mkdocs-material' etc. are a reserved backend seam.
    site_generator: str = "builtin"
    # Commit each server write to git automatically (local only — never pushes).
    autocommit: bool = False
    sources: Dict[str, SourceRepo] = field(default_factory=dict)

    @property
    def concepts_dir(self) -> Path:
        return self.root_dir / self.concepts_dir_name

    @classmethod
    def find(cls, start: Path) -> Optional[Path]:
        """Walk up from ``start`` looking for a ``canonia.yml``."""
        start = Path(start).resolve()
        for candidate in (start, *start.parents):
            cfg = candidate / CONFIG_FILENAME
            if cfg.is_file():
                return cfg
        return None

    @classmethod
    def load(cls, path_or_dir: Path) -> "CanoniaConfig":
        path = Path(path_or_dir)
        if path.is_dir():
            found = cls.find(path)
            if found is None:
                raise FileNotFoundError(f"no {CONFIG_FILENAME} at or above {path}")
            path = found
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        root_dir = path.parent

        canon = data.get("canon") or {}
        schema = data.get("schema") or {}
        index = data.get("index") or {}
        mcp = data.get("mcp") or {}
        site = data.get("site") or {}
        git = data.get("git") or {}
        imp = data.get("import") or {}

        sources: Dict[str, SourceRepo] = {}
        for name, spec in (imp.get("sources") or {}).items():
            spec = spec or {}
            raw = Path(spec.get("path", name))
            resolved = raw if raw.is_absolute() else (root_dir / raw)
            sources[name] = SourceRepo(path=resolved.resolve(), prefix=spec.get("prefix", ""))

        return cls(
            root_dir=root_dir,
            concepts_dir_name=canon.get("root", "concepts"),
            canon_name=canon.get("name", "canon"),
            domains=list(canon.get("domains", DEFAULT_DOMAINS)),
            id_pattern=schema.get("id_pattern", DEFAULT_ID_PATTERN),
            index_backend=index.get("backend", "sqlite"),
            index_model=index.get("model", "all-MiniLM-L6-v2"),
            index_model_dir=index.get("model_dir"),
            index_path=index.get("path"),
            index_semantic=bool(index.get("semantic", True)),
            index_hybrid_weight=float(index.get("hybrid_weight", 0.5)),
            mcp_name=mcp.get("name", "canonia"),
            site_generator=site.get("generator", "builtin"),
            autocommit=bool(git.get("autocommit", False)),
            sources=sources,
        )
