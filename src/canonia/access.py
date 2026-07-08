# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Access control — SEAM ONLY. Governance (RBAC) is a future module; v1 ships open.

This module exists now so the server and site call an access filter from day one
and the governance module can later fill it in without touching call sites. The
filter is a deliberate no-op: it returns every concept, for every identity, in
every domain. Do not add policy here yet — leave the seam.

The future module will scope by ``domain`` (present on every concept) and by
identity, and — per the project's stance — will scope **LLM identities too**, not
just humans. ``Identity.kind`` carries that distinction from the start.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import List, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Identity:
    """Who is asking. ``kind`` distinguishes human vs. LLM/agent callers."""

    name: str = "anonymous"
    kind: str = "human"  # "human" | "llm"
    roles: tuple = ()


#: The identity used when the caller is unauthenticated (v1: full open access).
ANONYMOUS = Identity()


def filter_concepts(
    concepts: Iterable[T], identity: Optional[Identity] = None
) -> List[T]:
    """Return the concepts ``identity`` may see. NO-OP in v1: returns all of them."""
    return list(concepts)


def can_access(concept, identity: Optional[Identity] = None) -> bool:
    """Whether ``identity`` may access ``concept``. NO-OP in v1: always True."""
    return True


def can_write(concept, identity: Optional[Identity] = None) -> bool:
    """Whether ``identity`` may write/delete ``concept``. NO-OP in v1: always True.

    The write-side twin of :func:`can_access`, called on every server write and
    remove so the governance module can scope write access (per-domain,
    per-identity — humans AND LLMs) without touching call sites.
    """
    return True
