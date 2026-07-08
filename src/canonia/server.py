# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""MCP server — the stateless agent interface to a canon.

Exposes five tools over the Model Context Protocol: ``search``, ``get``,
``create``, ``update``, and ``list_domains``. Reads and writes go straight to the
git-backed concept files, so every other session and repo inherits changes on the
next read (optimistic concurrency — no locking, per the project's stance).

Transport: the MCP **stdio** transport (newline-delimited JSON-RPC 2.0). This is a
dependency-free implementation of the protocol so the server runs anywhere Python
does, including 3.9. The tool logic (:class:`CanonService`) is transport-agnostic
and can be re-hosted on the official ``mcp`` SDK unchanged when it's available.

Every read path runs results through :func:`canonia.access.filter_concepts`, the
governance seam (a no-op in v1 — the canon ships open).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from canonia import __version__, access
from canonia.config import CanoniaConfig
from canonia.graph import Graph

if TYPE_CHECKING:  # imported lazily at runtime (the semantic extra may be absent)
    from canonia.index import SemanticSearcher
from canonia.schema import Concept, validate_concept

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "canonia"


def resolve_identity(name: Optional[str] = None, kind: Optional[str] = None) -> access.Identity:
    """Build the caller identity from CLI flags, falling back to env vars.

    ``CANONIA_IDENTITY`` / ``CANONIA_IDENTITY_KIND`` fill in whatever the flags
    don't provide. No name at all ⇒ :data:`access.ANONYMOUS` (v0.1-compatible
    open behavior). A *named* identity with no explicit kind defaults to
    ``llm`` — ``canonia serve`` is the agent interface, and mislabeling an
    agent as human would skip the draft-by-default review gate, while the
    reverse is merely a visible, correctable draft.
    """
    name = (name or os.environ.get("CANONIA_IDENTITY") or "").strip()
    kind = (kind or os.environ.get("CANONIA_IDENTITY_KIND") or "").strip().lower()
    if not name:
        return access.ANONYMOUS
    if not kind:
        kind = "llm"
    if kind not in ("human", "llm"):
        raise ValueError(f"identity kind must be 'human' or 'llm', got {kind!r}")
    return access.Identity(name=name, kind=kind)


def _file_version(path) -> Optional[str]:
    """Short content hash of a concept file — the optimistic-concurrency token."""
    if not path:
        return None
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return None


class ToolError(Exception):
    """A tool-level failure (reported as an ``isError`` result, not a crash)."""


# ---------------------------------------------------------------------------
# Service logic (transport-agnostic)
# ---------------------------------------------------------------------------

class CanonService:
    """Search/read/write operations over a canon directory.

    Stateless by design: every call reloads the graph from disk so results
    reflect concurrent commits by other sessions.
    """

    def __init__(
        self,
        canon_dir: Path,
        identity: Optional[access.Identity] = None,
        autocommit: Optional[bool] = None,
    ):
        self.config = CanoniaConfig.load(canon_dir)
        self.identity = identity or access.ANONYMOUS
        # CLI flag (autocommit=True/False) overrides canonia.yml's git.autocommit.
        self.autocommit = self.config.autocommit if autocommit is None else autocommit
        # Semantic searcher is built lazily on the first search that needs it, so
        # the server starts (and stays keyword-only canons stay) dependency-free.
        self._searcher: Optional["SemanticSearcher"] = None
        self._searcher_ready = False

    # --- helpers ------------------------------------------------------------

    def _graph(self) -> Graph:
        return Graph.load(self.config.concepts_dir)

    def _visible(self, concepts) -> List[Concept]:
        return access.filter_concepts(concepts, self.identity)

    def _concept_view(self, concept: Concept, graph: Graph, *, body: bool) -> dict:
        view = {
            "id": concept.id,
            "title": concept.title,
            "domain": concept.domain,
            "status": concept.status,
            "summary": concept.summary,
            "references": list(concept.references),
            # Effective backlinks follow redirects, so a merge keeps the inbound graph.
            "referenced_by": graph.effective_backlinks(concept.id),
            "source": [dict(s) for s in concept.source],
        }
        if concept.superseded_by:
            view["superseded_by"] = concept.superseded_by
        if concept.redirect:
            view["redirect"] = concept.redirect
        if concept.tags:
            view["tags"] = list(concept.tags)
        if concept.created:
            view["created"] = str(concept.created)
        if concept.updated:
            view["updated"] = str(concept.updated)
        # Optimistic-concurrency token: pass it back as update's
        # expected_version to detect a concurrent edit of this concept.
        version = _file_version(concept.path)
        if version:
            view["version"] = version
        if body:
            view["body"] = concept.body.strip("\n")
        return view

    # --- tools (read) -------------------------------------------------------

    def search(
        self,
        query: str,
        domain: Optional[str] = None,
        limit: int = 10,
        include_archived: bool = False,
    ) -> dict:
        graph = self._graph()
        terms = _tokens(query)
        candidates = []
        for concept in self._visible(graph.concepts.values()):
            if concept.status == "merged":
                continue  # redirect tombstones are never search results
            if concept.status == "archived" and not include_archived:
                continue
            if domain and concept.domain != domain:
                continue
            candidates.append(concept)

        # Semantic scores (empty dict ⇒ keyword-only: no index, extra, or terms).
        sem = self._semantic_scores(query, domain) if terms else {}
        weight = self.config.index_hybrid_weight if sem else 0.0
        keyword = {c.id: _score(c, terms) for c in candidates}
        kw_max = max(keyword.values(), default=0) or 1

        scored = []
        unindexed = 0
        for c in candidates:
            kw = keyword[c.id]
            # sim=None ⇒ no vector for this concept (created or changed since
            # the last `canonia index build`) — distinct from a genuine low sim.
            sim = sem.get(c.id) if sem else None
            if terms and kw <= 0 and (sim or 0.0) < _SEM_FLOOR:
                continue  # neither a keyword nor a semantic hit
            if not sem:
                # Keyword-only mode: exact integer scores, unchanged behavior.
                combined = float(kw)
            elif sim is None:
                # Not in the index yet: score on keywords alone. Blending in
                # sim=0 would cap fresh concepts at (1-weight) of the reachable
                # score — systematically down-ranking the newest knowledge
                # until the next index build.
                combined = kw / kw_max
                unindexed += 1
            else:
                combined = (1 - weight) * (kw / kw_max) + weight * max(sim, 0.0)
            scored.append((combined, kw, sim, c))
        scored.sort(key=lambda t: (-t[0], t[3].id))

        results = []
        for combined, kw, sim, c in scored[: max(1, int(limit))]:
            row = {
                "id": c.id,
                "title": c.title,
                "domain": c.domain,
                "status": c.status,
                "summary": c.summary,
                "score": round(combined, 4) if sem else kw,
            }
            if sem and sim is not None:
                row["semantic"] = round(sim, 4)
            results.append(row)
        out = {"query": query, "count": len(results), "results": results}
        if sem:
            out["mode"] = "hybrid"
            if unindexed:
                # Staleness signal: this many matched concepts have no vector
                # yet — the index predates them. Re-run `canonia index build`.
                out["unindexed"] = unindexed
        return out

    def _semantic_scores(self, query: str, domain: Optional[str]) -> Dict[str, float]:
        """Lazy hybrid-search hook: ``{id: cosine}`` or ``{}`` if unavailable.

        Builds the searcher once. Any failure (missing extra, unbuilt index,
        model load error) collapses to ``{}`` so search stays keyword-only.
        """
        if not self.config.index_semantic:
            return {}
        if not self._searcher_ready:
            self._searcher_ready = True
            try:
                from canonia import index

                searcher = index.SemanticSearcher(self.config)
                self._searcher = searcher if searcher.available else None
            except Exception:  # pragma: no cover - defensive
                self._searcher = None
        if self._searcher is None:
            return {}
        return self._searcher.scores(query, domain)

    def get(self, id: str, include_body: bool = True, follow: bool = True) -> dict:
        graph = self._graph()
        concept = graph.concepts.get(id)
        if concept is None or not access.can_access(concept, self.identity):
            raise ToolError(f"no concept with id '{id}'")
        # A redirect transparently forwards to its canonical concept (like a
        # Wikipedia redirect) unless the caller asks for the raw tombstone.
        if follow and concept.redirect:
            target_id = graph.resolve(id)
            if target_id is None:
                raise ToolError(f"'{id}' is a broken or cyclic redirect")
            view = self._concept_view(graph.concepts[target_id], graph, body=include_body)
            view["redirected_from"] = id
            return view
        return self._concept_view(concept, graph, body=include_body)

    def list_domains(self) -> dict:
        graph = self._graph()
        counts: Dict[str, int] = {d: 0 for d in self.config.domains}
        archived = redirects = 0
        for c in self._visible(graph.concepts.values()):
            if c.status == "merged":
                redirects += 1
            elif c.status == "archived":
                archived += 1
            else:
                counts[c.domain] = counts.get(c.domain, 0) + 1
        return {
            "domains": counts,
            "total": sum(counts.values()),
            "archived": archived,
            "redirects": redirects,
        }

    def create(
        self,
        id: str,
        title: str,
        domain: str,
        summary: str,
        references: Optional[List[str]] = None,
        source: Optional[List[dict]] = None,
        body: str = "",
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> dict:
        if status is None:
            # Agent-authored knowledge lands as a draft for human review;
            # humans (and an explicit status) publish straight to active.
            # Drafts are live — searchable and resolvable — just marked.
            status = "draft" if self.identity.kind == "llm" else "active"
        if not source:
            # Authored directly in the canon — provenance points at itself.
            source = [{"repo": self.config.canon_name,
                       "path": f"{self.config.concepts_dir_name}/{domain}/{id}.md"}]
        concept = Concept(
            id=id, title=title, domain=domain, summary=summary,
            references=references or [], source=source, status=status,
            tags=tags or [], body=body,
        )
        concept.created = datetime.date.today()
        self._validate(concept)
        # Ids are globally unique across ALL domains: check graph membership,
        # not just this domain's file path — a same-id file elsewhere would
        # otherwise be silently shadowed by whichever sorts first on load.
        if id in self._graph().concepts or self._path_for(concept).exists():
            raise ToolError(f"concept '{id}' already exists; use update")
        path = self._save(concept)
        return self._result(concept, [path], f"Create concept '{id}'", created=True)

    def update(
        self,
        id: str,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        domain: Optional[str] = None,
        status: Optional[str] = None,
        references: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        body: Optional[str] = None,
        append_body: Optional[str] = None,
        source: Optional[List[dict]] = None,
        expected_version: Optional[str] = None,
    ) -> dict:
        concept = self._load(id, "update")
        old_path = concept.path
        if expected_version:
            # Optimistic concurrency: the caller read a version (from get) and
            # only wants this write applied if nothing changed in between.
            current = _file_version(old_path)
            if current != expected_version:
                raise ToolError(
                    f"concept '{id}' changed since you read it "
                    f"(version {current}, you expected {expected_version}) — "
                    "re-read it and re-apply your change"
                )

        if title is not None:
            concept.title = title
        if summary is not None:
            concept.summary = summary
        if status is not None:
            concept.status = status
        if references is not None:
            concept.references = list(references)
        if tags is not None:
            concept.tags = list(tags)
        if source is not None:
            concept.source = list(source)
        if body is not None:
            concept.body = body
        if append_body:
            concept.body = (concept.body.rstrip("\n") + "\n\n" + append_body).strip("\n")
        if domain is not None:
            concept.domain = domain

        commit_paths, moved_from = self._relocate(concept, old_path)
        result = self._result(concept, commit_paths, f"Update concept '{id}'", created=False)
        if moved_from is not None:
            result["moved_from"] = str(moved_from)
        return result

    # --- tools (lifecycle) --------------------------------------------------

    def deprecate(self, id: str, superseded_by: Optional[str] = None, reason: Optional[str] = None) -> dict:
        """Mark a concept deprecated (it stays, still resolves). Non-breaking."""
        concept = self._load(id, "deprecate")
        concept.status = "deprecated"
        if superseded_by is not None:
            concept.superseded_by = superseded_by
        if reason:
            note = f"> **Deprecated.** {reason}"
            if superseded_by:
                note += f" Superseded by [[{superseded_by}]]."
            concept.body = (note + "\n\n" + concept.body).strip("\n")
        return self._result(concept, [self._save(concept)], f"Deprecate concept '{id}'", created=False)

    def merge(self, id: str, into: str, repoint: bool = False) -> dict:
        """Merge ``id`` into ``into``: ``id`` becomes a redirect tombstone.

        The target absorbs ``id``'s provenance; inbound references keep resolving
        through the redirect. With ``repoint=True`` those references are also
        rewritten to point straight at the target.
        """
        if id == into:
            raise ToolError("cannot merge a concept into itself")
        graph = self._graph()
        src = graph.concepts.get(id)
        if src is None:
            raise ToolError(f"no concept with id '{id}' to merge")
        tgt = graph.concepts.get(into)
        if tgt is None:
            raise ToolError(f"merge target '{into}' does not exist")
        if tgt.redirect:
            raise ToolError(f"target '{into}' is itself a redirect; merge into '{graph.resolve(into)}'")

        tgt.source = _union_sources(tgt.source, src.source)
        src.status, src.redirect = "merged", into
        src.references, src.superseded_by = [], None
        src.body = f"Merged into [[{into}]]."

        to_write: List[Concept] = [tgt, src]
        repointed: List[str] = []
        if repoint:
            for cid, c in graph.concepts.items():
                if cid in (id, into) or id not in c.references:
                    continue
                c.references = _dedup([into if r == id else r for r in c.references])
                to_write.append(c)
                repointed.append(cid)

        # Check every mutated concept BEFORE writing any of them: a validation
        # (or write-access) failure — e.g. a legacy source concept that
        # predates the schema — must not leave a half-merged canon on disk,
        # target rewritten with absorbed provenance but no tombstone.
        for c in to_write:
            self._precheck_write(c)
        commit_paths = [self._save(c) for c in to_write]

        result = self._result(src, commit_paths, f"Merge '{id}' into '{into}'", created=False)
        result.update({"merged_into": into, "repointed": sorted(repointed)})
        return result

    def archive(self, id: str) -> dict:
        """Drop a concept from the active/searchable set; it stays resolvable."""
        concept = self._load(id, "archive")
        concept.status = "archived"
        return self._result(concept, [self._save(concept)], f"Archive concept '{id}'", created=False)

    def restore(self, id: str, status: str = "active") -> dict:
        """Bring an archived/deprecated concept back to a live status."""
        concept = self._load(id, "restore")
        if concept.redirect:
            raise ToolError(f"'{id}' is a merged redirect; un-merging isn't supported — edit it directly")
        if status not in ("active", "draft"):
            raise ToolError("restore status must be 'active' or 'draft'")
        concept.status = status
        concept.superseded_by = None
        return self._result(concept, [self._save(concept)], f"Restore concept '{id}'", created=False)

    def remove(self, id: str, force: bool = False) -> dict:
        """Hard-delete a concept. Refuses unless nothing depends on it.

        Prefer ``deprecate`` or ``merge`` — they keep inbound references
        resolving. Removal is gated on zero dependents (references, redirect
        targets, supersedes); ``force=true`` overrides and reports the breakage.
        """
        graph = self._graph()
        concept = graph.concepts.get(id)
        if concept is None:
            raise ToolError(f"no concept with id '{id}' to remove")
        deps = graph.dependents(id)
        if deps and not force:
            raise ToolError(
                f"{len(deps)} concept(s) depend on '{id}': {deps}. "
                f"Deprecate or merge instead, or pass force=true to break them."
            )
        if not access.can_write(concept, self.identity):
            raise ToolError(f"identity '{self.identity.name}' may not remove '{concept.id}'")
        path = Path(concept.path) if concept.path else self._path_for(concept)
        self._ensure_contained(path)
        path.unlink(missing_ok=True)
        committed, note = self._maybe_commit([path], f"Remove concept '{id}'")
        warnings = [f"broke dependents: {deps}"] if (deps and force) else []
        if note:
            warnings.append(note)
        return {
            "ok": True,
            "id": id,
            "removed": str(path),
            "dependents_broken": deps if (deps and force) else [],
            "committed": committed,
            "warnings": warnings,
        }

    # --- write path ---------------------------------------------------------

    def _path_for(self, concept: Concept) -> Path:
        return self.config.concepts_dir / concept.domain / f"{concept.id}.md"

    def _load(self, id: str, verb: str) -> Concept:
        concept = self._graph().concepts.get(id)
        if concept is None:
            raise ToolError(f"no concept with id '{id}' to {verb}")
        return concept

    def _validate(self, concept: Concept) -> None:
        # The loaded path may reflect an old domain; the write path is derived
        # from `domain`, so don't let the folder check compare against a stale dir.
        concept.path = None
        issues = validate_concept(
            concept, domains=self.config.domains, id_pattern=self.config.id_pattern
        )
        if issues:
            raise ToolError("; ".join(str(i) for i in issues))

    def _ensure_contained(self, path: Path) -> None:
        """Refuse any concept path that escapes the concepts directory.

        Containment must hold independently of the id/domain validation: a
        loosened ``schema.id_pattern`` in canonia.yml (or a future validation
        regression) must never turn an id into a filesystem escape.
        """
        root = self.config.concepts_dir.resolve()
        if not path.resolve().is_relative_to(root):
            raise ToolError(f"refusing a path outside the canon: {path.name}")

    def _precheck_write(self, concept: Concept) -> None:
        """The write gate without the write: access check + validation.

        Multi-concept operations (merge) run this over every concept first so
        a failure on the Nth concept can't leave the first N-1 written.
        """
        if not access.can_write(concept, self.identity):
            raise ToolError(f"identity '{self.identity.name}' may not write '{concept.id}'")
        self._validate(concept)

    def _save(self, concept: Concept) -> Path:
        """Validate then atomically write one concept to disk; return its path."""
        if not access.can_write(concept, self.identity):
            raise ToolError(f"identity '{self.identity.name}' may not write '{concept.id}'")
        # Provenance stamp: date (not time) so a same-day no-op rewrite stays
        # byte-identical and autocommit's nothing-to-commit path still applies.
        concept.updated = datetime.date.today()
        self._validate(concept)
        path = self._path_for(concept)
        self._ensure_contained(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        concept.save(path)
        return path

    def _relocate(self, concept: Concept, old_path):
        """Save ``concept``; if its file moved (domain change), stage both paths."""
        new_path = self._save(concept)
        paths = [new_path]
        moved_from = None
        if old_path and Path(old_path).resolve() != new_path.resolve():
            Path(old_path).unlink(missing_ok=True)
            moved_from = Path(old_path)
            paths.append(moved_from)
        return paths, moved_from

    def _result(self, concept: Concept, commit_paths, message: str, *, created: bool) -> dict:
        graph = self._graph()
        warnings = [
            f"reference '{r}' does not resolve yet"
            for r in concept.references
            if r not in graph.concepts
        ]
        if concept.superseded_by and concept.superseded_by not in graph.concepts:
            warnings.append(f"superseded_by '{concept.superseded_by}' does not resolve yet")
        result = {
            "ok": True,
            "id": concept.id,
            "path": str(self._path_for(concept)),
            "status": concept.status,
            "created": created,
            "committed": False,
            # Version of what was just written — lets a caller chain a
            # compare-and-swap update without an extra get.
            "version": _file_version(self._path_for(concept)),
            "warnings": warnings,
        }
        committed, note = self._maybe_commit(commit_paths, message)
        result["committed"] = committed
        if note:
            warnings.append(note)
        return result

    # --- git (autocommit; local only, never pushes) -------------------------

    def _maybe_commit(self, paths, message: str):
        if not (self.autocommit and paths):
            return False, None
        return self._git_commit(paths, message)

    def _git_commit(self, paths, message: str):
        """Stage ``paths`` and commit in the canon repo. Returns (committed, note)."""
        root = self.config.root_dir
        author = None
        if self.identity is not access.ANONYMOUS and self.identity.name != "anonymous":
            author = f"{self.identity.name} <{self.identity.kind}@canonia>"
        try:
            self._run_git(["add", "--", *[str(p) for p in paths]], root)
            args = ["commit", "-m", message]
            if author:
                args += ["--author", author]
            proc = self._run_git(args, root)
        except FileNotFoundError:
            return False, "autocommit skipped: git not found"
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            if "nothing to commit" in detail:
                return False, None  # no-op change; not an error
            if "not a git repository" in detail:
                return False, ("autocommit skipped: the canon is not a git repository "
                               "(run `git init` for versioned, attributable writes)")
            return False, f"autocommit failed: {detail}"
        return True, None

    @staticmethod
    def _run_git(args, cwd):
        return subprocess.run(
            ["git", *args], cwd=str(cwd),
            capture_output=True, text=True,
        )


# --- helpers ----------------------------------------------------------------

def _dedup(items: List[str]) -> List[str]:
    """Order-preserving de-duplication."""
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _union_sources(a: List[dict], b: List[dict]) -> List[dict]:
    """Union two source lists, de-duplicated by (repo, path) — the merge fold."""
    out, seen = [], set()
    for entry in list(a) + list(b):
        key = (entry.get("repo"), entry.get("path"))
        if key not in seen:
            seen.add(key)
            out.append(dict(entry))
    return out


# --- search scoring ---------------------------------------------------------

# Minimum cosine for a concept with no keyword hit to still surface in hybrid
# search — keeps semantically-adjacent-but-unrelated concepts out of results.
_SEM_FLOOR = 0.25


def _tokens(text: str) -> List[str]:
    out, cur = [], []
    for ch in (text or "").lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _score(concept: Concept, terms: List[str]) -> int:
    if not terms:
        return 0
    id_tokens = set(_tokens(concept.id)) | {concept.id}
    title_tokens = set(_tokens(concept.title))
    summary_tokens = set(_tokens(concept.summary))
    tag_tokens = set(_tokens(" ".join(concept.tags)))
    body_tokens = set(_tokens(concept.body))
    score = 0
    for term in terms:
        if term in id_tokens:
            score += 6
        if term in title_tokens:
            score += 5
        if term in tag_tokens:
            score += 3
        if term in summary_tokens:
            score += 3
        if term in body_tokens:
            score += 1
    return score


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": {"type": "string"}}
_SOURCE_LIST = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"repo": _STR, "path": _STR},
        "required": ["path"],
    },
}

