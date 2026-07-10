# Does it actually help? — the retrieval/task evaluation

Latency numbers ([performance.md](performance.md)) say the machinery is fast;
they don't prove agents do better *work* with a canon. So we measured that
too, the same way: real end-to-end runs, honest reporting, negative results
publishable. This page is the user-facing summary; method and full tables
live in [`evals/README.md`](../evals/README.md), and the harness is in the
repo (`evals/harness/`) so the experiment can be re-run.

## What was measured

135 real Claude Code agent runs (July 2026): 15 fixed, realistic coding-agent
tasks × 3 arms × 3 repetitions, tasks drawn from real repos against a real
153-concept canon. The arms:

- **A — no knowledge**: the task repo alone.
- **B — canonia**: the canon served over MCP, hybrid search, used exactly as
  [using-with-agents.md](using-with-agents.md) prescribes.
- **C — the honest baseline**: the *same* concept files as a plain folder of
  markdown the agent can grep and read. No server, no ids, no search.

C is the claim canonia has to beat: *"CLAUDE.md + grep is good enough."*
Every task's needed knowledge existed in the canon and **not** in the task
repo; success was scored against per-task rubrics (mechanical checks where
possible, otherwise a judge blind to which arm produced the work).

## What came out

| Arm | Task success | Median turns | Median wall | Median output tokens |
|---|---|---|---|---|
| A — no knowledge | 0.58 | 27 | 362 s | 28.2k |
| **B — canonia** | **0.89** | 45 | **358 s** | **28.5k** |
| C — same files, grep | 0.84 | 51 | 437 s | 36.2k |

Per-task paired comparison, B vs C: **+4.8 points mean success (95% CI
[+1.9, +8.6]), 4 wins, 11 ties, 0 losses** — canonia never did worse than
grep on any task, and delivered its results at **21% fewer output tokens,
18% less wall time, and 12% fewer turns**.

## What it means — when canonia helps, and when grep is enough

1. **Having the knowledge at all is the biggest win** (+26 points over no
   knowledge). If your conventions and reference facts live nowhere, fix
   that first — with canonia or with plain files.
2. **Canonia is the cheaper, more reliable way to serve it.** Grep gets
   close on quality by brute force — reading more, spending more tokens and
   time, with much lower precision (0.66 vs 0.78). Canonia's search+get
   targets the right concepts and stops. Same knowledge, −21% tokens,
   slightly better outcomes, never worse.
3. **Where the value concentrates** — measured, not asserted:
   - **Facts with no local exemplar** (our third cluster: writing that must
     stay consistent with an established reference canon): success 0.22
     without knowledge, 0.84 with canonia. The canon *is* the task.
   - **Multi-concept conventions** (process cluster): canonia's clearest
     quality edge over grep (0.86 vs 0.78) — search assembles the full set
     of relevant conventions where grep misses pieces.
   - **Mature, convention-rich repos**: all arms score high — an
     exemplar-rich repo teaches its own conventions by example, and three of
     six infra tasks saturated. If all your knowledge is one repo's local
     style, grep that repo; canonia earns its keep when knowledge crosses
     repo boundaries.
4. **Retrieval headroom**: canonia won while fetching only 77% of the
   human-judged relevant concepts — the known keyword-scorer limits (see
   [performance.md](performance.md)) are visible here, so the roadmapped
   retrieval fixes plausibly widen the gap.

## Limits of the experiment

One canon, one maintainer's repos and conventions, 15 tasks, one primary
model (a pinned Sonnet; a stronger-model spot-check is recorded in the full
results). Judged rubric items used a blind LLM judge with maintainer spot
checks. Task prompts, rubrics, and gold answers reference private content
and are not published; the harness, method, and aggregate numbers are.
Treat the numbers as strong evidence for the shape of the effect, not as a
universal constant.
