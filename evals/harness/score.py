"""Score completed runs: rubric success, retrieval quality, judge verdicts.

Three scoring layers, strictly separated:
- mechanical rubric items (regex / file_exists / command) — code, no model;
- retrieval metrics — parsed from the run transcript, no model;
- llm rubric items — a pinned judge that sees ONLY task prompt, rubric, gold
  concept excerpts, and the artifact (diff + final message). Never the
  transcript, never the arm; arm-block text is scrubbed from the payload so a
  CLAUDE.md edit can't leak which arm produced it.
"""
import json
import re
import subprocess
import time
from pathlib import Path

from . import claude_cli, workspace

DIFF_CAP = 60_000
FINAL_CAP = 10_000
EXCERPT_CAP = 4_000

JUDGE_PROMPT = """You are a strict, impartial grader for a coding-agent evaluation.

Below: the TASK an agent was given, numbered RUBRIC items, REFERENCE EXCERPTS
(ground truth the rubric is based on), and the agent's ARTIFACT (a git diff of
everything it changed, plus its final message).

Grade each rubric item strictly against the artifact. Promises, plans, or
claims in the final message do not count unless the diff backs them. When
genuinely ambiguous, fail the item and say why.

Respond with ONLY a JSON object, no other text:
{{"<item id>": {{"pass": true/false, "reason": "<one sentence>"}}, ...}}

=== TASK ===
{task}

=== RUBRIC ===
{rubric}

=== REFERENCE EXCERPTS ===
{excerpts}

=== ARTIFACT: DIFF ===
{diff}

=== ARTIFACT: FINAL MESSAGE ===
{final}
"""


def score_run(cfg, task, run_dir, template_dir, use_judge=True, log=print):
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if manifest["status"] != "completed":
        score = {"run_id": manifest["run_id"], "scorable": False,
                 "status": manifest["status"]}
        _write(run_dir / "score.json", score)
        return score

    repo_dir = run_dir / "ws" / "repo"
    diff = (run_dir / "artifact.diff").read_text(encoding="utf-8")
    final = (run_dir / "final_message.txt").read_text(encoding="utf-8")

    items = {}
    llm_items = []
    for item in task["rubric"]:
        kind = item["kind"]
        if kind == "llm":
            llm_items.append(item)
            continue
        items[item["id"]] = {"kind": kind, "pass": _mechanical(item, repo_dir)}

    if llm_items and use_judge:
        verdicts = _judge(cfg, task, template_dir, diff, final, llm_items,
                          run_dir, log=log)
        if verdicts is None:
            # A judge failure must NEVER count as an agent failure: mark the
            # run unscored so reports exclude it and a later `score` pass
            # retries it (learned the hard way — an 80-run window-exhaustion
            # cascade briefly scored as mass rubric failures).
            score = {"run_id": manifest["run_id"], "task": task["id"],
                     "arm": manifest["arm"], "rep": manifest["rep"],
                     "scorable": False, "status": "judge_failed"}
            _write(run_dir / "score.json", score)
            return score
        for item in llm_items:
            v = verdicts.get(item["id"], {})
            items[item["id"]] = {"kind": "llm", "pass": bool(v.get("pass")),
                                 "reason": v.get("reason", "no verdict")}

    weights = {i["id"]: float(i.get("weight", 1)) for i in task["rubric"]}
    graded = {rid: r for rid, r in items.items()}
    total_w = sum(weights[rid] for rid in graded) or 1.0
    success = sum(weights[rid] for rid, r in graded.items() if r["pass"]) / total_w

    score = {
        "run_id": manifest["run_id"], "task": task["id"], "arm": manifest["arm"],
        "rep": manifest["rep"], "scorable": True,
        "success": round(success, 4), "items": items,
        "retrieval": retrieval_metrics(task, manifest, run_dir, template_dir),
        "tokens": _token_summary(manifest),
    }
    _write(run_dir / "score.json", score)
    return score


