"""CLI for the canonia retrieval/task eval harness.

Usage (from the canonia repo root, with the project venv's python):

    .venv/bin/python -m evals.harness validate --pack ../canon-evals
    .venv/bin/python -m evals.harness template --pack ../canon-evals
    .venv/bin/python -m evals.harness run      --pack ../canon-evals [--tasks T01,T02]
                                               [--arms A,B,C] [--reps 3] [--model ...]
                                               [--include-drafts]
    .venv/bin/python -m evals.harness score    --pack ../canon-evals [--no-judge]
    .venv/bin/python -m evals.harness report   --pack ../canon-evals

The pack (tasks, results, transcripts) is private and lives outside this repo;
only aggregate numbers cross back in, by hand, after review.
"""
import argparse
import json
import sys
from pathlib import Path

from . import report as report_mod
from . import runner, score as score_mod, tasks as tasks_mod, workspace


def main(argv=None):
    ap = argparse.ArgumentParser(prog="evals.harness")
    ap.add_argument("--pack", required=True, help="path to the private task pack")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="validate pack config + all task files")
    tp = sub.add_parser("template", help="build canon template + hybrid probe")
    tp.add_argument("--force", action="store_true")

    rp = sub.add_parser("run", help="run the matrix (resumable)")
    rp.add_argument("--tasks", help="comma-separated task ids (default: all approved)")
    rp.add_argument("--arms", default="A,B,C")
    rp.add_argument("--reps", type=int)
    rp.add_argument("--model", help="override config models.fleet")
    rp.add_argument("--include-drafts", action="store_true")
    rp.add_argument("--no-resume", action="store_true")

    sp = sub.add_parser("score", help="score completed runs")
    sp.add_argument("--no-judge", action="store_true")
    sp.add_argument("--rescore", action="store_true")

    sub.add_parser("report", help="aggregate scores into report.md")
    sub.add_parser("status", help="one line per run manifest")

    args = ap.parse_args(argv)
    cfg = tasks_mod.load_config(args.pack)
    runs_root = Path(cfg["pack_dir"]) / "results" / "runs"

    if args.cmd == "validate":
        return _validate(cfg)
    if args.cmd == "template":
        template = workspace.build_canon_template(cfg, force=args.force)
        print(f"template ok (hybrid): {template}")
        return 0

    all_tasks = tasks_mod.load_tasks(
        cfg["pack_dir"],
        include_drafts=getattr(args, "include_drafts", True),
        only=args.tasks.split(",") if getattr(args, "tasks", None) else None,
    )

    if args.cmd == "run":
        if not all_tasks:
            print("no matching tasks (drafts need --include-drafts)", file=sys.stderr)
            return 1
        arms = [a.strip().upper() for a in args.arms.split(",")]
        bad = [a for a in arms if a not in tasks_mod.ARMS]
        if bad:
            print(f"unknown arms: {bad}", file=sys.stderr)
            return 1
        manifests = runner.run_matrix(
            cfg, all_tasks, arms,
            reps=args.reps or int(cfg["reps"]),
            model=args.model or cfg["models"]["fleet"],
            resume=not args.no_resume,
        )
        bad = [m["run_id"] for m in manifests if m["status"] != "completed"]
        if bad:
            print(f"non-completed runs: {bad}", file=sys.stderr)
        return 0

    if args.cmd == "score":
        template = workspace.build_canon_template(cfg)
        by_id = {t["id"]: t for t in tasks_mod.load_tasks(cfg["pack_dir"],
                                                          include_drafts=True)}
        n = 0
        for mf in sorted(runs_root.glob("*/manifest.json")):
            run_dir = mf.parent
            if (run_dir / "score.json").is_file() and not args.rescore:
                continue
            manifest = json.loads(mf.read_text())
            task = by_id.get(manifest["task"])
            if task is None:
                print(f"skip {run_dir.name}: unknown task {manifest['task']}")
                continue
            s = score_mod.score_run(cfg, task, run_dir, template,
                                    use_judge=not args.no_judge)
            n += 1
            if s.get("scorable"):
                print(f"{s['run_id']}: success={s['success']} "
                      f"recall={s['retrieval']['recall_fetched']}")
            else:
                print(f"{s['run_id']}: not scorable ({s['status']})")
        print(f"scored {n} runs")
        return 0

    if args.cmd == "report":
        report_mod.build_report(cfg, all_tasks, runs_root,
                                Path(cfg["pack_dir"]) / "results")
        return 0

    if args.cmd == "status":
        for mf in sorted(runs_root.glob("*/manifest.json")):
            m = json.loads(mf.read_text())
            scored = (mf.parent / "score.json").is_file()
            print(f"{m['run_id']:<14} {m['status']:<13} "
                  f"turns={m.get('num_turns')} scored={scored}")
        return 0
    return 1


def _validate(cfg):
    all_tasks = tasks_mod.load_tasks(cfg["pack_dir"], include_drafts=True)
    concepts = Path(cfg["canon_repo"]) / "concepts"
    problems = []
    for t in all_tasks:
        problems += tasks_mod.validate_task(t, cfg["pack_dir"], concepts)
    for t in all_tasks:
        print(f"{t['id']} [{t.get('status')}] {t.get('cluster')}: {t.get('title')}")
    if problems:
        print(f"\n{len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"- {p}", file=sys.stderr)
        return 1
    print(f"\n{len(all_tasks)} task(s), all valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
