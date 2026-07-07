# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Canonia — a git-backed, MCP-served knowledge graph for AI coding agents.

One canonical store of single-topic markdown *concepts* that many repos
*reference* (by ``id``) instead of copying. Concepts link to concepts (a graph);
a stateless MCP server is the agent interface; a static site gives humans a
browsable graph; git provides versioning + authorship.

Status: pre-alpha (v0.1 feature-complete). Ships the schema, the graph gates, the
importer (``canonia import``), the MCP server (``canonia serve``), the static site
(``canonia build``), and an optional local semantic index (``canonia index``, the
``canonia[semantic]`` extra). Governance/access control is a future module.
"""

__version__ = "0.1.0"

from canonia.schema import Concept, ValidationError

__all__ = ["Concept", "ValidationError", "__version__"]