def _mechanical(item, repo_dir):
    kind = item["kind"]
    if kind == "file_exists":
        return (repo_dir / item["file"]).is_file()
    if kind == "regex":
        f = repo_dir / item["file"]
        if not f.is_file():
            return False
        return re.search(item["pattern"], f.read_text(encoding="utf-8", errors="replace"),
                         re.MULTILINE) is not None
    if kind == "command":
        try:
            r = subprocess.run(item["cmd"], shell=True, cwd=str(repo_dir),
                               capture_output=True, timeout=120)
            return r.returncode == 0
        except subprocess.TimeoutExpired:
            return False
    raise ValueError(f"unknown rubric kind: {kind}")


# ---------------------------------------------------------------- retrieval

def retrieval_metrics(task, manifest, run_dir, template_dir):
    """Which concepts did the agent actually consult, vs the human gold set?

    fetched = read in full (MCP `get` in B; Read of a note file in C).
    seen    = surfaced in search/grep results without necessarily reading.
    Also counts arm-B reads of canon files that bypass MCP (reported, not
    prevented — bypassing the product is itself a finding).
    """
    arm = manifest["arm"]
    gold = set(task["gold_concepts"])
    adjacent = gold | _neighbors(template_dir, gold)
    events = _tool_events(run_dir / "transcript.jsonl")

    fetched, seen, searches = set(), set(), 0
    canon_bypass_reads = 0
    ws = manifest.get("ws", "")

    for name, inp, result_text in events:
        blob = json.dumps(inp)
        if arm == "B":
            if name == "mcp__canonia__get":
                fetched.add(str(inp.get("id", "")))
            elif name == "mcp__canonia__search":
                searches += 1
                seen.update(_ids_from_search_json(result_text))
            elif ws and (ws + "/canon") in blob:
                canon_bypass_reads += 1
        elif arm == "C":
            notes = manifest.get("notes_dir", "")
            if name == "Read" and str(inp.get("file_path", "")).startswith(notes):
                fetched.add(Path(inp["file_path"]).stem)
            elif name in ("Grep", "Glob") and notes and notes in blob:
                searches += 1
                seen.update(_ids_from_paths(result_text, notes))
            elif name == "Bash" and notes and notes in blob:
                searches += 1
                seen.update(_ids_from_paths(result_text or "", notes))

    fetched.discard("")
    out = {"n_gold": len(gold), "n_fetched": len(fetched), "n_searches": searches,
           "fetched": sorted(fetched), "gold_fetched": sorted(gold & fetched),
           "recall_fetched": round(len(gold & fetched) / len(gold), 4) if gold else None,
           "recall_seen": round(len(gold & (fetched | seen)) / len(gold), 4) if gold else None,
           "precision_fetched": round(len(fetched & adjacent) / len(fetched), 4) if fetched else None}
    if arm == "B":
        out["canon_bypass_reads"] = canon_bypass_reads
    return out


def _tool_events(transcript_path):
    """Yield (tool_name, input, result_text) triples from a stream-json transcript."""
    pending = {}  # tool_use id -> (name, input)
    events = []
    if not Path(transcript_path).is_file():
        return events
    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    pending[block.get("id")] = (block.get("name", ""),
                                                block.get("input") or {})
                elif block.get("type") == "tool_result":
                    name, inp = pending.pop(block.get("tool_use_id"), ("", {}))
                    events.append((name, inp, _result_text(block)))
    # tool_use without a captured result (e.g. final-turn cut-off)
    for name, inp in pending.values():
        events.append((name, inp, ""))
    return events


def _result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
    return ""


def _ids_from_search_json(text):
    try:
        data = json.loads(text)
        return {r["id"] for r in data.get("results", []) if isinstance(r, dict) and "id" in r}
    except (json.JSONDecodeError, AttributeError):
        return set(re.findall(r'"id":\s*"([a-z0-9-]+)"', text or ""))


