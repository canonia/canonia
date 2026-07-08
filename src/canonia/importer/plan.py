# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""The import plan — what the importer *would* write, for review before commit.

An :class:`ImportPlan` is the dry-run surface: every concept it would emit, how
its body was sourced (whole file / extracted section / stub), and any warnings.
Nothing is written until the caller explicitly commits.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from canonia.schema import Concept

# How a concept's body was produced.
WHOLE_FILE = "whole-file"   # source is a dedicated file, used verbatim
SECTION = "section"         # a heading section was extracted by anchor
STUB = "stub"               # body could not be extracted -> summary + review note

# Why an existing concept file is a prune candidate under --prune.
ORPHAN = "orphan"           # the sources no longer emit this id at all
MOVED = "moved"             # the id is re-emitted, but to a different file (stale here)


@dataclass
class EmittedConcept:
    """One concept the importer produced, with provenance about its body."""

    concept: Concept
    body_strategy: str
    body_source: str = ""          # human-readable description of where body came from
    warnings: List[str] = field(default_factory=list)

    @property
    def rel_path(self) -> str:
        return f"{self.concept.domain}/{self.concept.id}.md"


@dataclass
class Pruned:
    """An existing concept file this import would remove under --prune."""

    id: str
    path: Path
    reason: str                    # ORPHAN | MOVED


@dataclass
class ImportPlan:
    """The full set of emitted concepts plus roll-up stats for review."""

    emitted: List[EmittedConcept] = field(default_factory=list)
    out_dir: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)

    def add(self, item: EmittedConcept) -> None:
        self.emitted.append(item)

    # --- roll-ups -----------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.emitted)

    def by_strategy(self) -> Counter:
        return Counter(e.body_strategy for e in self.emitted)

    def by_domain(self) -> Counter:
        return Counter(e.concept.domain for e in self.emitted)

    def stubs(self) -> List[EmittedConcept]:
        return [e for e in self.emitted if e.body_strategy == STUB]

    def with_warnings(self) -> List[EmittedConcept]:
        return [e for e in self.emitted if e.warnings]

    # --- writing ------------------------------------------------------------

    def write(self, out_dir: Path) -> List[Path]:
        """Write every emitted concept under ``out_dir/<domain>/<id>.md``.

        Writes are atomic (temp file + rename) and refused if a concept's
        domain/id would place the file outside ``out_dir`` — containment must
        not depend on the id pattern alone.
        """
        out_dir = Path(out_dir)
        out_root = out_dir.resolve()
        written: List[Path] = []
        for item in self.emitted:
            target = out_dir / item.rel_path
            if not target.resolve().is_relative_to(out_root):
                raise ValueError(f"refusing to write outside the canon: {item.rel_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            item.concept.save(target)
            written.append(target)
        return written

    # --- reconciliation (--prune) ------------------------------------------

    def reconcile(self, out_dir: Path) -> List[Pruned]:
        """Concept files under ``out_dir`` this import would NOT (re)produce.

        Reconcile by id: an existing concept whose id is absent from the emitted
        set is an :data:`ORPHAN`; one whose id is re-emitted but to a different
        file (e.g. its domain changed) is :data:`MOVED` — stale at its old path.
        Ids the import re-emits at the same path are left for :meth:`write` to
        overwrite. With every non-emitted file pruned, the committed canon equals
        the emitted set exactly, so the in-memory gate check predicts the result.
        """
        from canonia.graph import Graph  # local: avoid import cycle at module load

        out_dir = Path(out_dir)
        if not out_dir.exists():
            return []
        targets = {e.concept.id: (out_dir / e.rel_path).resolve() for e in self.emitted}
        pruned: List[Pruned] = []
        for cid, concept in Graph.load(out_dir).concepts.items():
            if concept.path is None:
                continue
            path = Path(concept.path).resolve()
            if cid not in targets:
                pruned.append(Pruned(cid, path, ORPHAN))
            elif targets[cid] != path:
                pruned.append(Pruned(cid, path, MOVED))
        return sorted(pruned, key=lambda p: str(p.path))

    def apply_prune(self, pruned: List[Pruned]) -> List[Path]:
        """Delete the reconciled files. Caller gates this behind --commit."""
        removed: List[Path] = []
        for item in pruned:
            if item.path.exists():
                item.path.unlink()
                removed.append(item.path)
        return removed

    # --- reporting ----------------------------------------------------------

    def render_report(
        self, *, committed: bool = False, pruned: Optional[List[Pruned]] = None
    ) -> str:
        lines: List[str] = []
        verb = "Imported" if committed else "Would import"
        lines.append(f"{verb} {self.total} concepts")

        dom = self.by_domain()
        lines.append("  by domain:   " + " · ".join(
            f"{d} {dom[d]}" for d in sorted(dom)
        ))
        strat = self.by_strategy()
        lines.append("  body source: " + " · ".join(
            f"{s} {strat[s]}" for s in (WHOLE_FILE, SECTION, STUB) if strat.get(s)
        ))

        stubs = self.stubs()
        if stubs:
            lines.append("")
            lines.append(f"  {len(stubs)} concept(s) need a body (stubbed, flagged for review):")
            for e in stubs:
                lines.append(f"    - {e.rel_path}: {e.body_source}")

        warned = self.with_warnings()
        if warned:
            lines.append("")
            lines.append(f"  {len(warned)} concept(s) with warnings:")
            for e in warned:
                for w in e.warnings:
                    lines.append(f"    - {e.rel_path}: {w}")

        if pruned:
            pverb = "Pruned" if committed else "Would prune"
            lines.append("")
            lines.append(f"  {pverb} {len(pruned)} concept(s) the sources no longer produce:")
            for p in pruned:
                lines.append(f"    - {p.reason}: {self._display_path(p.path)}")

        for w in self.warnings:
            lines.append(f"  ! {w}")

        return "\n".join(lines)

    def _display_path(self, path: Path) -> str:
        """Path relative to the output dir when possible, else absolute.

        Rendered with forward slashes on every OS so the report reads the same
        way concept ``rel_path``s are written (``domain/id.md``).
        """
        if self.out_dir is not None:
            try:
                return Path(path).resolve().relative_to(Path(self.out_dir).resolve()).as_posix()
            except ValueError:
                pass
        return Path(path).as_posix()
