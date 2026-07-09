"""Fleet orchestration: preflight guards, run matrix, resume, quota pacing.

Every run writes results/<run_id>/manifest.json; a relaunch skips runs whose
manifest says completed, so the fleet survives plan-window exhaustion, kills,
and multi-day pacing. Rate-limited runs sleep and retry in place.
"""
import datetime
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import claude_cli, workspace
from .probe import ProbeError

MAX_RATE_LIMIT_WAITS = 18  # x rate_limit_wait_s (default 20 min) = up to 6 h
MAX_TRANSIENT_RETRIES = 3  # fresh-workspace retries on API hiccups/timeouts


class PreflightError(Exception):
    pass


def preflight(cfg):
    """Hard guards before any tokens are spent. Returns an info dict."""
    problems = []

    for var in claude_cli.STRIP_ENV:
        if os.environ.get(var):
            problems.append(
                f"{var} is set — headless runs would bill the API pay-per-token, "
                "not the subscription. Unset it.")

    claude_v = _cmd_out(["claude", "--version"])
    if claude_v is None:
        problems.append("`claude` CLI not found on PATH")
    canonia_v = _cmd_out([cfg["canonia_bin"], "--version"])
    if canonia_v is None:
        problems.append(f"canonia venv binary not runnable: {cfg['canonia_bin']}")

    # The eval measures canonia AS-IS at the pinned commit. HEAD may advance
    # (the harness itself lands in commits), but the measured code paths must
    # be byte-identical to the pin, both in HEAD and in the working tree.
    head = _cmd_out(["git", "-C", cfg["canonia_repo"], "rev-parse", "HEAD"])
    measured = ["src", "tests", "pyproject.toml"]
    drifted = _cmd_out(["git", "-C", cfg["canonia_repo"], "diff", "--name-only",
                        cfg["canonia_commit"], "HEAD", "--", *measured])
    if drifted is None:
        problems.append(f"cannot diff against pinned {cfg['canonia_commit']!r}")
    elif drifted:
        problems.append("canonia measured paths drifted from the pinned commit "
                        f"(eval must run the audit-snapshot code):\n{drifted}")
    dirty = _cmd_out(["git", "-C", cfg["canonia_repo"], "status", "--porcelain",
                      "--", *measured])
    if dirty:
        problems.append(f"canonia src/tests dirty — eval must run pristine code:\n{dirty}")

    canon_head = _cmd_out(["git", "-C", cfg["canon_repo"], "rev-parse", "HEAD"])
    if canon_head != cfg["canon_commit"]:
        problems.append(f"canon HEAD {canon_head!r} != pinned {cfg['canon_commit']!r}")

    if problems:
        raise PreflightError("preflight failed:\n- " + "\n- ".join(problems))
    return {"claude_version": claude_v, "canonia_version": canonia_v,
            "canonia_commit": head, "canon_commit": canon_head}


def run_matrix(cfg, tasks, arms, reps, model, out_root=None, resume=True, log=print):
    """Run tasks x arms x reps; returns a list of manifest dicts."""
    out_root = Path(out_root or Path(cfg["pack_dir"]) / "results" / "runs")
    out_root.mkdir(parents=True, exist_ok=True)
    info = preflight(cfg)
    log(f"preflight ok: claude {info['claude_version']}, canonia {info['canonia_version']}")
    template = workspace.build_canon_template(cfg)
    log(f"canon template ready (hybrid probe passed): {template}")

    jobs = [(t, arm, rep) for t in tasks for arm in arms for rep in range(1, reps + 1)]
    log(f"{len(jobs)} runs ({len(tasks)} tasks x {arms} x {reps} reps), "
        f"concurrency {cfg['concurrency']}")

    manifests = []
    with ThreadPoolExecutor(max_workers=int(cfg["concurrency"])) as pool:
        futures = [pool.submit(run_one, cfg, t, arm, rep, model, out_root,
                               template, info, resume, log)
                   for t, arm, rep in jobs]
        for f in futures:
            manifests.append(f.result())
    done = sum(1 for m in manifests if m["status"] == "completed")
    log(f"fleet pass finished: {done}/{len(manifests)} completed")
    return manifests


def run_one(cfg, task, arm, rep, model, out_root, template, preflight_info,
            resume=True, log=print):
    run_id = f"{task['id']}-{arm}-r{rep}"
    run_dir = Path(out_root) / run_id
    manifest_path = run_dir / "manifest.json"

    if resume and manifest_path.is_file():
        old = json.loads(manifest_path.read_text())
        if old.get("status") == "completed":
            return old

    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": run_id, "task": task["id"], "cluster": task["cluster"],
        "arm": arm, "rep": rep, "model": model,
        "started": _now(), "status": "building",
        **preflight_info,
    }

    # Every attempt gets a FRESH workspace: a retried agent must never see the
    # half-finished work of a dead attempt (build_workspace rmtree's first).
    rate_waits = transient_retries = 0
    while True:
        try:
            frag = workspace.build_workspace(cfg, task, arm, run_dir / "ws", template)
            manifest.update(frag)
        except ProbeError as exc:
            # P2-C5 guard tripped: this run must not be counted as hybrid.
            manifest.update(status="invalid_probe", error=str(exc), ended=_now())
            _write(manifest_path, manifest)
            log(f"{run_id}: INVALID (hybrid probe failed) — {exc}")
            return manifest

        kwargs = dict(
            prompt=str(task["prompt"]),
            cwd=run_dir / "ws" / "repo",
            model=model,
            transcript_path=run_dir / "transcript.jsonl",
            max_turns=int(cfg["max_turns"]),
            timeout_s=int(cfg["run_timeout_s"]),
        )
        if arm == "B":
            kwargs["mcp_config"] = frag["mcp_config"]
        elif arm == "C":
            kwargs["add_dirs"] = [frag["notes_dir"]]

        res = claude_cli.run_claude(**kwargs)
        if res["status"] == "rate_limited" and rate_waits < MAX_RATE_LIMIT_WAITS:
            rate_waits += 1
            log(f"{run_id}: plan window exhausted — pausing "
                f"{cfg['rate_limit_wait_s']}s (wait {rate_waits}/{MAX_RATE_LIMIT_WAITS})")
            time.sleep(int(cfg["rate_limit_wait_s"]))
            continue
        if res["status"] in ("transient", "timeout") and transient_retries < MAX_TRANSIENT_RETRIES:
            transient_retries += 1
            log(f"{run_id}: {res['status']} failure — fresh retry "
                f"{transient_retries}/{MAX_TRANSIENT_RETRIES} in 60s")
            time.sleep(60)
            continue
        break

    (run_dir / "artifact.diff").write_text(
        workspace.collect_diff(run_dir / "ws" / "repo"), encoding="utf-8")
    (run_dir / "final_message.txt").write_text(
        res.get("result_text") or "", encoding="utf-8")
    manifest.update(
        status=res["status"], usage=res.get("usage"),
        total_cost_usd=res.get("total_cost_usd"), num_turns=res.get("num_turns"),
        duration_s=res.get("duration_s"), rc=res.get("rc"),
        rate_limit_waits=rate_waits, transient_retries=transient_retries,
        ended=_now(),
    )
    if res["status"] != "completed":
        manifest["stderr_tail"] = res.get("stderr_tail")
        manifest["error_subtype"] = res.get("error_subtype")
    _write(manifest_path, manifest)
    log(f"{run_id}: {manifest['status']} "
        f"({res.get('num_turns')} turns, {res.get('duration_s')}s)")
    return manifest


def _write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _cmd_out(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()
