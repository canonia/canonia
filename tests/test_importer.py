# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Tests for the curated and zero-config importers against synthetic fixtures."""

from pathlib import Path

import yaml

from canonia.config import SourceRepo
from canonia.graph import Graph
from canonia.importer import import_curated, import_zeroconfig
from canonia.importer.plan import (
    MOVED,
    ORPHAN,
    SECTION,
    STUB,
    WHOLE_FILE,
    EmittedConcept,
    ImportPlan,
)
from canonia.schema import Concept


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_sources(tmp_path: Path) -> dict:
    repo = tmp_path / "src"
    _write(repo / "whole.md", "# Whole\n\nBody of whole, links [Other](other.md).\n")
    _write(repo / "other.md", "# Other\n\nOther body.\n")
    _write(
        repo / "multi.md",
        "# Multi\n\n## First thing\nfirst section body\n\n## Second thing\nsecond section body\n",
    )
    _write(repo / "shared.md", "# Shared\n\nShared prose used by two concepts.\n")
    return {"r": SourceRepo(path=repo)}


def _mapping(tmp_path: Path) -> Path:
    mapping = {
        "concepts": [
            # whole dedicated file -> WHOLE_FILE, link to `other` rewritten
            {"id": "whole", "title": "Whole", "domain": "process",
             "summary": "whole", "source": [{"repo": "r", "path": "whole.md"}],
             "references": ["other"]},
            {"id": "other", "title": "Other", "domain": "process",
             "summary": "other", "source": [{"repo": "r", "path": "other.md"}]},
            # anchored section -> SECTION
            {"id": "first", "title": "First", "domain": "process",
             "summary": "first", "source": [{"repo": "r", "path": "multi.md#first-thing"}]},
            # two concepts share one whole file with no anchor -> both STUB
            {"id": "shared-a", "title": "Shared A", "domain": "process",
             "summary": "sa", "source": [{"repo": "r", "path": "shared.md"}]},
            {"id": "shared-b", "title": "Shared B", "domain": "process",
             "summary": "sb", "source": [{"repo": "r", "path": "shared.md"}]},
            # source file missing on disk -> STUB with warning
            {"id": "ghost", "title": "Ghost", "domain": "process",
             "summary": "ghost", "source": [{"repo": "r", "path": "nope.md"}]},
        ]
    }
    path = tmp_path / "mapping.yml"
    path.write_text(yaml.safe_dump(mapping), encoding="utf-8")
    return path


def test_curated_body_strategies_and_link_rewrite(tmp_path: Path):
    repos = _build_sources(tmp_path)
    plan = import_curated(_mapping(tmp_path), repos)
    by_id = {e.concept.id: e for e in plan.emitted}

    assert by_id["whole"].body_strategy == WHOLE_FILE
    assert "[[other]]" in by_id["whole"].concept.body       # link rewritten
    assert not by_id["whole"].concept.body.startswith("# ") # leading H1 stripped

    assert by_id["first"].body_strategy == SECTION
    assert by_id["first"].concept.body == "first section body"

    # A file shared as primary by two concepts is stubbed, not duplicated.
    assert by_id["shared-a"].body_strategy == STUB
    assert by_id["shared-b"].body_strategy == STUB
    assert "shared-b" in by_id["shared-a"].concept.body or by_id["shared-a"].warnings

    # A missing source file stubs and warns rather than crashing.
    assert by_id["ghost"].body_strategy == STUB
    assert by_id["ghost"].warnings


def test_curated_output_passes_gates(tmp_path: Path):
    repos = _build_sources(tmp_path)
    plan = import_curated(_mapping(tmp_path), repos)
    g = Graph()
    for e in plan.emitted:
        g.add(e.concept)
    assert g.validate(domains=("process",)) == []


def test_curated_is_idempotent(tmp_path: Path):
    repos = _build_sources(tmp_path)
    mapping = _mapping(tmp_path)
    out = tmp_path / "out"
    import_curated(mapping, repos).write(out)
    first = {p.name: p.read_text(encoding="utf-8") for p in out.rglob("*.md")}
    import_curated(mapping, repos).write(out)
    second = {p.name: p.read_text(encoding="utf-8") for p in out.rglob("*.md")}
    assert first == second