TOOLS: List[dict] = [
    {
        "name": "search",
        "title": "Search concepts",
        "description": "Keyword search across concept id, title, summary, tags, and body. "
                       "Returns ranked hits (id, title, domain, summary). Optionally filter by domain.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {**_STR, "description": "Search terms."},
                "domain": {**_STR, "description": "Restrict to one domain (optional)."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
                "include_archived": {"type": "boolean", "description": "Include archived concepts (default false)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get",
        "title": "Get a concept",
        "description": "Fetch one concept by id: frontmatter, body, and backlinks (referenced_by). "
                       "A merged (redirect) id transparently forwards to its canonical concept "
                       "unless follow=false. The returned 'version' token can be passed to "
                       "update as expected_version to detect concurrent edits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {**_STR, "description": "The concept id (kebab-case)."},
                "include_body": {"type": "boolean", "description": "Include the markdown body (default true)."},
                "follow": {"type": "boolean", "description": "Follow redirects to the canonical concept (default true)."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "create",
        "title": "Create a concept",
        "description": "Create a new concept file. Fails if the id already exists (use update). "
                       "Unresolved references are returned as warnings, not errors. "
                       "When the server runs with an LLM identity, new concepts default to "
                       "status 'draft' (still searchable/resolvable) pending human review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": _STR, "title": _STR, "domain": _STR, "summary": _STR,
                "references": _STR_LIST, "source": _SOURCE_LIST,
                "body": _STR,
                "status": {**_STR, "description": "default: active (draft under an LLM identity)"},
                "tags": _STR_LIST,
            },
            "required": ["id", "title", "domain", "summary"],
        },
    },
    {
        "name": "update",
        "title": "Update a concept",
        "description": "Update an existing concept. Only the fields you pass change; "
                       "'append_body' adds a paragraph, 'body' replaces it. Changing 'domain' relocates the file. "
                       "Pass get's 'version' as expected_version to fail cleanly if someone "
                       "else changed the concept since you read it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": _STR, "title": _STR, "summary": _STR, "domain": _STR, "status": _STR,
                "references": _STR_LIST, "tags": _STR_LIST,
                "body": _STR, "append_body": _STR, "source": _SOURCE_LIST,
                "expected_version": {**_STR, "description": "Version token from get; the update "
                                     "is rejected if the concept changed since (optional)."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "list_domains",
        "title": "List domains",
        "description": "List the canon's domains with a concept count for each, "
                       "plus counts of archived concepts and redirects.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "deprecate",
        "title": "Deprecate a concept",
        "description": "Mark a concept deprecated. It stays and keeps resolving (non-breaking); "
                       "optionally name the concept that supersedes it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": _STR,
                "superseded_by": {**_STR, "description": "Id of the replacement concept (optional)."},
                "reason": {**_STR, "description": "Short note prepended to the body (optional)."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "merge",
        "title": "Merge (redirect) a concept",
        "description": "Merge one concept into another: it becomes a redirect tombstone forwarding "
                       "to the target, which absorbs its provenance. Inbound references keep resolving. "
                       "Set repoint=true to also rewrite those references to the target.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {**_STR, "description": "The concept to retire."},
                "into": {**_STR, "description": "The canonical concept it folds into."},
                "repoint": {"type": "boolean", "description": "Rewrite inbound references to the target (default false)."},
            },
            "required": ["id", "into"],
        },
    },
    {
        "name": "archive",
        "title": "Archive a concept",
        "description": "Drop a concept from the active/searchable set. It stays on disk and keeps "
                       "resolving; restore brings it back.",
        "inputSchema": {"type": "object", "properties": {"id": _STR}, "required": ["id"]},
    },
    {
        "name": "restore",
        "title": "Restore a concept",
        "description": "Return an archived or deprecated concept to a live status (active/draft).",
        "inputSchema": {
            "type": "object",
            "properties": {"id": _STR, "status": {**_STR, "description": "active or draft (default active)."}},
            "required": ["id"],
        },
    },
    {
        "name": "remove",
        "title": "Remove a concept (hard delete)",
        "description": "Permanently delete a concept. Refuses if anything depends on it — prefer "
                       "deprecate or merge. Pass force=true to delete anyway and report what breaks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": _STR,
                "force": {"type": "boolean", "description": "Delete even if there are dependents (default false)."},
            },
            "required": ["id"],
        },
    },
]

