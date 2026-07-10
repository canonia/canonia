# Retrieval/task eval — does a canon over MCP beat "CLAUDE.md + grep"?

Canonia's read side is RAG and must be **measured, not assumed** — the
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

15 fixed tasks in three knowledge clusters — infra/ops conventions (6),
engineering process conventions (4), and **narrative** (5: writing tasks for a
private creative project whose facts must stay consistent with an established
reference canon) — drawn from the maintainer's real repos, pinned to exact
commits. Each task's
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

**Measurement guards:**
- **Availability latch:** `canonia index build` runs before any
  server start, and every arm-B run copy is probed over real MCP stdio —
  the run is invalid unless the probe returns `"mode": "hybrid"`. A failed
  probe never silently downgrades to keyword-only.
- **Measured as-is:** canonia is pinned at the snapshot commit; nothing
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

## Results (fleet of 2026-07-09/10, 135 runs, all scorable)

Primary fleet: `claude-sonnet-5`, 15 tasks × 3 arms × 3 reps. Every arm-B
run's hybrid probe passed; agents used the MCP path faithfully (3 direct
canon-file reads across all 45 B runs, all detected and reported).

### Success and cost, by arm

| Arm | Task-mean success | Median turns | Median wall | Median output tok | Retrieval recall / precision |
|---|---|---|---|---|---|
| A — no knowledge | 0.578 | 27 | 362 s | 28.2k | — |
| **B — canonia over MCP** | **0.889** | 45 | **358 s** | **28.5k** | 0.77 / **0.78** |
| C — same files, grep | 0.841 | 51 | 437 s | 36.2k | **0.90** / 0.66 |

### Paired per-task comparisons (bootstrap 95% CI over tasks)

| Pair | Mean diff | 95% CI | Wins / ties / losses |
|---|---|---|---|
| **B − C** | **+0.048** | **[0.019, 0.086]** | **4 / 11 / 0** |
| C − A | +0.263 | [0.130, 0.405] | 10 / 5 / 0 |
| B − A | +0.311 | [0.169, 0.462] | 11 / 4 / 0 |

### Per-cluster task-mean success

| Cluster | A | B | C |
|---|---|---|---|
| infra/ops (mature exemplar-rich repo) | 0.859 | 0.945 | 0.914 |
| process conventions | 0.599 | 0.864 | 0.784 |
| narrative (no local exemplars) | 0.224 | 0.842 | 0.799 |

### Reading the numbers honestly

1. **The knowledge itself is the dominant factor** (C−A = +26 points). Where
   the needed facts have no local exemplar (narrative cluster), arm A scores
   0.22 — the canon *is* the task.
2. **Serving it over MCP beats grep on quality — modestly, reliably — and
   clearly on cost.** B−C is +4.8 points with a CI excluding zero and **zero
   losses in 15 tasks**, delivered at **21% fewer output tokens, 18% less
   wall time, and 12% fewer turns**. C compensates by reading more (higher
   recall, much lower precision) — it greps around and stuffs context until
   it works; B searches, fetches the right concepts, and stops. Against the
   baseline claim under test — "CLAUDE.md + grep is good enough" — the
   honest verdict: *grep gets close on quality, but it is the expensive,
   less-reliable way to be close.*
3. **The use-case boundary is visible.** In a mature, convention-rich repo
   (infra cluster) all arms score high — the repo teaches its own conventions
   by example, and three tasks saturate to ties. The canon's read-side value
   concentrates in **greenfield work, cross-repo knowledge, and facts without
   local exemplars** — exactly the product's intended use.
4. **Retrieval headroom is real.** B won while fetching only 77% of the gold
   concepts — the known keyword-scorer weaknesses are visible in the data,
   so retrieval improvements plausibly widen the gap. One task (T11) was
   hard for every arm (B 0.38 / C 0.33 / A 0.00).

### Per-task success (titles sanitized; prompts/rubrics are private)

| Task | Cluster | A | B | C |
|---|---|---|---|---|
| T01 — add a public web service end-to-end | infra/ops | 1.000 | 1.000 | 1.000 |
| T02 — add an automated, verified backup restore drill | infra/ops | 0.970 | 0.970 | 0.939 |
| T03 — scoped DB credentials for a new scheduled job | infra/ops | 0.818 | 0.818 | 0.818 |
| T04 — add CI to an ops repo | infra/ops | 0.792 | 0.917 | 0.792 |
| T05 — rebuild weekly maintenance + OS update/reboot policy | infra/ops | 0.633 | 0.967 | 0.933 |
| T06 — add a private, storage-heavy service | infra/ops | 0.939 | 1.000 | 1.000 |
| T07 — write a proper CLAUDE.md for a docs-only repo | process | 0.762 | 0.857 | 0.857 |
| T08 — prepare a personal repo to go public with contributors | process | 0.467 | 0.933 | 0.667 |
| T09 — record a freshly settled convention durably | process | 0.167 | 0.667 | 0.611 |
| T10 — design a small-model agent's tool surface | process | 1.000 | 1.000 | 1.000 |
| T11 — consistency pass over design docs vs canonical facts | narrative | 0.000 | 0.375 | 0.333 |
| T12 — write a planning brief whose facts must match canon | narrative | 0.143 | 1.000 | 0.905 |
| T13 — draft a framing document consistent with established canon | narrative | 0.292 | 1.000 | 0.958 |
| T14 — draft public-facing copy under strict factual constraints | narrative | 0.583 | 1.000 | 1.000 |
| T15 — write reference entries for an established subject area | narrative | 0.100 | 0.833 | 0.800 |

### Spot-check on a stronger model

A 5-task × 3-arm × 1-rep spot-check on a stronger model (the harder test for
B, since stronger models grep well): **A 0.37 / B 0.81 / C 0.81** mean
success — the quality edge over grep closes to a tie on this small sample
(n=5, single rep), while the efficiency edge persists (**B −18% output
tokens vs C**) and the knowledge effect stays dominant (B and C both ≈ +0.44
over A). The primary fleet on the same five tasks: A 0.30 / B 0.88 / C 0.78.
Reading: the stronger the model, the better it compensates for unstructured
access by reading more — canonia's **quality** edge concentrates on
cheaper/smaller models; its **cost** edge holds everywhere tested.

### Threats to validity

One canon (153 concepts), one maintainer's repos and conventions, n=15 tasks,
one primary model; judged rubric items use a blind LLM judge (spot-checked by
the maintainer); three tasks saturated (documented per-task by-example
leakage in a mature source repo); the task pack is private, so third parties
can reproduce the harness but not these exact numbers. The negative-result
commitment held: numbers above are reported as measured, including the ties.
