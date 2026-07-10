"""Aggregate scored runs into the results report.

Outputs (all inside the private pack's results/):
- report.md         full private report (task titles, per-task tables)
- aggregate.json    machine-readable aggregates
- public-summary.md ONLY aggregate numbers + sanitized task rows, suitable for
                    copying into the public evals/README.md after human review.

Paired stats: per-task arm means are compared pairwise (B-C, C-A, B-A) with a
sign summary and a bootstrap CI over tasks (seeded, reproducible).
"""
import json
import random
import statistics
from pathlib import Path

PAIRS = (("B", "C"), ("C", "A"), ("B", "A"))
BOOTSTRAP_N = 10_000
WIN_EPS = 0.05  # success-rate difference below this counts as a tie


def build_report(cfg, tasks, runs_root, out_dir, log=print):
    runs_root, out_dir = Path(runs_root), Path(out_dir)
    rows = []
    for mf in sorted(runs_root.glob("*/manifest.json")):
        manifest = json.loads(mf.read_text())
        score_path = mf.parent / "score.json"
        score = json.loads(score_path.read_text()) if score_path.is_file() else None
        rows.append({"manifest": manifest, "score": score})

    total = len(rows)
    scorable = [r for r in rows if r["score"] and r["score"].get("scorable")]
    excluded = {}
    for r in rows:
        if not (r["score"] and r["score"].get("scorable")):
            excluded[r["manifest"]["status"]] = excluded.get(r["manifest"]["status"], 0) + 1
    log(f"{total} runs, {len(scorable)} scorable, excluded by status: {excluded}")

    by_task_arm = {}
    for r in scorable:
        s, m = r["score"], r["manifest"]
        by_task_arm.setdefault((s["task"], s["arm"]), []).append(r)

    task_ids = sorted({t["id"] for t in tasks})
    task_meta = {t["id"]: t for t in tasks}
    arms = sorted({arm for (_, arm) in by_task_arm}) or ["A", "B", "C"]

    # task x arm cell = mean over reps
    cells = {}
    for (tid, arm), rr in by_task_arm.items():
        cells[(tid, arm)] = {
            "success": _mean([x["score"]["success"] for x in rr]),
            "out_tok": _median([_tok(x, "output_tokens") for x in rr]),
            "in_tok": _median([_tok(x, "input_tokens_total") for x in rr]),
            "turns": _median([_tok(x, "num_turns") for x in rr]),
            "wall_s": _median([_tok(x, "duration_s") for x in rr]),
            "recall": _mean([_ret(x, "recall_fetched") for x in rr]),
            "precision": _mean([_ret(x, "precision_fetched") for x in rr]),
            "reps": len(rr),
        }

    agg = {"n_runs": total, "excluded": excluded, "arms": {}, "pairs": {},
           "clusters": {}, "cells": {f"{t}|{a}": c for (t, a), c in cells.items()}}
    for arm in arms:
        vals = [cells[(t, arm)]["success"] for t in task_ids if (t, arm) in cells]
        agg["arms"][arm] = {
            "task_mean_success": _mean(vals), "n_tasks": len(vals),
            "median_out_tok": _median([cells[(t, arm)]["out_tok"] for t in task_ids
                                       if (t, arm) in cells]),
            "mean_recall": _mean([cells[(t, arm)]["recall"] for t in task_ids
                                  if (t, arm) in cells and cells[(t, arm)]["recall"] is not None]),
        }
    for a, b in PAIRS:
        diffs = [cells[(t, a)]["success"] - cells[(t, b)]["success"]
                 for t in task_ids if (t, a) in cells and (t, b) in cells]
        if diffs:
            agg["pairs"][f"{a}-{b}"] = {
                "n": len(diffs), "mean_diff": _mean(diffs),
                "ci95": _bootstrap_ci(diffs),
                "wins": sum(1 for d in diffs if d > WIN_EPS),
                "ties": sum(1 for d in diffs if abs(d) <= WIN_EPS),
                "losses": sum(1 for d in diffs if d < -WIN_EPS),
            }
    for cluster in sorted({t["cluster"] for t in tasks}):
        ct = [t["id"] for t in tasks if t["cluster"] == cluster]
        agg["clusters"][cluster] = {
            arm: _mean([cells[(t, arm)]["success"] for t in ct if (t, arm) in cells])
            for arm in arms}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "aggregate.json").write_text(json.dumps(agg, indent=2))
    (out_dir / "report.md").write_text(_render(agg, task_ids, task_meta, cells, arms,
                                               private=True))
    (out_dir / "public-summary.md").write_text(_render(agg, task_ids, task_meta, cells,
                                                       arms, private=False))
    log(f"wrote {out_dir}/report.md, aggregate.json, public-summary.md")
    return agg


