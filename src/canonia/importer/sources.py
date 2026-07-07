# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Read source repos and resolve source-relative links to concept ids.

Given the parsed manifest, this builds the indexes the curated importer needs:

* read a :class:`SourceRef` off disk (with the repo's path prefix applied);
* decide whether a concept's *primary* source is a dedicated whole file or a
  file shared with other concepts (which changes the body strategy);
* resolve a markdown link inside a source file to the concept id it points at,
  so bodies can be rewritten from ``[text](path)`` to ``[[id]]``.
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from typing import Dict, List, Optional, Set

from canonia.config import SourceRepo
from canonia.importer.mapping import ConceptSpec, SourceRef


class SourceResolver:
    """Reads source files and maps ``(repo, path[, anchor])`` -> concept ids."""

    def __init__(self, repos: Dict[str, SourceRepo], specs: List[ConceptSpec]):
        self.repos = repos
        self._cache: Dict[str, Optional[str]] = {}

        # (repo, path, anchor) -> ids  and  (repo, path) -> ids, over ALL sources.
        self._by_full: Dict[tuple, Set[str]] = defaultdict(set)
        self._by_path: Dict[tuple, Set[str]] = defaultdict(set)
        # (repo, path, anchor) -> ids whose *primary* (first) source is exactly this.
        self._primary_owners: Dict[tuple, Set[str]] = defaultdict(set)

        for spec in specs:
            for i, ref in enumerate(spec.sources):
                self._by_full[(ref.repo, ref.path, ref.anchor)].add(spec.id)
                self._by_path[(ref.repo, ref.path)].add(spec.id)
                if i == 0:
                    self._primary_owners[(ref.repo, ref.path, ref.anchor)].add(spec.id)

    # --- file reading -------------------------------------------------------

    def read(self, ref: SourceRef) -> Optional[str]:
        repo = self.repos.get(ref.repo)
        if repo is None:
            return None
        key = f"{ref.repo}:{ref.path}"
        if key not in self._cache:
            path = repo.resolve(ref.path)
            try:
                self._cache[key] = path.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                self._cache[key] = None
        return self._cache[key]

    def exists(self, ref: SourceRef) -> bool:
        return self.read(ref) is not None

    # --- body strategy ------------------------------------------------------

    def primary_co_owners(self, spec: ConceptSpec) -> List[str]:
        """Other concept ids whose primary source is identical to this spec's.

        Covers both whole files (a region file also claimed by its lair) and
        anchored sections (one glossary section claimed by many terms). When
        non-empty the body can't be extracted without duplicating prose across
        concepts, so the caller stubs and flags rather than mangle.
        """
        primary = spec.primary
        if primary is None:
            return []
        owners = self._primary_owners.get((primary.repo, primary.path, primary.anchor), set())
        return sorted(owners - {spec.id})

    # --- link resolution ----------------------------------------------------

    def resolve_link(self, from_ref: SourceRef, target: str) -> Optional[str]:
        """Resolve a markdown link ``target`` (relative to ``from_ref``) to an id.

        Returns the concept id when the link points at exactly one concept's
        source, else ``None`` (the link points outside the canon).
        """
        path_part, _, raw_anchor = target.partition("#")
        anchor = raw_anchor.strip() or None
        # Resolve relative to the directory of the source file, within the repo.
        base_dir = posixpath.dirname(from_ref.path)
        resolved = posixpath.normpath(posixpath.join(base_dir, path_part)) if path_part else from_ref.path
        repo = from_ref.repo

        if anchor:
            ids = self._by_full.get((repo, resolved, anchor))
            if ids and len(ids) == 1:
                return next(iter(ids))
        ids = self._by_path.get((repo, resolved))
        if ids and len(ids) == 1:
            return next(iter(ids))
        return None
