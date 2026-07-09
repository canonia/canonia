# Retrieval/task eval — does a canon over MCP beat "CLAUDE.md + grep"?

The July 2026 audit ([second pass](../docs/audit-2026-07-pass2.md)) concluded
that canonia's read side is RAG and must be **measured, not assumed** — the
competitive risk is not an incumbent product but *"CLAUDE.md + grep is good
enough."* This eval is the artifact that settles it. If the canon does not
beat the baselines, that negative result is the deliverable, reported with
the same care.

**Premise under test:** coding agents do better work with a canonical
knowledge graph over MCP than without one.

## Arms

| Arm | Knowledge available | Access path |
|---|---|---|
| **A — floor** | none | task repo only |
| **B — canonia** | canon copy, semantic index pre-built | `canonia serve` over MCP, hybrid search |
| **C — grep baseline** | the same concept files, verbatim | a plain `../notes/` folder; no MCP, no ids, no search tools |

**C is the real baseline**: the knowledge exists as greppable markdown — the
counterfactual world where you never adopted canonia. A is a floor control
separating "the knowledge helps" from "canonia the system helps". B earns its
keep only by beating C.

Parity rules: every arm gets the same curated task CLAUDE.md; B and C each get
an equivalent-strength appended pointer ("source of truth, consult first" —
B's is the instruction block from [using-with-agents.md](../docs/using-with-agents.md),
C's names the notes folder); A gets no pointer (nothing to point to). Arm C
files keep their frontmatter/ids — conservative against our own hypothesis.
Arm-B agents *could* bypass MCP and grep the canon directly; the harness
detects and reports that rather than pretending it can't happen.

## Task set

15 fixed tasks in three clusters — infra/ops (6), process (4), lore (5) —
drawn from the maintainer's real repos, pinned to exact commits. Each task's
needed knowledge lives in full-bodied canon concepts and is **not derivable
from the task snapshot** (local doc copies are scrubbed — simulating the
post-adoption world — and every task records grep evidence plus any residual
by-example leakage in a `leakage_note`). Prompts never hint that external
knowledge exists; the arm blocks do that.

Each task ships with a human-approved **gold concept set** and a **rubric of
5–10 binary checks** derived from the gold bodies — mechanical where possible
(file exists / regex / command), judged otherwise. Prompts, rubrics, and gold
sets reference private canon content, so the task pack lives outside this
repo; only sanitized titles and aggregate numbers appear here.

## Metrics (per run)

1. **Task success** — weighted fraction of rubric checks passed. Mechanical
   checks scored by code; judged checks by a pinned LLM judge that sees ONLY
   the task, rubric, gold excerpts, and the artifact (diff + final message) —
   blind to arm, transcript withheld, arm-identifying text scrubbed.
2. **Cost** — output tokens, total input tokens, turns, wall time (and USD as
   reported, informational — runs bill a subscription).
3. **Retrieval quality** — mechanical, from the transcript: concepts *fetched*
   (MCP `get` in B / note file `Read` in C) and *seen* (search/grep results).
   Recall vs the gold set; precision vs gold ∪ one reference-hop.

## Runner

Real end-to-end `claude -p` runs — no mocked model calls anywhere. Per run:
a fresh workspace with its own task-repo snapshot and its own canon/notes
copy (nothing writable is ever shared); pinned model; explicit
`--allowedTools` grant; web tools disabled; `--strict-mcp-config` so B gets
exactly one MCP server and A/C get none. Runs are manifest-tracked and
resumable; the fleet paces itself across subscription rate-limit windows.

**Audit guards honored:**
- **P2-C5 (availability latch):** `canonia index build` runs before any
  server start, and every arm-B run copy is probed over real MCP stdio —
  the run is invalid unless the probe returns `"mode": "hybrid"`. A failed
  probe never silently downgrades to keyword-only.
- **Measured as-is:** canonia is pinned at the audit-snapshot commit; nothing
  on the retrieval path (keyword scorer, tool descriptions) was touched
  before measurement.
- Preflight refuses to run with `ANTHROPIC_API_KEY` set (headless runs must
  bill the subscription, not a pay-per-token account) or with a dirty
  `src/` tree.

## Analysis

Per-task arm means (3 reps), paired comparisons (B−C, C−A, B−A) with
win/tie/loss counts and seeded bootstrap 95% CIs over tasks, broken out per
cluster. Primary fleet on a pinned Sonnet model; a small spot-check on a
stronger model verifies the direction holds where grep skills are strongest.

## Running it

```bash
.venv/bin/python -m evals.harness --pack <private-pack> validate
.venv/bin/python -m evals.harness --pack <private-pack> template   # index build + hybrid probe
.venv/bin/python -m evals.harness --pack <private-pack> run [--tasks T01,T02 --reps 1]
.venv/bin/python -m evals.harness --pack <private-pack> score
.venv/bin/python -m evals.harness --pack <private-pack> report
```

## Results

**Pending — the fleet has not run yet.** This section will carry the aggregate
tables (per-arm success, paired comparisons, per-cluster breakdown, retrieval
quality) and the honest interpretation, whichever way it lands.
