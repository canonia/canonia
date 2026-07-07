# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Tests for the curated and zero-config importers against synthetic fixtures."""

from pathlib import Path

import yaml

from canonia.config import SourceRepo
from canonia.graph import Graph
from canonia.importer import import_curated, import_zeroconfig
from canonia.importer.plan import SECTION, STUB, WHOLE_FILE


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
    first = {p.name: p.read_text() for p in out.rglob("*.md")}
    import_curated(mapping, repos).write(out)
    second = {p.name: p.read_text() for p in out.rglob("*.md")}
    assert first == second


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