_TOOL_NAMES = {t["name"] for t in TOOLS}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 over stdio (MCP stdio transport)
# ---------------------------------------------------------------------------

# JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class StdioServer:
    """Speaks the MCP stdio transport for one :class:`CanonService`."""

    def __init__(self, service: CanonService, stdin=None, stdout=None, stderr=None):
        self.service = service
        self._in = stdin or sys.stdin
        self._out = stdout or sys.stdout
        self._err = stderr or sys.stderr

    def log(self, message: str) -> None:
        print(message, file=self._err, flush=True)

    def run(self) -> None:
        who = self.service.identity
        tag = "" if who is access.ANONYMOUS else f" as {who.name} ({who.kind})"
        self.log(f"canonia MCP server ({__version__}) on stdio{tag} — {len(self.service._graph())} concepts")
        for line in self._in:
            line = line.strip()
            if not line:
                continue
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self._send_error(None, PARSE_ERROR, "parse error")
            return
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            self._send_error(message.get("id") if isinstance(message, dict) else None,
                             INVALID_REQUEST, "invalid request")
            return

        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        # Notifications (no id) get no response.
        if msg_id is None:
            return
        if not isinstance(method, str):
            self._send_error(msg_id, INVALID_REQUEST, "missing or invalid method")
            return

        try:
            result = self._dispatch(method, params)
        except _RpcError as exc:
            self._send_error(msg_id, exc.code, exc.message)
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._send_error(msg_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
            return
        self._send_result(msg_id, result)

    def _dispatch(self, method: str, params: dict):
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._call_tool(params)
        raise _RpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    def _initialize(self, params: dict) -> dict:
        client_version = params.get("protocolVersion")
        return {
            "protocolVersion": client_version or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
        }

    def _call_tool(self, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or name not in _TOOL_NAMES:
            raise _RpcError(METHOD_NOT_FOUND, f"unknown tool: {name}")
        handler = getattr(self.service, name)
        try:
            structured = handler(**args)
        except ToolError as exc:
            return _tool_error(str(exc))
        except TypeError as exc:
            # Bad/missing arguments for the tool signature.
            return _tool_error(f"invalid arguments for {name}: {exc}")
        return {
            "content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False, indent=2)}],
            "structuredContent": structured,
            "isError": False,
        }

    def _send_result(self, msg_id, result) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _send_error(self, msg_id, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})

    def _write(self, obj: dict) -> None:
        # One JSON message per line, no embedded newlines (stdio transport rule).
        self._out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._out.flush()


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def serve(
    canon_dir=".",
    autocommit: Optional[bool] = None,
    identity: Optional[access.Identity] = None,
    **_ignored,
) -> None:
    """Run the MCP server on stdio for the canon at ``canon_dir``."""
    service = CanonService(Path(canon_dir), identity=identity, autocommit=autocommit)
    StdioServer(service).run()
