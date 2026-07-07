# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Curated import — build concept files from a reviewed ``mapping.yml``.

The manifest supplies every concept's frontmatter (id, title, domain, summary,
references, source). This module produces the *body*: it reads each concept's
primary source and either uses the whole dedicated file, extracts the section an
anchor points at, or — when neither is safe — emits an honest stub that carries
the summary and a review marker rather than fabricating or duplicating prose.
Markdown links inside extracted bodies are rewritten ``[text](path)`` -> ``[[id]]``
when they point at another concept.
"""

from __future__ import annotations

from typing import Dict, List

from canonia import markdown
from canonia.config import SourceRepo
from canonia.importer.mapping import ConceptSpec, load_mapping
from canonia.importer.plan import (
    SECTION,
    STUB,
    WHOLE_FILE,
    EmittedConcept,
    ImportPlan,
)
from canonia.importer.sources import SourceResolver
from canonia.schema import Concept

STUB_MARKER = "<!-- canonia:body-pending -->"


def import_curated(
    mapping_path,
    repos: Dict[str, SourceRepo],
) -> ImportPlan:
    """Produce an :class:`ImportPlan` from a manifest + its source repos."""
    specs = load_mapping(mapping_path)
    resolver = SourceResolver(repos, specs)
    plan = ImportPlan()

    if not repos:
        plan.warnings.append("no source repos configured; every body will be a stub")

    for spec in specs:
        plan.add(_emit(spec, resolver))
    return plan


def _emit(spec: ConceptSpec, resolver: SourceResolver) -> EmittedConcept:
    warnings: List[str] = []
    body, strategy, source_desc = _build_body(spec, resolver, warnings)

    concept = Concept(
        id=spec.id,
        title=spec.title,
        domain=spec.domain,
        summary=spec.summary,
        references=list(spec.references),
        source=[{"repo": s.repo, "path": s.raw_path} for s in spec.sources],
        status="active",
        body=body,
    )
    return EmittedConcept(
        concept=concept,
        body_strategy=strategy,
        body_source=source_desc,
        warnings=warnings,
    )


def _build_body(spec: ConceptSpec, resolver: SourceResolver, warnings: List[str]):
    primary = spec.primary
    if primary is None:
        warnings.append("no source entries in mapping")
        return _stub(spec, "no source in mapping"), STUB, "no source"

    text = resolver.read(primary)
    if text is None:
        warnings.append(f"source not found on disk: {primary.repo}:{primary.raw_path}")
        return (
            _stub(spec, f"source file not found: {primary.repo}:{primary.raw_path}"),
            STUB,
            f"missing source {primary.repo}:{primary.raw_path}",
        )

    # A source claimed as the primary of several concepts can't become a body
    # without cloning the same prose into each — stub and flag instead.
    co_owners = resolver.primary_co_owners(spec)
    if co_owners:
        shared = f"{primary.repo}:{primary.raw_path} shared as primary with {co_owners}"
        warnings.append(f"primary source {shared}; needs a distinguishing anchor")
        return _stub(spec, shared), STUB, f"{primary.repo}:{primary.raw_path} (shared)"

    _meta, file_body = markdown.split_frontmatter(text)

    if primary.anchor:
        section = markdown.extract_section(file_body, primary.anchor)
        if section is None:
            warnings.append(
                f"anchor #{primary.anchor} did not resolve in {primary.repo}:{primary.path}"
            )
            return (
                _stub(spec, f"anchor #{primary.anchor} did not resolve in {primary.repo}:{primary.path}"),
                STUB,
                f"unresolved anchor #{primary.anchor} in {primary.repo}:{primary.path}",
            )
        body = _rewrite(section, spec, resolver)
        return body, SECTION, f"{primary.repo}:{primary.raw_path}"

    body = _rewrite(_strip_leading_h1(file_body), spec, resolver)
    return body, WHOLE_FILE, f"{primary.repo}:{primary.path}"


def _rewrite(body: str, spec: ConceptSpec, resolver: SourceResolver) -> str:
    primary = spec.primary
    if primary is None:                       # no source to resolve links against
        return body.strip("\n")
    rewritten = markdown.rewrite_links(
        body, lambda target: resolver.resolve_link(primary, target)
    )
    # A concept should never inline-reference itself after rewriting.
    return rewritten.strip("\n")


def _strip_leading_h1(body: str) -> str:
    """Drop a leading level-1 heading (it duplicates the concept ``title``)."""
    lines = body.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("# "):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
    return "\n".join(lines[i:]).strip("\n")


def _stub(spec: ConceptSpec, reason: str) -> str:
    provenance = ", ".join(f"{s.repo}:{s.raw_path}" for s in spec.sources) or "none"
    return (
        f"{spec.summary}\n\n"
        f"{STUB_MARKER}\n"
        f"> **Body pending import.** {reason}. "
        f"Provenance: {provenance}. Resolve by adding a section anchor to this "
        f"concept's source in the mapping, or by authoring the body here directly."
    )
