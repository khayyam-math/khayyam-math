---
name: 2026-05-18 — 4-round figure-quality iteration + deterministic-route finding
description: Iterated 4+ rounds fixing the 5 named figure weaknesses; key finding is deterministic routes are stable, LLM routes have irreducible variance.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Ran an iterative improve→test→inspect loop (25 challenge prompts, 5 per
weakness category, gpt-4o vision-judged each round). Overall mean
trajectory: 7.20 → 8.52 → 8.20 → 8.56 → 8.48 / 10.

What shipped (commits on main, deployed to khayyammath.com 2026-05-18):
- arithmetic verifier (`_verify_arithmetic` in express.py) — flags wrong
  numeric claims into the structural critic
- `studio/templates/algorithm_trace.py` — NEW deterministic route:
  sorts / binary search / Gaussian elimination / determinant / long
  division traced in Python, rendered as stacked grids. Fires before
  the sequential route.
- matplotlib 3D `expr` surface form (eval z=f(X,Y) in locked-down ns) +
  sin/cos/uniform 2-D curve forms
- panels/sequential cells size to sub-figure aspect ratio
- graphviz SVGs keep explicit pixel width/height (was width="100%",
  caused edge clipping)

**KEY FINDING — do not re-litigate:** the deterministic routes
(algorithm_trace, matplotlib) scored 9.0-9.4 in EVERY round and never
regressed. The LLM-dependent routes (panels, sequential, raw LLM-SVG,
graphviz-DOT) average 7-9 but any single figure swings ±3-5 run to
run — same prompt scored 9,9,9,1,9 across rounds. A single 25-prompt
benchmark cannot distinguish a regression from an unlucky generation.

**Why:** figure *content* on LLM routes is LLM-generated, so it has
irreducible variance. "Zero weaknesses" is unreachable while those
routes exist.

**How to apply:** to genuinely eliminate a weakness, make its route
deterministic (a Python template/tracer) — that is the only fix that
stays fixed. Do NOT chase single-run benchmark dips on LLM routes;
re-run 2-3× before believing a regression.

Follow-up (same day): built the two remaining deterministic
candidates —
- `adjacency_matrix(vertices, edges, directed)` in graph.py, wired
  into the template router — graph→matrix rendered via data_table.
- `studio/templates/process_route.py` — "<X> cycle" / "scientific
  method" prompts: ring layout for cycles, vertical flow for linear
  processes. Fires before the sequential route.
Deployed to khayyammath.com 2026-05-18.
