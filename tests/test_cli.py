# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Tests for the CLI import gate: write-blocking + post-commit-state validation."""

from pathlib import Path

from canonia.cli import main
from canonia.schema import Concept


def _write_source(src: Path, name: str, body: str) -> None:
    src.mkdir(parents=True, exist_ok=True)
    title = name[:-3].title() if name.endswith(".md") else name.title()
    (src / name).write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


def test_import_commit_blocked_when_gates_fail(tmp_path: Path, capsys):
    src = tmp_path / "docs"
    _write_source(src, "note.md", "See [[nonexistent-xyz]].")
    out = tmp_path / "concepts"

    rc = main(["import", "--zero-config", str(src), "--domain", "process",
               "--out", str(out), "--commit"])

    assert rc == 1
    assert not out.exists() or not list(out.rglob("*.md"))
    assert "NOTHING was written" in capsys.readouterr().out


def test_import_commit_force_overrides_gate(tmp_path: Path):
    src = tmp_path / "docs"
    _write_source(src, "note.md", "See [[nonexistent-xyz]].")
    out = tmp_path / "concepts"

    rc = main(["import", "--zero-config", str(src), "--domain", "process",
               "--out", str(out), "--commit", "--force"])

    assert rc == 1  # the gate still fails loudly
    assert (out / "process" / "note.md").exists()  # ...but the operator chose to write


def test_import_gate_accepts_references_to_existing_canon(tmp_path: Path):
    # A body ref to a concept already on disk is NOT dangling: the gate must
    # validate the predicted post-commit canon (emitted ∪ existing), not the
    # emitted set in isolation.
    out = tmp_path / "concepts"
    (out / "process").mkdir(parents=True)
    existing = Concept(id="existing-concept", title="Existing", domain="process",
                       summary="s", source=[{"repo": "r", "path": "e.md"}])
    (out / "process" / "existing-concept.md").write_text(existing.to_markdown(), encoding="utf-8")

    src = tmp_path / "docs"
    _write_source(src, "note.md", "See [[existing-concept]].")

    rc = main(["import", "--zero-config", str(src), "--domain", "process",
               "--out", str(out), "--commit"])

    assert rc == 0
    assert (out / "process" / "note.md").exists()
    assert (out / "process" / "existing-concept.md").exists()  # untouched


def test_import_dry_run_still_reports_gate_failures(tmp_path: Path, capsys):
    src = tmp_path / "docs"
    _write_source(src, "note.md", "See [[nonexistent-xyz]].")
    out = tmp_path / "concepts"

    rc = main(["import", "--zero-config", str(src), "--domain", "process",
               "--out", str(out)])

    assert rc == 1
    assert not out.exists() or not list(out.rglob("*.md"))
    assert "dry-run" in capsys.readouterr().out


def test_init_domains_strips_whitespace(tmp_path: Path):
    from canonia.cli import main as cli_main
    root = tmp_path / "canon"
    rc = cli_main(["init", str(root), "--domains", "process, lore"])
    assert rc == 0
    assert (root / "concepts" / "lore").is_dir()
    assert not (root / "concepts" / " lore").exists()
    assert "[process, lore]" in (root / "canonia.yml").read_text(encoding="utf-8")


def test_parse_sources_handles_windows_drive_colons():
    from canonia.cli import _parse_sources
    repos = _parse_sources(["win=C:\\repo", "unix=../docs:canon", "both=C:\\repo:sub"])
    assert str(repos["win"].path).endswith("C:\\repo") and repos["win"].prefix == ""
    assert repos["unix"].prefix == "canon"
    assert str(repos["both"].path).endswith("C:\\repo") and repos["both"].prefix == "sub"
