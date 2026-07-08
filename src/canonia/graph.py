# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""The concept graph — load a canon, compute backlinks, run the gates.

The :class:`Graph` loads every ``concepts/**/*.md`` file into :class:`Concept`
objects, indexes them by id, and answers the two questions the framework leans
on everywhere:

* **backlinks** — who references this concept (the reverse of ``references:``);
* **the dangling-reference gate** — does every ``references:`` id and every
  inline ``[[id]]`` resolve to a concept that exists? This is what proves the
  graph survived an import/split without breaking edges.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from canonia.schema import (
    DEFAULT_DOMAINS,
    DEFAULT_ID_PATTERN,
    Concept,
    Issue,
    validate_concept,
)


@dataclass
class Graph:
    """An in-memory view of a canon: concepts by id, forward + back edges."""

    concepts: Dict[str, Concept] = field(default_factory=dict)
    # id -> list of (file path, concept id) for concepts that failed to get a
    # unique id (duplicates); kept so the gate can report them.
    duplicates: List[Concept] = field(default_factory=list)

    # --- loading ------------------------------------------------------------

    @classmethod
    def load(cls, concepts_root: Path) -> Graph:
        concepts_root = Path(concepts_root)
        graph = cls()
        for path in sorted(concepts_root.rglob("*.md")):
            # Skip hidden files AND files under hidden directories (.git,
            # .canonia, editor scratch dirs) — they are not canon content.
            if any(part.startswith(".") for part in path.relative_to(concepts_root).parts):
                continue
            concept = Concept.load(path)
            if concept.id and concept.id not in graph.concepts:
                graph.concepts[concept.id] = concept
            else:
                graph.duplicates.append(concept)
        return graph

    def add(self, concept: Concept) -> None:
        if concept.id in self.concepts:
            self.duplicates.append(concept)
        else:
            self.concepts[concept.id] = concept

    # --- queries ------------------------------------------------------------

    def __contains__(self, concept_id: str) -> bool:
        return concept_id in self.concepts

    def __len__(self) -> int:
        return len(self.concepts)

    def ids(self) -> Iterable[str]:
        return self.concepts.keys()

    def resolve(self, concept_id: str, _seen=None) -> Optional[str]:
        """Follow a redirect chain to the terminal (non-redirect) id.

        Returns the concept_id itself if it isn't a redirect, the ultimate
        target if it is, or ``None`` if the chain is broken or cyclic.
        """
        _seen = _seen or set()
        concept = self.concepts.get(concept_id)
        if concept is None:
            return None
        if not concept.redirect:
            return concept_id
        if concept_id in _seen:
            return None  # cycle
        _seen.add(concept_id)
        return self.resolve(concept.redirect, _seen)

    def backlinks(self, concept_id: str) -> List[str]:
        """Ids of concepts whose ``references:`` include ``concept_id`` directly."""
        return sorted(
            cid
            for cid, c in self.concepts.items()
            if concept_id in c.references
        )

    def effective_backlinks(self, concept_id: str) -> List[str]:
        """Ids that reference ``concept_id`` directly *or through a redirect*.

        A concept C that references A, where A redirects to ``concept_id``, is an
        effective backlink — this is what ``get`` shows so a merge doesn't lose
        the inbound graph.
        """
        out = set()
        for cid, c in self.concepts.items():
            if cid == concept_id:
                continue
            for ref in c.references:
                if self.resolve(ref) == concept_id:
                    out.add(cid)
        return sorted(out)

    def dependents(self, concept_id: str) -> List[str]:
        """Concepts that would break if ``concept_id`` were hard-removed.

        Anything pointing *at* it: a ``references:`` entry, an inline ``[[id]]``
        in the body, a redirect target, or a ``superseded_by``. This mirrors
        exactly what the dangling-reference gate checks, so a remove that
        passes this gate can never leave the canon failing validation.
        """
        out = set()
        for cid, c in self.concepts.items():
            if cid == concept_id:
                continue
            if (
                concept_id in c.references
                or c.redirect == concept_id
                or c.superseded_by == concept_id
                or concept_id in c.inline_refs()
            ):
                out.add(cid)
        return sorted(out)

    def backlink_index(self) -> Dict[str, List[str]]:
        """``{id: [ids that reference it]}`` for every concept in the graph."""
        index: Dict[str, List[str]] = {cid: [] for cid in self.concepts}
        for cid, concept in self.concepts.items():
            for ref in concept.references:
                if ref in index:
                    index[ref].append(cid)
        return {k: sorted(v) for k, v in index.items()}

    def neighbors(self, concept_id: str) -> Dict[str, List[str]]:
        concept = self.concepts[concept_id]
        return {
            "references": list(concept.references),
            "referenced_by": self.backlinks(concept_id),
        }

    # --- gates --------------------------------------------------------------

    def validate(
        self, *, domains=DEFAULT_DOMAINS, id_pattern: str = DEFAULT_ID_PATTERN
    ) -> List[Issue]:
        """Run every gate and return all issues (empty == the canon is sound).

        Covers: per-concept schema validation, duplicate ids, and the
        dangling-reference gate over both ``references:`` and inline ``[[id]]``.
        """
        issues: List[Issue] = []

        for concept in self.concepts.values():
            issues.extend(
                validate_concept(concept, domains=domains, id_pattern=id_pattern)
            )

        for dup in self.duplicates:
            where = str(dup.path) if dup.path else "<unknown>"
            issues.append(Issue(dup.id or where, "id", f"duplicate id (also at {where})"))

        issues.extend(self.dangling_references())
        return issues

    def dangling_references(self) -> List[Issue]:
        """Every reference, inline ``[[id]]``, and lifecycle pointer must resolve.

        A reference to a redirect tombstone is fine (the id exists and forwards),
        so merges are non-breaking. Broken or cyclic redirects, and dangling
        ``superseded_by`` pointers, are reported.
        """
        issues: List[Issue] = []
        known = set(self.concepts)
        for cid, concept in self.concepts.items():
            for ref in concept.references:
                if ref not in known:
                    issues.append(
                        Issue(cid, "references", f"dangling reference -> '{ref}'")
                    )
            for ref in concept.inline_refs():
                if ref not in known:
                    issues.append(
                        Issue(cid, "body", f"dangling inline [[{ref}]]")
                    )
            # A concept must not reference itself.
            if cid in concept.references:
                issues.append(Issue(cid, "references", "references itself"))

            if concept.redirect:
                if concept.redirect not in known:
                    issues.append(
                        Issue(cid, "redirect", f"redirect target does not exist -> '{concept.redirect}'")
                    )
                elif self.resolve(cid) is None:
                    issues.append(Issue(cid, "redirect", "redirect chain is cyclic"))
            if concept.superseded_by and concept.superseded_by not in known:
                issues.append(
                    Issue(cid, "superseded_by", f"target does not exist -> '{concept.superseded_by}'")
                )
        return issues


def validate_root(
    concepts_root: Path, *, domains=DEFAULT_DOMAINS, id_pattern: str = DEFAULT_ID_PATTERN
) -> List[Issue]:
    """Convenience: load a concepts root and run all gates."""
    return Graph.load(concepts_root).validate(domains=domains, id_pattern=id_pattern)
