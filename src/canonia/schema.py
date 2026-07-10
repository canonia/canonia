# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""The concept model + validation — Canonia's contract.

One concept per file: frontmatter (``id, title, domain, status, summary,
source[], references[]``) plus a markdown body. ``id`` is a globally-unique,
kebab-case reference key; ``references[]`` is the authoritative graph. This
module owns parsing a file into a :class:`Concept`, serializing one back out,
and structurally validating the frontmatter against the contract.

The *dangling-reference gate* (does every referenced id resolve?) is a
whole-graph property and lives in :mod:`canonia.graph`.
"""

from __future__ import annotations

import itertools
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from canonia import markdown

# Kebab-case: dash-separated alphanumeric runs — rejects 'foo-' and 'foo--bar'.
# Validated with fullmatch, so a user-supplied pattern need not be anchored and
# '$' cannot admit a trailing newline.
DEFAULT_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
# '.' and ':' are reserved for future id namespacing (e.g. `infra:deploy-key`).
# Flat ids are baked into filenames, site URLs, sqlite primary keys, and the
# wikilink syntax, so an id minted with either character today would turn
# namespacing into a breaking migration. Rejected in every id position
# regardless of `schema.id_pattern` — a loosened pattern cannot re-admit them.
RESERVED_ID_CHARS = ".:"
DEFAULT_DOMAINS = ("process", "product", "infra", "ops")
# Lifecycle states. active/draft/deprecated are live (resolve to themselves);
# merged is a redirect tombstone (forwards via `redirect`); archived is dropped
# from the active/searchable set but still on disk and still resolvable.
VALID_STATUS = ("active", "draft", "deprecated", "merged", "archived")
LIVE_STATUS = ("active", "draft", "deprecated")

# Frontmatter keys the schema knows about; anything else is preserved but flagged.
KNOWN_KEYS = {
    "id", "title", "domain", "status", "summary",
    "source", "references", "tags", "created", "updated",
    "redirect", "superseded_by",
}
# The order concept frontmatter is written in.
_EMIT_ORDER = [
    "id", "title", "domain", "status", "summary", "created", "updated",
    "superseded_by", "redirect", "tags", "references", "source",
]


class ValidationError(Exception):
    """Raised when one or more concepts violate the schema (message lists all)."""


# Per-call-unique temp names: two threads saving the same path (e.g. two
# racing `create`s in one server) must never share a temp file, or the loser's
# bytes could land under the winner's name. next() on a count is atomic.
_SAVE_SEQ = itertools.count()


@dataclass
class Issue:
    """A single validation problem, tied to a concept id (or file) and field."""

    concept: str
    field: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"[{self.concept}] {self.field}: {self.message}"


@dataclass
class Concept:
    """One canon concept: validated frontmatter + markdown body."""

    id: str
    title: str
    domain: str
    summary: str
    references: List[str] = field(default_factory=list)
    source: List[Dict[str, str]] = field(default_factory=list)
    status: str = "active"
    # Lifecycle pointers. `redirect` (status=merged) forwards this id to a
    # canonical concept so inbound references still resolve. `superseded_by`
    # (status=deprecated) names a replacement while this concept lives on.
    redirect: Optional[str] = None
    superseded_by: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    # Provenance timestamps, carried as the raw YAML value (str or date) so a
    # load→save round-trip re-emits exactly what the author wrote.
    created: Optional[object] = None
    updated: Optional[object] = None
    body: str = ""
    extra: Dict[str, object] = field(default_factory=dict)
    path: Optional[Path] = None

    @property
    def is_redirect(self) -> bool:
        return bool(self.redirect)

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_STATUS

    # --- construction -------------------------------------------------------

    @classmethod
    def from_markdown(cls, text: str, path: Optional[Path] = None) -> Concept:
        meta, body = markdown.split_frontmatter(text)
        return cls.from_frontmatter(meta, body, path=path)

    @classmethod
    def from_frontmatter(
        cls, meta: dict, body: str = "", path: Optional[Path] = None
    ) -> Concept:
        meta = dict(meta or {})
        extra = {k: v for k, v in meta.items() if k not in KNOWN_KEYS}
        return cls(
            id=str(meta.get("id", "")).strip(),
            title=str(meta.get("title", "")).strip() if meta.get("title") is not None else "",
            domain=str(meta.get("domain", "")).strip(),
            summary=str(meta.get("summary", "")).strip() if meta.get("summary") is not None else "",
            references=list(meta.get("references") or []),
            source=_normalize_source(meta.get("source")),
            status=str(meta.get("status") or "active").strip(),
            redirect=(str(meta["redirect"]).strip() if meta.get("redirect") else None),
            superseded_by=(str(meta["superseded_by"]).strip() if meta.get("superseded_by") else None),
            tags=list(meta.get("tags") or []),
            created=meta.get("created") or None,
            updated=meta.get("updated") or None,
            body=body,
            extra=extra,
            path=path,
        )

    @classmethod
    def load(cls, path: Path) -> Concept:
        return cls.from_markdown(Path(path).read_text(encoding="utf-8"), path=Path(path))

    # --- serialization ------------------------------------------------------

    def frontmatter(self) -> dict:
        meta: dict = {
            "id": self.id,
            "title": self.title,
            "domain": self.domain,
            "status": self.status,
            "summary": self.summary,
        }
        if self.created:
            meta["created"] = self.created
        if self.updated:
            meta["updated"] = self.updated
        if self.superseded_by:
            meta["superseded_by"] = self.superseded_by
        if self.redirect:
            meta["redirect"] = self.redirect
        if self.tags:
            meta["tags"] = list(self.tags)
        meta["references"] = list(self.references)
        meta["source"] = [dict(s) for s in self.source]
        # Preserve any unknown keys (forward-compat) at the end.
        for k, v in self.extra.items():
            meta[k] = v
        # Stable ordering for known keys, extras trailing.
        ordered = {k: meta[k] for k in _EMIT_ORDER if k in meta}
        for k, v in meta.items():
            if k not in ordered:
                ordered[k] = v
        return ordered

    def to_markdown(self) -> str:
        body = self.body.rstrip("\n")
        return markdown.dump_frontmatter(self.frontmatter()) + "\n" + body + "\n"

    def save(self, path: Path, *, exclusive: bool = False) -> None:
        """Atomically write this concept to ``path`` (temp file + rename).

        A crash mid-write can never leave a truncated concept file, and the
        temp name starts with a dot so a concurrent :meth:`Graph.load` (which
        skips dotfiles) never picks up a half-written concept.

        With ``exclusive=True`` the write lands only if ``path`` does not
        already exist — atomically, via hard link, so there is no window
        between check and write — and raises :class:`FileExistsError`
        otherwise. This is the guard that keeps two racing creates of the same
        id from silently clobbering each other. On filesystems without hard
        links it degrades to the plain replace, leaving the caller's
        pre-flight exists() check as the only (racy) guard.
        """
        path = Path(path)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{next(_SAVE_SEQ)}.tmp")
        try:
            tmp.write_text(self.to_markdown(), encoding="utf-8")
            if not exclusive:
                os.replace(tmp, path)
            else:
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    raise
                except OSError:
                    os.replace(tmp, path)
        finally:
            # replace() consumed the temp (unlink is a no-op); link() did not.
            try:
                tmp.unlink()
            except OSError:
                pass

    # --- graph helpers ------------------------------------------------------

    def inline_refs(self) -> List[str]:
        return markdown.extract_inline_refs(self.body)


def _normalize_source(source) -> List[Dict[str, str]]:
    """Coerce a frontmatter ``source`` value into ``[{repo, path}, ...]``.

    Accepts the ``[{repo, path}]`` form and the bare-string form (repo implied
    by the mapping batch's default). Bare strings are left as ``{path: ...}``
    and the caller supplies the repo.
    """
    if not source:
        return []
    if isinstance(source, (str, dict)):
        source = [source]
    out: List[Dict[str, str]] = []
    for entry in source:
        if isinstance(entry, str):
            out.append({"path": entry})
        elif isinstance(entry, dict):
            out.append({str(k): str(v) for k, v in entry.items()})
    return out


# --- validation -------------------------------------------------------------

def id_problem(value: str, id_re: re.Pattern) -> Optional[str]:
    """Why ``value`` cannot be a concept id, or ``None`` if it can.

    Checks the (configurable) pattern, then the non-configurable
    reserved-separator rule.
    """
    if not id_re.fullmatch(value):
        return f"does not match {id_re.pattern}"
    hit = sorted(set(value) & set(RESERVED_ID_CHARS))
    if hit:
        chars = ", ".join(repr(c) for c in hit)
        return f"contains {chars}, reserved for future namespacing (rejected regardless of id_pattern)"
    return None


def validate_concept(
    concept: Concept,
    *,
    domains=DEFAULT_DOMAINS,
    id_pattern: str = DEFAULT_ID_PATTERN,
) -> List[Issue]:
    """Structural validation of one concept's frontmatter (no cross-references).

    Returns a list of :class:`Issue`; empty means valid.
    """
    issues: List[Issue] = []
    who = concept.id or (str(concept.path) if concept.path else "<unknown>")
    id_re = re.compile(id_pattern)

    if not concept.id:
        issues.append(Issue(who, "id", "missing"))
    else:
        problem = id_problem(concept.id, id_re)
        if problem:
            issues.append(Issue(who, "id", f"'{concept.id}' {problem}"))

    if not concept.title:
        issues.append(Issue(who, "title", "missing"))

    if not concept.domain:
        issues.append(Issue(who, "domain", "missing"))
    elif concept.domain not in domains:
        issues.append(
            Issue(who, "domain", f"'{concept.domain}' not in {list(domains)}")
        )

    if not concept.summary:
        issues.append(Issue(who, "summary", "missing"))
    elif "\n" in concept.summary.strip():
        issues.append(Issue(who, "summary", "must be a single line"))

    if not concept.source:
        issues.append(Issue(who, "source", "at least one source entry required"))
    else:
        for i, s in enumerate(concept.source):
            if "path" not in s or not s["path"]:
                issues.append(Issue(who, f"source[{i}]", "missing 'path'"))
            if "repo" not in s or not s["repo"]:
                issues.append(Issue(who, f"source[{i}]", "missing 'repo'"))

    if concept.status not in VALID_STATUS:
        issues.append(
            Issue(who, "status", f"'{concept.status}' not in {list(VALID_STATUS)}")
        )

    for ref in concept.references:
        problem = id_problem(ref, id_re) if isinstance(ref, str) else "not a string"
        if problem:
            issues.append(Issue(who, "references", f"invalid id '{ref}': {problem}"))

    # Lifecycle pointers: shape only; resolution is a graph-level gate.
    if concept.redirect is not None:
        problem = id_problem(concept.redirect, id_re)
        if problem:
            issues.append(Issue(who, "redirect", f"invalid id '{concept.redirect}': {problem}"))
        elif concept.redirect == concept.id:
            issues.append(Issue(who, "redirect", "concept redirects to itself"))
        if concept.status != "merged":
            issues.append(Issue(who, "redirect", "set but status is not 'merged'"))
    if concept.status == "merged" and not concept.redirect:
        issues.append(Issue(who, "status", "'merged' requires a 'redirect' target"))

    if concept.superseded_by is not None:
        problem = id_problem(concept.superseded_by, id_re)
        if problem:
            issues.append(Issue(who, "superseded_by", f"invalid id '{concept.superseded_by}': {problem}"))
        elif concept.superseded_by == concept.id:
            issues.append(Issue(who, "superseded_by", "concept supersedes itself"))

    if concept.extra:
        issues.append(
            Issue(who, "frontmatter", f"unknown keys: {sorted(concept.extra)}")
        )

    # Domain should agree with the folder the file lives in, when known.
    if concept.path is not None and concept.domain:
        folder = concept.path.parent.name
        if folder in domains and folder != concept.domain:
            issues.append(
                Issue(who, "domain", f"'{concept.domain}' but file is under '{folder}/'")
            )

    return issues
