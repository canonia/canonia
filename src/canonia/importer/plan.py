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
        """Write every emitted concept under ``out_dir/<domain>/<id>.md``."""
        out_dir = Path(out_dir)
        written: List[Path] = []
        for item in self.emitted:
            target = out_dir / item.rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.concept.to_markdown(), encoding="utf-8")
            written.append(target)
        return written

    # --- reporting ----------------------------------------------------------

    def render_report(self, *, committed: bool = False) -> str:
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

        for w in self.warnings:
            lines.append(f"  ! {w}")

        return "\n".join(lines)