def _plan_of(*concepts: Concept) -> ImportPlan:
    plan = ImportPlan()
    for c in concepts:
        plan.add(EmittedConcept(concept=c, body_strategy=WHOLE_FILE))
    return plan


def _seed(out: Path, *concepts: Concept) -> None:
    """Write concepts to disk as an existing canon (domain/id.md)."""
    _plan_of(*concepts).write(out)


def _c(cid: str, domain: str = "process") -> Concept:
    return Concept(id=cid, title=cid.title(), domain=domain, summary=cid)


def test_reconcile_flags_orphans_not_reemitted(tmp_path: Path):
    out = tmp_path / "canon"
    _seed(out, _c("keep"), _c("gone"))                 # existing canon
    plan = _plan_of(_c("keep"), _c("added"))           # sources drop `gone`
    plan.out_dir = out

    pruned = plan.reconcile(out)
    assert [(p.id, p.reason) for p in pruned] == [("gone", ORPHAN)]


def test_reconcile_flags_moved_when_domain_changes(tmp_path: Path):
    out = tmp_path / "canon"
    _seed(out, _c("thing", domain="process"))
    plan = _plan_of(_c("thing", domain="ops"))         # same id, new domain -> new path
    plan.out_dir = out

    pruned = plan.reconcile(out)
    assert [(p.id, p.reason) for p in pruned] == [("thing", MOVED)]
    assert pruned[0].path == (out / "process" / "thing.md").resolve()


def test_reconcile_empty_when_import_is_superset(tmp_path: Path):
    out = tmp_path / "canon"
    _seed(out, _c("a"), _c("b"))
    plan = _plan_of(_c("a"), _c("b"), _c("c"))
    plan.out_dir = out
    assert plan.reconcile(out) == []


def test_reconcile_on_absent_out_dir_is_empty(tmp_path: Path):
    plan = _plan_of(_c("a"))
    assert plan.reconcile(tmp_path / "nope") == []


def test_apply_prune_removes_orphans_and_leaves_reemitted(tmp_path: Path):
    out = tmp_path / "canon"
    _seed(out, _c("keep"), _c("gone"))
    plan = _plan_of(_c("keep"), _c("added"))
    plan.out_dir = out

    pruned = plan.reconcile(out)
    plan.write(out)                                    # re-emit keep + added
    removed = plan.apply_prune(pruned)

    on_disk = {p.stem for p in out.rglob("*.md")}
    assert on_disk == {"keep", "added"}                # gone pruned, keep untouched
    assert removed == [(out / "process" / "gone.md").resolve()]


def test_apply_prune_moved_deletes_stale_path_keeps_new(tmp_path: Path):
    out = tmp_path / "canon"
    _seed(out, _c("thing", domain="process"))
    plan = _plan_of(_c("thing", domain="ops"))
    plan.out_dir = out

    pruned = plan.reconcile(out)                        # captures old path first
    plan.write(out)                                     # writes ops/thing.md
    plan.apply_prune(pruned)                            # removes process/thing.md

    files = {p.relative_to(out).as_posix() for p in out.rglob("*.md")}
    assert files == {"ops/thing.md"}


def test_report_shows_prune_section(tmp_path: Path):
    out = tmp_path / "canon"
    _seed(out, _c("gone"))
    plan = _plan_of(_c("added"))
    plan.out_dir = out
    pruned = plan.reconcile(out)

    dry = plan.render_report(committed=False, pruned=pruned)
    assert "Would prune 1" in dry and "orphan: process/gone.md" in dry
    done = plan.render_report(committed=True, pruned=pruned)
    assert "Pruned 1" in done


def test_zeroconfig_infers_id_title_and_references(tmp_path: Path):
    folder = tmp_path / "docs"
    _write(folder / "alpha_one.md", "# Alpha One\n\nFirst para. See [Beta](beta.md).\n")
    _write(folder / "beta.md", "# Beta\n\nBeta intro paragraph.\n")
    plan = import_zeroconfig(folder, domain="process", repo="docs")
    by_id = {e.concept.id: e.concept for e in plan.emitted}

    assert set(by_id) == {"alpha-one", "beta"}       # underscore -> dash
    assert by_id["alpha-one"].title == "Alpha One"
    assert by_id["alpha-one"].summary.startswith("First para")
    assert by_id["alpha-one"].references == ["beta"]  # link auto-extracted
    assert "[[beta]]" in by_id["alpha-one"].body
