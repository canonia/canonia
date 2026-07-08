# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""``canonia`` command-line interface.

Subcommands: ``init`` (scaffold a canon), ``import`` (curated / zero-config),
``validate`` (run the gates), ``index`` (build/query the semantic index),
``serve`` (MCP server), and ``build`` (static site).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

from canonia import __version__
from canonia.config import CONFIG_FILENAME, CanoniaConfig, SourceRepo
from canonia.graph import Graph
from canonia.importer import import_curated, import_zeroconfig
from canonia.importer.plan import ImportPlan
from canonia.schema import DEFAULT_DOMAINS, Issue

# --- helpers ----------------------------------------------------------------

def _load_config(canon: Optional[str]) -> Optional[CanoniaConfig]:
    start = Path(canon) if canon else Path.cwd()
    try:
        return CanoniaConfig.load(start)
    except FileNotFoundError:
        return None


def _parse_sources(pairs: List[str]) -> Dict[str, SourceRepo]:
    """Parse ``--source name=path[:prefix]`` flags into a repo registry."""
    repos: Dict[str, SourceRepo] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--source expects name=path[:prefix], got {pair!r}")
        name, rest = pair.split("=", 1)
        prefix = ""
        # A trailing ':prefix' is a relative subdir; unix absolute paths have no colon.
        if ":" in rest:
            rest, prefix = rest.rsplit(":", 1)
        repos[name.strip()] = SourceRepo(path=Path(rest).expanduser().resolve(), prefix=prefix)
    return repos


def _graph_from_plan(plan: ImportPlan) -> Graph:
    graph = Graph()
    for item in plan.emitted:
        graph.add(item.concept)
    return graph


def _overlay_existing(graph: Graph, plan: ImportPlan, out_dir: Path) -> None:
    """Add the on-disk concepts this import will NOT overwrite (gate context).

    Without ``--prune`` the post-commit canon is the emitted set overlaid on
    what's already on disk, so the gate must see both: a reference to an
    existing concept is not dangling, and an emitted id that clashes with an
    existing concept at a *different* path is a genuine post-commit duplicate.
    """
    if not out_dir.exists():
        return
    targets = {(out_dir / e.rel_path).resolve() for e in plan.emitted}
    existing = Graph.load(out_dir)
    for concept in list(existing.concepts.values()) + list(existing.duplicates):
        if concept.path is not None and Path(concept.path).resolve() in targets:
            continue  # this import overwrites that file
        graph.add(concept)


def _print_issues(issues: List[Issue]) -> None:
    for issue in issues:
        print(f"  ✗ {issue}", file=sys.stderr)


# --- commands ---------------------------------------------------------------

