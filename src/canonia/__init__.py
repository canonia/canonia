# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Canonia — a git-backed, MCP-served knowledge graph for AI coding agents.

One canonical store of single-topic markdown *concepts* that many repos
*reference* (by ``id``) instead of copying. Concepts link to concepts (a graph);
a stateless MCP server is the agent interface; a static site gives humans a
browsable graph; git provides versioning + authorship.

Status: pre-alpha. This package currently ships the schema, the graph gates, and
the importer (``canonia import``). The MCP server, embedding index, and static
site are stubs pending implementation — see the module docstrings.
"""

__version__ = "0.1.0.dev0"

from canonia.schema import Concept, ValidationError

__all__ = ["Concept", "ValidationError", "__version__"]