def _render(agg, task_ids, task_meta, cells, arms, private):
    L = []
    L.append("# Retrieval/task eval — results" + ("" if private else " (public summary)"))
    L.append("")
    L.append(f"Runs: {agg['n_runs']} total; excluded by status: {agg['excluded']}")
    L.append("")
    L.append("## Arms (means over task-level means)")
    L.append("")
    L.append("| arm | task-mean success | median output tok | mean recall | tasks |")
    L.append("|---|---|---|---|---|")
    for arm in arms:
        a = agg["arms"][arm]
        L.append(f"| {arm} | {_f(a['task_mean_success'])} | {_f(a['median_out_tok'],0)} "
                 f"| {_f(a['mean_recall'])} | {a['n_tasks']} |")
    L.append("")
    L.append("## Paired comparisons (success, per-task)")
    L.append("")
    L.append("| pair | mean diff | 95% CI (bootstrap) | wins / ties / losses |")
    L.append("|---|---|---|---|")
    for pair, p in agg["pairs"].items():
        L.append(f"| {pair} | {_f(p['mean_diff'])} | [{_f(p['ci95'][0])}, {_f(p['ci95'][1])}] "
                 f"| {p['wins']} / {p['ties']} / {p['losses']} |")
    L.append("")
    L.append("## Per-cluster task-mean success")
    L.append("")
    L.append("| cluster | " + " | ".join(arms) + " |")
    L.append("|---|" + "---|" * len(arms))
    for cluster, vals in agg["clusters"].items():
        L.append(f"| {cluster} | " + " | ".join(_f(vals.get(a)) for a in arms) + " |")
    L.append("")
    L.append("## Per-task")
    L.append("")
    L.append("| task | cluster | " + " | ".join(f"{a} success" for a in arms) + " |")
    L.append("|---|---|" + "---|" * len(arms))
    for tid in task_ids:
        meta = task_meta.get(tid, {})
        title = meta.get("title", "") if private else meta.get("title", "")
        label = f"{tid} — {title}" if private else f"{tid} — {title}"
        row = [f"| {label} | {meta.get('cluster','?')} |"]
        for a in arms:
            c = cells.get((tid, a))
            row.append(f" {_f(c['success']) if c else '—'} |")
        L.append("".join(row))
    L.append("")
    if not private:
        L.append("_Task titles are the sanitized one-liners from the pack; prompts, "
                 "rubrics, and gold sets are private._")
    return "\n".join(L) + "\n"


def _bootstrap_ci(diffs, n=BOOTSTRAP_N, seed=0):
    rng = random.Random(seed)
    means = []
    k = len(diffs)
    for _ in range(n):
        sample = [diffs[rng.randrange(k)] for _ in range(k)]
        means.append(sum(sample) / k)
    means.sort()
    return [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)]


def _tok(row, key):
    return (row["score"].get("tokens") or {}).get(key)


def _ret(row, key):
    return (row["score"].get("retrieval") or {}).get(key)


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.mean(vals), 4) if vals else None


def _median(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def _f(v, nd=3):
    if v is None:
        return "—"
    return f"{v:.{nd}f}" if nd else f"{int(v)}"
