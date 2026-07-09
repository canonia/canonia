"""Load and validate the private task pack (schema: canon-evals/README.md).

The pack lives OUTSIDE this repo (prompts/rubrics/gold sets reference private
canon content). This module is pure loading + validation; it never copies pack
content anywhere inside the canonia repo.
"""
import re
from pathlib import Path

import yaml

ARMS = ("A", "B", "C")
CLUSTERS = ("infra-ops", "process", "lore")
RUBRIC_KINDS = ("llm", "regex", "file_exists", "command")

_TASK_ID_RE = re.compile(r"^T\d{2}$")
# Prompts and CLAUDE.md bodies must never hint at the canon; the arm blocks do
# that (authoring rule 4). Enforced mechanically so a draft can't slip through.
_CANON_HINT_RE = re.compile(r"canonia|\bcanon\b|knowledge.?graph|mcp", re.IGNORECASE)
_BODY_PENDING_MARKER = "canonia:body-pending"

REQUIRED_CONFIG_KEYS = (
    "canonia_repo", "canonia_commit", "canonia_venv",
    "canon_repo", "canon_commit", "repos_root",
    "models", "reps", "concurrency", "run_timeout_s", "max_turns",
    "rate_limit_wait_s",
)


class PackError(Exception):
    pass


def load_config(pack_dir):
    pack_dir = Path(pack_dir).resolve()
    cfg_path = pack_dir / "config.yml"
    if not cfg_path.is_file():
        raise PackError(f"no config.yml in pack: {pack_dir}")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise PackError("config.yml is not a mapping")
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in cfg]
    if missing:
        raise PackError(f"config.yml missing keys: {missing}")
    for k in ("fleet", "spotcheck", "judge"):
        if k not in cfg["models"]:
            raise PackError(f"config.yml models missing '{k}'")
    cfg["pack_dir"] = str(pack_dir)
    cfg["canonia_bin"] = str(Path(cfg["canonia_venv"]) / "bin" / "canonia")
    return cfg


def load_tasks(pack_dir, include_drafts=False, only=None):
    """Return task dicts, sorted by id. `only` is an iterable of task ids."""
    tasks_dir = Path(pack_dir) / "tasks"
    tasks = []
    for path in sorted(tasks_dir.glob("*.yml")):
        t = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(t, dict):
            raise PackError(f"{path.name}: not a mapping")
        t["_file"] = str(path)
        tasks.append(t)
    seen = set()
    for t in tasks:
        tid = t.get("id")
        if tid in seen:
            raise PackError(f"duplicate task id {tid}")
        seen.add(tid)
    if only is not None:
        only = set(only)
        unknown = only - seen
        if unknown:
            raise PackError(f"unknown task ids requested: {sorted(unknown)}")
        tasks = [t for t in tasks if t["id"] in only]
    if not include_drafts:
        tasks = [t for t in tasks if t.get("status") == "approved"]
    return sorted(tasks, key=lambda t: t["id"])


def validate_task(task, pack_dir, canon_concepts_dir=None):
    """Return a list of problem strings (empty = valid).

    With `canon_concepts_dir` (a checkout's concepts/ root) gold concepts are
    checked to exist and to have real bodies (no body-pending stubs) —
    authoring rule 1.
    """
    p = []
    name = task.get("_file", "?")

    tid = task.get("id", "")
    if not _TASK_ID_RE.match(str(tid)):
        p.append(f"{name}: bad id {tid!r}")
    if task.get("cluster") not in CLUSTERS:
        p.append(f"{tid}: bad cluster {task.get('cluster')!r}")
    if task.get("status") not in ("draft", "approved"):
        p.append(f"{tid}: bad status {task.get('status')!r}")
    if not str(task.get("title", "")).strip():
        p.append(f"{tid}: missing title")

    repo = str(task.get("repo", ""))
    if not repo:
        p.append(f"{tid}: missing repo")
    elif repo.startswith("template:"):
        tpl = Path(pack_dir) / "templates" / repo.split(":", 1)[1]
        if not tpl.is_dir():
            p.append(f"{tid}: template dir missing: {tpl}")
    elif not re.fullmatch(r"[0-9a-f]{40}", str(task.get("commit", ""))):
        p.append(f"{tid}: real repo needs a full 40-char pinned commit")

    for path in task.get("scrub", []) or []:
        pp = Path(path)
        if pp.is_absolute() or ".." in pp.parts:
            p.append(f"{tid}: scrub path must be relative, no '..': {path}")

    for field in ("prompt", "claude_md", "leakage_note"):
        if not str(task.get(field, "")).strip():
            p.append(f"{tid}: missing {field}")
    for field in ("prompt", "claude_md"):
        hit = _CANON_HINT_RE.search(str(task.get(field, "")))
        if hit:
            p.append(f"{tid}: {field} leaks a canon hint ({hit.group(0)!r}) — rule 4")

    gold = task.get("gold_concepts") or []
    if not gold:
        p.append(f"{tid}: empty gold_concepts")
    if canon_concepts_dir is not None:
        for cid in gold:
            hits = list(Path(canon_concepts_dir).glob(f"*/{cid}.md"))
            if not hits:
                p.append(f"{tid}: gold concept not in canon: {cid}")
            elif _BODY_PENDING_MARKER in hits[0].read_text(encoding="utf-8"):
                p.append(f"{tid}: gold concept is a body-pending stub: {cid}")

    rubric = task.get("rubric") or []
    if not 3 <= len(rubric) <= 10:
        p.append(f"{tid}: rubric needs 3-10 items, has {len(rubric)}")
    rids = set()
    for item in rubric:
        rid = item.get("id", "?")
        if rid in rids:
            p.append(f"{tid}: duplicate rubric id {rid}")
        rids.add(rid)
        kind = item.get("kind")
        if kind not in RUBRIC_KINDS:
            p.append(f"{tid}/{rid}: bad kind {kind!r}")
        elif kind == "llm" and not str(item.get("text", "")).strip():
            p.append(f"{tid}/{rid}: llm item needs text")
        elif kind == "regex" and not (item.get("file") and item.get("pattern")):
            p.append(f"{tid}/{rid}: regex item needs file + pattern")
        elif kind == "file_exists" and not item.get("file"):
            p.append(f"{tid}/{rid}: file_exists item needs file")
        elif kind == "command" and not str(item.get("cmd", "")).strip():
            p.append(f"{tid}/{rid}: command item needs cmd")
    return p
