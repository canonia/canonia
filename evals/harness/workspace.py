"""Per-run workspace assembly.

Isolation rule: every run gets its own copies — a task-repo snapshot at the
pinned commit, and (arm B) a private canon copy with a pre-built semantic
index, or (arm C) a private copy of the concept files as plain markdown.
Nothing writable is ever shared between runs.

The canon template is built once per fleet: canon checkout at the pinned
commit + `canonia index build` + a hybrid probe (P2-C5 guard). Runs then get
cheap copies of the template; arm B re-probes ITS copy before the agent starts.
"""
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .probe import probe_hybrid, ProbeError

# Arm-parity instruction blocks, appended to each task's curated CLAUDE.md.
# B is the block docs/using-with-agents.md prescribes (connect + instruct);
# C is the equivalent-strength pointer for the plain-markdown counterfactual.
# Both name the same topics and say "source of truth" + "consult first".
ARM_B_BLOCK = """
## Canonical knowledge — use the `canonia` MCP server

This project's durable, cross-repo knowledge lives in a canonical knowledge
store reachable through the `canonia` MCP tools. It is the source of truth —
prefer it over your own memory or copied notes.

**Before** answering a question or making a decision about a topic it may
cover (our conventions, architecture, infra and ops runbooks, process rules,
story lore — domains: process / infra / ops / lore), **consult it first**:
1. `search` for the topic (hybrid keyword + semantic).
2. `get` the most relevant concept(s) by `id` to read the full body, its
   `references`, and its backlinks (`referenced_by`).
3. Ground your work in what you find and cite concepts by their `id`.

When you learn something durable and reusable, write it back with `create` /
`update` rather than only noting it locally.
"""

ARM_C_BLOCK = """
## Project knowledge notes

This project's durable, cross-repo knowledge lives as markdown notes in
`../notes/` (one topic per file, organized by area: process / infra / ops /
lore). The notes are the source of truth — prefer them over your own memory
or copied notes.

**Before** answering a question or making a decision about a topic the notes
may cover (our conventions, architecture, infra and ops runbooks, process
rules, story lore), **consult the notes first**: grep/read the relevant files
and ground your work in what they say.
"""

ARM_BLOCKS = {"A": "", "B": ARM_B_BLOCK, "C": ARM_C_BLOCK}


class WorkspaceError(Exception):
    pass


def git_export(repo, commit, dest):
    """Extract `commit` of `repo` into `dest` (no .git — snapshots are re-inited)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar") as tmp:
        subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", "-o", tmp.name, commit],
            check=True, capture_output=True, text=True,
        )
        with tarfile.open(tmp.name) as tar:
            tar.extractall(dest)


def build_canon_template(cfg, force=False):
    """Build (or reuse) the fleet's canon template; returns its path.

    Refuses to return a template whose probe is not hybrid — the P2-C5 guard
    starts here and is repeated per arm-B run copy.
    """
    results = Path(cfg["pack_dir"]) / "results"
    template = results / f"_canon-template-{cfg['canon_commit'][:7]}"
    canon = template / "canon"
    if force and template.exists():
        shutil.rmtree(template)
    if not (canon / "canonia.yml").is_file():
        if template.exists():
            shutil.rmtree(template)
        git_export(cfg["canon_repo"], cfg["canon_commit"], canon)
        # Index BEFORE any serve ever sees this canon (P2-C5: availability
        # latches at first use; the order build -> serve is load-bearing).
        r = subprocess.run(
            [cfg["canonia_bin"], "index", "build", "--canon", str(canon)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            shutil.rmtree(template, ignore_errors=True)
            raise WorkspaceError(f"canonia index build failed:\n{r.stderr or r.stdout}")
    probe = probe_hybrid(cfg["canonia_bin"], canon)
    if not probe["ok"]:
        raise WorkspaceError(f"canon template probe is not hybrid: {probe}")
    (template / "template.json").write_text(json.dumps(
        {"canon_commit": cfg["canon_commit"], "probe": probe}, indent=2))
    return template


def build_workspace(cfg, task, arm, ws_dir, template_dir):
    """Assemble one run's workspace; returns a manifest fragment.

    Layout: ws/repo (agent cwd, git-inited baseline), plus ws/canon + ws/mcp.json
    (arm B) or ws/notes (arm C). Arm B's canon copy is re-probed here; a
    non-hybrid probe raises — the runner records the run as invalid, it never
    silently counts as hybrid.
    """
    ws = Path(ws_dir)
    if ws.exists():
        shutil.rmtree(ws)
    repo_dir = ws / "repo"

    repo = str(task["repo"])
    if repo.startswith("template:"):
        src = Path(cfg["pack_dir"]) / "templates" / repo.split(":", 1)[1]
        shutil.copytree(src, repo_dir)
    else:
        git_export(Path(cfg["repos_root"]) / repo, task["commit"], repo_dir)

    for rel in task.get("scrub", []) or []:
        target = repo_dir / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        # A missing scrub target is fine: the pin may predate the path.

    claude_md = str(task["claude_md"]).rstrip() + "\n" + ARM_BLOCKS[arm]
    (repo_dir / "CLAUDE.md").write_text(claude_md, encoding="utf-8")

    fragment = {"arm": arm, "ws": str(ws), "repo_snapshot": repo}
    if arm == "B":
        shutil.copytree(template_dir / "canon", ws / "canon")
        mcp_path = ws / "mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"canonia": {
            "type": "stdio",
            "command": cfg["canonia_bin"],
            "args": ["serve", "--canon", str(ws / "canon"), "--no-autocommit"],
        }}}, indent=2))
        probe = probe_hybrid(cfg["canonia_bin"], ws / "canon")
        fragment["probe"] = probe
        if not probe["ok"]:
            raise ProbeError(f"arm-B run canon probe not hybrid: {probe}")
        fragment["mcp_config"] = str(mcp_path)
    elif arm == "C":
        shutil.copytree(template_dir / "canon" / "concepts", ws / "notes")
        fragment["notes_dir"] = str(ws / "notes")

    _git(repo_dir, "init", "-q")
    _git(repo_dir, "add", "-A")
    _git(repo_dir, "-c", "user.name=eval-harness", "-c", "user.email=eval@local",
         "commit", "-qm", "baseline")
    return fragment


def collect_diff(repo_dir):
    """Everything the agent changed vs the baseline commit, incl. new files."""
    _git(repo_dir, "add", "-A")
    r = subprocess.run(["git", "-C", str(repo_dir), "diff", "--cached"],
                       capture_output=True, text=True)
    return r.stdout


def _git(repo_dir, *args):
    subprocess.run(["git", "-C", str(repo_dir), *args], check=True,
                   capture_output=True, text=True)
