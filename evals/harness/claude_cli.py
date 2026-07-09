"""Spawn real headless `claude -p` runs and parse their stream-json output.

No mocked model calls anywhere: eval agents and the judge are real end-to-end
Claude Code processes. The environment is scrubbed of API-key variables so
every run bills the subscription plan, never a pay-per-token API account
(a set ANTHROPIC_API_KEY silently switches billing — the harness treats that
as a hard error at preflight AND strips it here, belt and braces).
"""
import json
import os
import re
import select
import subprocess
import time
from pathlib import Path

# Signatures of "the plan window is exhausted" — the runner pauses and retries
# instead of burning attempts. Kept deliberately broad; misclassification only
# costs one extra wait-and-retry.
RATE_LIMIT_RE = re.compile(
    r"usage limit|rate.?limit|out of extra usage|quota|too many requests|429|overloaded",
    re.IGNORECASE,
)

# Infrastructure hiccups worth an automatic fresh retry (observed in the
# pilot: "the socket connection was closed unexpectedly" after 48 good turns).
TRANSIENT_RE = re.compile(
    r"socket.*closed|ECONNRESET|ETIMEDOUT|fetch failed|network error|"
    r"connection (reset|refused|closed)|internal server error|\b5\d\d\b",
    re.IGNORECASE,
)

STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# Explicit tool grant instead of --dangerously-skip-permissions: everything a
# coding agent needs inside its isolated workspace, nothing interactive left
# to prompt for. mcp__canonia grants all tools of the arm-B server.
ALLOWED_TOOLS = ("Bash,Read,Write,Edit,MultiEdit,Grep,Glob,LS,"
                 "TodoWrite,Task,NotebookEdit,mcp__canonia")


def run_claude(prompt, cwd, model, transcript_path, mcp_config=None, add_dirs=(),
               max_turns=80, timeout_s=1800, output_format="stream-json"):
    """Run one headless agent; returns a result dict (never raises on run failure).

    status: completed | error | timeout | rate_limited
    """
    cmd = ["claude", "-p", prompt,
           "--model", model,
           "--output-format", output_format,
           "--max-turns", str(max_turns),
           "--allowedTools", ALLOWED_TOOLS,
           "--disallowedTools", "WebSearch,WebFetch",
           "--strict-mcp-config"]
    if output_format == "stream-json":
        cmd.append("--verbose")
    if mcp_config:
        cmd += ["--mcp-config", str(mcp_config)]
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]

    env = dict(os.environ)
    for var in STRIP_ENV:
        env.pop(var, None)

    transcript_path = Path(transcript_path)
    started = time.monotonic()
    deadline = started + timeout_s
    result_obj = None
    timed_out = False

    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with transcript_path.open("w", encoding="utf-8") as out:
            for line in _lines_until(proc, deadline):
                out.write(line)
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("type") == "result":
                    result_obj = obj
        if proc.poll() is None:
            proc.kill()
            timed_out = True
        stderr = proc.stderr.read() or ""
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    duration = round(time.monotonic() - started, 1)
    res = {"rc": proc.returncode, "duration_s": duration, "status": "error",
           "result_text": "", "usage": None, "total_cost_usd": None,
           "num_turns": None, "stderr_tail": stderr[-2000:]}

    if result_obj is not None:
        res.update(result_text=str(result_obj.get("result") or ""),
                   usage=result_obj.get("usage"),
                   total_cost_usd=result_obj.get("total_cost_usd"),
                   num_turns=result_obj.get("num_turns"))
        if result_obj.get("subtype") == "success" and not result_obj.get("is_error"):
            res["status"] = "completed"
        elif RATE_LIMIT_RE.search(res["result_text"]):
            res["status"] = "rate_limited"
        elif TRANSIENT_RE.search(res["result_text"]):
            res["status"] = "transient"
        else:
            res["status"] = "error"
            res["error_subtype"] = result_obj.get("subtype")
    elif timed_out:
        res["status"] = "timeout"
    elif RATE_LIMIT_RE.search(stderr):
        res["status"] = "rate_limited"
    return res


def _lines_until(proc, deadline):
    """Yield stdout lines until EOF or the wall-clock deadline."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 2.0))
        if not ready:
            if proc.poll() is not None:
                return
            continue
        line = proc.stdout.readline()
        if not line:
            return
        yield line
