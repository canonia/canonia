# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Embedding index (sqlite-vec) for semantic search + dedup.

NOT YET IMPLEMENTED. The first importer run does dedup by exact/fuzzy title+slug
match (the index does not exist until this module is built); see the migration
plan's honest limitation note.
"""

from __future__ import annotations


def build_index(*args, **kwargs):  # pragma: no cover - placeholder
    raise NotImplementedError("canonia index: embedding index not implemented yet.")