def _ids_from_paths(text, notes_dir):
    ids = set()
    for m in re.finditer(re.escape(notes_dir) + r"/(?:[a-z-]+/)?([a-z0-9-]+)\.md", text or ""):
        ids.add(m.group(1))
    return ids


def _neighbors(template_dir, gold):
    """Gold-adjacent ids: one hop along `references:` in either direction."""
    concepts_dir = Path(template_dir) / "canon" / "concepts"
    refs, rev = {}, {}
    for f in concepts_dir.glob("*/*.md"):
        cid = f.stem
        body = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^references:\n((?:- .*\n)*)", body, re.MULTILINE)
        out = set(re.findall(r"^- (.+)$", m.group(1), re.MULTILINE)) if m else set()
        refs[cid] = out
        for r in out:
            rev.setdefault(r, set()).add(cid)
    adj = set()
    for g in gold:
        adj |= refs.get(g, set()) | rev.get(g, set())
    return adj


# -------------------------------------------------------------------- judge

def _judge(cfg, task, template_dir, diff, final, llm_items, run_dir, log=print):
    rubric = "\n".join(f'{i["id"]}: {i["text"]}' for i in llm_items)
    excerpts = []
    for cid in task["gold_concepts"]:
        hits = list((Path(template_dir) / "canon" / "concepts").glob(f"*/{cid}.md"))
        if hits:
            excerpts.append(f"--- {cid} ---\n" +
                            hits[0].read_text(encoding="utf-8")[:EXCERPT_CAP])
    prompt = JUDGE_PROMPT.format(
        task=str(task["prompt"]),
        rubric=rubric,
        excerpts="\n\n".join(excerpts),
        diff=_blind(diff, cfg)[:DIFF_CAP],
        final=_blind(final, cfg)[:FINAL_CAP],
    )
    attempt = content_retries = rate_waits = transient_retries = 0
    while True:
        attempt += 1
        res = claude_cli.run_claude(
            prompt=prompt, cwd=run_dir, model=cfg["models"]["judge"],
            transcript_path=run_dir / f"judge-{attempt}.json",
            max_turns=1, timeout_s=300, output_format="json")
        if res["status"] == "rate_limited":
            rate_waits += 1
            if rate_waits > 18:
                return None
            log(f"{run_dir.name}: judge hit the plan window — pausing "
                f"{cfg['rate_limit_wait_s']}s (wait {rate_waits}/18)")
            time.sleep(int(cfg["rate_limit_wait_s"]))
            continue
        if res["status"] in ("transient", "timeout"):
            transient_retries += 1
            if transient_retries > 3:
                return None
            time.sleep(60)
            continue
        if res["status"] != "completed":
            log(f"{run_dir.name}: judge call failed ({res['status']})")
            return None
        verdicts = _parse_verdicts(res.get("result_text", ""))
        if verdicts is not None:
            return verdicts
        content_retries += 1
        log(f"{run_dir.name}: judge returned unparseable verdicts "
            f"(content retry {content_retries}/2)")
        if content_retries >= 2:
            return None


def _parse_verdicts(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _blind(text, cfg):
    """Strip arm-identifying instruction blocks from judge input."""
    for arm in ("B", "C"):
        for line in workspace.arm_block(arm, cfg).strip().splitlines():
            if line.strip():
                text = text.replace(line, "[project instructions]")
    return text


def _token_summary(manifest):
    u = manifest.get("usage") or {}
    return {
        "output_tokens": u.get("output_tokens"),
        "input_tokens_total": sum(u.get(k) or 0 for k in
                                  ("input_tokens", "cache_creation_input_tokens",
                                   "cache_read_input_tokens")),
        "num_turns": manifest.get("num_turns"),
        "duration_s": manifest.get("duration_s"),
        "cost_usd": manifest.get("total_cost_usd"),
    }


def _write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2), encoding="utf-8")
