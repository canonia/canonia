# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""``canonia import`` — seed a canon from source repos.

Two modes, both **dry-run by default** and **review-then-commit** — the importer
never silently mangles a user's docs:

* **curated** (:mod:`canonia.importer.curated`) — consume a reviewed
  ``mapping.yml`` manifest (split / merge / dedup decisions) and emit one
  concept file per entry, extracting bodies from the source repos.
* **zero-config** (:mod:`canonia.importer.zeroconfig`) — a folder of markdown ->
  one concept per file, ``id`` from the slug, ``references`` auto-extracted from
  existing links.

The importer is a pure function of ``(sources + manifest)`` so it can be re-run
for free as the schema evolves.
"""

from canonia.importer.curated import import_curated
from canonia.importer.mapping import ConceptSpec, SourceRef, load_mapping
from canonia.importer.plan import EmittedConcept, ImportPlan
from canonia.importer.zeroconfig import import_zeroconfig

__all__ = [
    "import_curated",
    "import_zeroconfig",
    "load_mapping",
    "ConceptSpec",
    "SourceRef",
    "ImportPlan",
    "EmittedConcept",
]
