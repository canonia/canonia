"""P2-C5 latch guard.

`canonia serve` probes semantic-search availability once and latches: a server
started before `canonia index build` never gains hybrid search until restart
(audit-2026-07-pass2.md, P2-C5). An eval that misses this silently measures
keyword-only while believing it measures hybrid.

So: before ANY arm-B run counts, this probe spawns a fresh `canonia serve` on
that run's own canon copy, issues a real `search` over the MCP stdio protocol
(newline-delimited JSON-RPC 2.0), and asserts the response says
``"mode": "hybrid"``. A failed probe marks the run invalid — never a silent
downgrade to keyword-only.
"""
import json
import select
import subprocess
import time


class ProbeError(Exception):
    pass


def probe_hybrid(canonia_bin, canon_dir, timeout_s=90):
    """Return {"ok": bool, "mode": str, ...}; raises ProbeError on protocol failure."""
    proc = subprocess.Popen(
        [str(canonia_bin), "serve", "--canon", str(canon_dir), "--no-autocommit"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "canonia-eval-probe", "version": "0"}}})
        _recv(proc, 1, timeout_s)
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        # First search triggers the (lazy) semantic-searcher build — this is
        # exactly the latch moment the guard exists to observe.
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "search",
                                "arguments": {"query": "availability probe", "limit": 1}}})
        resp = _recv(proc, 2, timeout_s)
        result = resp.get("result") or {}
        struct = result.get("structuredContent")
        if struct is None:  # fall back to the text content block
            try:
                struct = json.loads(result["content"][0]["text"])
            except Exception:
                raise ProbeError(f"unparseable search response: {resp!r}")
        mode = struct.get("mode", "keyword")
        return {"ok": mode == "hybrid", "mode": mode,
                "result_count": struct.get("count"),
                "unindexed": struct.get("unindexed", 0)}
    finally:
        proc.kill()
        proc.wait()


def _send(proc, obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def _recv(proc, want_id, timeout_s):
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError(f"timeout waiting for response id={want_id}")
        ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 1.0))
        if not ready:
            if proc.poll() is not None:
                raise ProbeError(f"server exited (rc={proc.returncode}) before id={want_id}")
            continue
        line = proc.stdout.readline()
        if not line:
            raise ProbeError(f"server closed stdout before id={want_id}")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue  # stray non-protocol output
        if msg.get("id") == want_id:
            if "error" in msg:
                raise ProbeError(f"rpc error for id={want_id}: {msg['error']}")
            return msg