def cmd_init(args) -> int:
    root = Path(args.directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    domains = list(args.domains.split(",")) if args.domains else list(DEFAULT_DOMAINS)
    cfg = root / CONFIG_FILENAME
    if cfg.exists() and not args.force:
        print(f"{cfg} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    cfg.write_text(
        "# Canonia config — binds this canon to the Canonia framework.\n"
        "canon:\n"
        "  root: concepts\n"
        f"  domains: [{', '.join(domains)}]\n"
        "schema:\n"
        '  id_pattern: "^[a-z0-9][a-z0-9-]*$"\n',
        encoding="utf-8",
    )
    for d in domains:
        (root / "concepts" / d).mkdir(parents=True, exist_ok=True)
        (root / "concepts" / d / ".gitkeep").touch()
    # Keep generated (and potentially sensitive) output out of version control.
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Canonia generated output — never commit/publish (may hold sensitive\n"
            "# content); serve the site privately behind an auth edge instead.\n"
            ".canonia/\nsite/\n",
            encoding="utf-8",
        )
    print(f"Initialized canon at {root} (domains: {', '.join(domains)})")
    return 0


def cmd_import(args) -> int:
    config = _load_config(args.canon)
    domains = config.domains if config else list(DEFAULT_DOMAINS)
    id_pattern = config.id_pattern if config else None

    out_dir = Path(args.out).resolve() if args.out else (
        config.concepts_dir if config else Path.cwd() / "concepts"
    )

    if args.zero_config:
        if not args.domain:
            print("--zero-config requires --domain", file=sys.stderr)
            return 1
        plan = import_zeroconfig(Path(args.zero_config), domain=args.domain, repo=args.repo)
    else:
        if not args.mapping:
            print("curated import requires --mapping (or use --zero-config)", file=sys.stderr)
            return 1
        repos: Dict[str, SourceRepo] = dict(config.sources) if config else {}
        repos.update(_parse_sources(args.source))
        plan = import_curated(Path(args.mapping), repos)

    plan.out_dir = out_dir

    # --prune reconciles the canon to this import: concept files the sources no
    # longer produce are removed. Computed against the pre-import disk state so
    # the same list is reported (dry-run) and applied (commit). With prune on,
    # the committed canon equals the emitted set, so the gate check below — run
    # on the emitted graph — is an accurate prediction of the final on-disk state.
    pruned = plan.reconcile(out_dir) if getattr(args, "prune", False) else None

    # Gate-check the *predicted post-commit canon*: with --prune that is
    # exactly the emitted set (everything else is removed); without it, the
    # emitted set overlaid on what's already on disk.
    graph = _graph_from_plan(plan)
    if pruned is None:
        _overlay_existing(graph, plan, out_dir)
    if id_pattern:
        issues = graph.validate(domains=domains, id_pattern=id_pattern)
    else:
        issues = graph.validate(domains=domains)

    # A failing gate blocks --commit: never write a canon that would fail its
    # own gates. --force overrides (writes anyway; the exit code stays 1).
    blocked = bool(args.commit and issues and not args.force)

    if args.commit and not blocked:
        written = plan.write(out_dir)
        removed = plan.apply_prune(pruned) if pruned else []
        print(plan.render_report(committed=True, pruned=pruned))
        print(f"\nWrote {len(written)} files under {out_dir}")
        if removed:
            print(f"Pruned {len(removed)} files no longer produced by the sources")
    else:
        print(plan.render_report(committed=False, pruned=pruned))
        if blocked:
            print(
                f"\n(gates failed — NOTHING was written to {out_dir}; "
                "fix the reported issues or re-run with --force)"
            )
        else:
            note = "" if not pruned else f" ({len(pruned)} would be pruned)"
            print(f"\n(dry-run — no files written; re-run with --commit to write to {out_dir}){note}")

    if getattr(args, "check_dupes", False):
        _report_dupes(plan, config, out_dir, args.dupe_threshold)

    print()
    if issues:
        print(f"Gates: {len(issues)} issue(s) — schema / dangling-reference:", file=sys.stderr)
        _print_issues(issues)
        return 1
    print(f"Gates: OK — post-import canon has {len(graph)} concepts, schema + dangling-reference passed.")
    return 0


def _report_dupes(plan: ImportPlan, config, out_dir: Path, threshold: float) -> None:
    """Advisory semantic near-duplicate check over the import (never fails it)."""
    from canonia import index

    if not index.deps_available():
        print(
            "\n  ! --check-dupes skipped: needs the extra (pip install 'canonia[semantic]')",
            file=sys.stderr,
        )
        return
    new_concepts = [e.concept for e in plan.emitted]
    if not new_concepts:
        return
    # Compare against concepts already in the target canon, if any exist on disk.
    existing = []
    if out_dir.exists():
        emitted_ids = {c.id for c in new_concepts}
        existing = [c for c in Graph.load(out_dir).concepts.values() if c.id not in emitted_ids]
    try:
        model = index.load_model(config) if config else None
        if model is None:
            print("\n  ! --check-dupes skipped: no canonia.yml to locate the model", file=sys.stderr)
            return
        pairs = index.near_duplicates(new_concepts, model, existing=existing, threshold=threshold)
    except Exception as exc:  # model download/load failure shouldn't sink the import
        print(f"\n  ! --check-dupes skipped: {exc}", file=sys.stderr)
        return

    print(f"\nDuplicate check (cosine ≥ {threshold}):")
    if not pairs:
        print("  none — no near-duplicate concepts found.")
        return
    within = [p for p in pairs if p.kind == "within-import"]
    vs_canon = [p for p in pairs if p.kind == "vs-canon"]
    if within:
        print(f"  {len(within)} within this import (candidates to merge/dedup in mapping.yml):")
        for p in within:
            print(f"    {p.score:.3f}  {p.a_id}  ~  {p.b_id}")
    if vs_canon:
        print(f"  {len(vs_canon)} vs. existing canon (already covered? review before committing):")
        for p in vs_canon:
            print(f"    {p.score:.3f}  {p.a_id}  ~  {p.b_id} (existing)")


def cmd_validate(args) -> int:
    config = _load_config(args.canon)
    if args.directory:
        concepts_dir = Path(args.directory)
    elif config:
        concepts_dir = config.concepts_dir
    else:
        concepts_dir = Path.cwd() / "concepts"

    if not concepts_dir.exists():
        print(f"no concepts directory at {concepts_dir}", file=sys.stderr)
        return 1

    graph = Graph.load(concepts_dir)
    if config:
        issues = graph.validate(domains=config.domains, id_pattern=config.id_pattern)
    else:
        issues = graph.validate(domains=list(DEFAULT_DOMAINS))
    if issues:
        print(f"{len(issues)} issue(s) in {len(graph)} concepts:", file=sys.stderr)
        _print_issues(issues)
        return 1
    print(f"OK — {len(graph)} concepts, schema + dangling-reference gates passed.")
    return 0


def cmd_serve(args) -> int:
    from canonia import server

    canon = args.canon or "."
    if _load_config(canon) is None:
        print(f"no {CONFIG_FILENAME} at or above {Path(canon).resolve()}", file=sys.stderr)
        return 1
    # Flags fall back to $CANONIA_IDENTITY / $CANONIA_IDENTITY_KIND; a named
    # identity with no kind defaults to llm (serve is the agent interface).
    try:
        identity = server.resolve_identity(args.identity, args.identity_kind)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # --autocommit / --no-autocommit override canonia.yml's git.autocommit.
    try:
        server.serve(canon, autocommit=args.autocommit, identity=identity)
    except KeyboardInterrupt:  # pragma: no cover
        return 0
    return 0


def cmd_index(args) -> int:
    from canonia import index

    config = _load_config(args.canon)
    if config is None:
        print(f"no {CONFIG_FILENAME} at or above {Path(args.canon or '.').resolve()}", file=sys.stderr)
        return 1
    if not index.deps_available():
        print("semantic index needs the extra: pip install 'canonia[semantic]'", file=sys.stderr)
        return 1

    if args.action == "build":
        graph = Graph.load(config.concepts_dir)
        choice = index.resolve_backend(config.index_backend)
        if choice.fell_back:
            print(f"  ! canonia.yml requests backend '{choice.requested}' — {choice.reason}", file=sys.stderr)
        stats = index.build_index(
            config, list(graph.concepts.values()),
            log=lambda m: print(f"  {m}", file=sys.stderr),
        )
        print(f"Index built → {index.index_path_for(config)}")
        print(
            f"  {stats.total} concepts · +{stats.added} new · ~{stats.updated} changed · "
            f"{stats.unchanged} unchanged · -{stats.removed} removed"
        )
        return 0

    if args.action == "stats":
        idx = index.open_index(config)
        if idx is None:
            print("no index yet — run: canonia index build", file=sys.stderr)
            return 1
        with idx:
            model = idx.conn.execute("SELECT value FROM meta WHERE key='model'").fetchone()
            choice = index.resolve_backend(config.index_backend)
            print(f"Index: {index.index_path_for(config)}")
            print(f"  {len(idx)} vectors · model {model[0] if model else '?'} · backend {choice.name} ({choice.reason})")
            if choice.fell_back:
                print(f"  ! canonia.yml requests backend '{choice.requested}' — {choice.reason}", file=sys.stderr)
        return 0

    if args.action == "search":
        if not args.query:
            print("index search needs a query", file=sys.stderr)
            return 1
        searcher = index.SemanticSearcher(config)
        if not searcher.available:
            print("no index yet — run: canonia index build", file=sys.stderr)
            return 1
        scores = searcher.scores(args.query, args.domain)
        graph = Graph.load(config.concepts_dir)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[: max(1, args.k)]
        for cid, sim in ranked:
            c = graph.concepts.get(cid)
            print(f"  {sim:.3f}  {cid}  — {c.title if c else ''}")
        if not ranked:
            print("  (no results)")
        return 0

    if args.action == "dupes":
        idx = index.open_index(config)
        if idx is None:
            print("no index yet — run: canonia index build", file=sys.stderr)
            return 1
        with idx:
            pairs = idx.duplicate_pairs(args.threshold)
        graph = Graph.load(config.concepts_dir)
        if not pairs:
            print(f"No concept pairs above cosine {args.threshold}.")
            return 0
        print(f"{len(pairs)} near-duplicate pair(s) at cosine ≥ {args.threshold}:")
        for a, b, sim in pairs:
            ta = graph.concepts.get(a)
            tb = graph.concepts.get(b)
            print(f"  {sim:.3f}  {a} ({ta.title if ta else '?'})  ~  {b} ({tb.title if tb else '?'})")
        return 0

    print(f"unknown index action: {args.action}", file=sys.stderr)  # pragma: no cover
    return 1


def cmd_build(args) -> int:
    from canonia import site

    canon = args.canon or "."
    if _load_config(canon) is None:
        print(f"no {CONFIG_FILENAME} at or above {Path(canon).resolve()}", file=sys.stderr)
        return 1
    result = site.build_site(canon, out_dir=args.out)
    print(
        f"Built site → {result['out_dir']}\n"
        f"  {result['pages']} pages · {result['live']} live · "
        f"{result['redirects']} redirects · {result['archived']} archived"
    )
    if result["broken_links"]:
        print(f"  ! {result['broken_links']} broken wikilink(s) in bodies", file=sys.stderr)
    print(f"  open {result['out_dir']}/index.html")
    # The site has NO built-in access control (governance is a future module).
    print(
        "  ⚠ no access control — serve privately (tailnet/loopback) or behind an\n"
        "    auth edge; do NOT expose it on a public interface. See docs/deploying.md.",
        file=sys.stderr,
    )
    return 0


# --- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canonia", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"canonia {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="scaffold a new canon (canonia.yml + concepts/)")
    p_init.add_argument("directory", nargs="?", default=".", help="canon root (default: .)")
    p_init.add_argument("--domains", help="comma-separated domain list")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing canonia.yml")
    p_init.set_defaults(func=cmd_init)

    p_imp = sub.add_parser("import", help="import concepts (dry-run by default)")
    p_imp.add_argument("--mapping", help="curated mode: path to mapping.yml")
    p_imp.add_argument("--zero-config", help="zero-config mode: a folder of markdown")
    p_imp.add_argument("--domain", help="zero-config: domain for all imported files")
    p_imp.add_argument("--repo", default="local", help="zero-config: provenance repo name")
    p_imp.add_argument(
        "--source", action="append", default=[],
        help="curated: source repo as name=path[:prefix] (repeatable)",
    )
    p_imp.add_argument("--canon", help="canon dir (for canonia.yml); default: search from cwd")
    p_imp.add_argument("--out", help="output concepts dir (default: canon's concepts/)")
    p_imp.add_argument("--commit", action="store_true", help="write files (default: dry-run)")
    p_imp.add_argument(
        "--force", action="store_true",
        help="with --commit: write even when the gates fail (exit code stays 1)",
    )
    p_imp.add_argument(
        "--prune", action="store_true",
        help="remove existing concept files the sources no longer produce "
             "(reconcile; shown in the dry-run before --commit)",
    )
    p_imp.add_argument(
        "--check-dupes", dest="check_dupes", action="store_true",
        help="flag near-duplicate concepts (semantic; needs the 'semantic' extra)",
    )
    p_imp.add_argument(
        "--dupe-threshold", dest="dupe_threshold", type=float, default=0.9,
        help="cosine threshold for --check-dupes (default 0.9)",
    )
    p_imp.set_defaults(func=cmd_import)

    p_val = sub.add_parser("validate", help="run schema + dangling-reference gates")
    p_val.add_argument("directory", nargs="?", help="concepts dir (default: from canonia.yml)")
    p_val.add_argument("--canon", help="canon dir (for canonia.yml)")
    p_val.set_defaults(func=cmd_validate)

    p_serve = sub.add_parser("serve", help="run the MCP server on stdio")
    p_serve.add_argument("--canon", help="canon dir (for canonia.yml); default: cwd")
    p_serve.add_argument(
        "--autocommit", dest="autocommit", action="store_true", default=None,
        help="git-commit each write (local only, never pushes); overrides canonia.yml",
    )
    p_serve.add_argument(
        "--no-autocommit", dest="autocommit", action="store_false",
        help="disable autocommit even if canonia.yml enables it",
    )
    p_serve.add_argument(
        "--identity", help="who this server writes as (git author; falls back to $CANONIA_IDENTITY)",
    )
    p_serve.add_argument(
        "--identity-kind", dest="identity_kind", choices=["human", "llm"],
        help="identity kind; a named identity defaults to llm — LLM creates land as drafts",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_index = sub.add_parser("index", help="semantic embedding index (build / search / dupes / stats)")
    p_index.add_argument("action", choices=["build", "search", "dupes", "stats"])
    p_index.add_argument("query", nargs="?", help="search: the query text")
    p_index.add_argument("--canon", help="canon dir (for canonia.yml); default: cwd")
    p_index.add_argument("--domain", help="search: restrict to one domain")
    p_index.add_argument("--k", type=int, default=10, help="search: max results (default 10)")
    p_index.add_argument("--threshold", type=float, default=0.9, help="dupes: min cosine (default 0.9)")
    p_index.set_defaults(func=cmd_index)

    p_build = sub.add_parser("build", help="build the static site (browsable graph + backlinks)")
    p_build.add_argument("--canon", help="canon dir (for canonia.yml); default: cwd")
    p_build.add_argument("--out", help="output site dir (default: <canon>/site)")
    p_build.set_defaults(func=cmd_build)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
