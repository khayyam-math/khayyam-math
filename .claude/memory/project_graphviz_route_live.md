---
name: 2026-05-14 LIVE — Graphviz route for graph-shaped figures
description: LLM emits DOT, dot -Tsvg renders. Live as ECS rev 91 on khayyammath.com. Sub-10s graph diagrams with deterministic layout.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
**Live on khayyammath.com (ECS rev 91, commit `2153181`).**

The graph-shaped subset of prompts now bypasses the LLM-SVG loop
entirely. Prompts matching the classifier (state machines, Turing
machines, DAGs, trees, Hasse diagrams, Cayley graphs, Petersen
graphs, flowcharts, control-flow graphs, ~30 keyword variants)
get routed to:
  1. gpt-4o-mini emits DOT source
  2. `dot -Tsvg` (or `circo` for circular layouts) renders
  3. SVG is post-processed to be responsive (`width="100%"`,
     `preserveAspectRatio="xMidYMid meet"`, no fixed pt size)
  4. Returned to the canvas iframe

**Files**:
  - `studio/templates/graphviz_route.py` — classifier, engine
    picker, DOT extractor, render, LLM-emit-DOT, end-to-end pipeline,
    SVG responsive post-processor.
  - `studio/express.py` lines 1377-1420 — fast-path BEFORE the
    template router and BEFORE the LLM-SVG loop. Gated by
    `SEVIM_GRAPHVIZ_ROUTE=on` (default on).
  - `service/canvas.py` — `Canvas.set_raw_svg()` counts
    `<g class="node">` / `<g class="edge">` in the SVG; new
    `raw_node_count` / `raw_edge_count` fields override the
    structured-graph counts that the viewer header reads.
  - `service/app.py` — `/canvas/{cid}/state` and the SSE stream
    use the overrides when set.
  - `Dockerfile` — apt-installs `graphviz` in the runtime layer.
  - `tests/test_graphviz_route.py` — 23 tests, all pass.

**Measured improvements** (Playwright audit, before vs after deploy):
  - Desktop chat snapshot: ✅ fits any width cleanly (was clipped).
  - Mobile chat snapshot: ✅ same.
  - Desktop live canvas: ✅ ~4-9 s render (was 30-90 s with retries).
  - Mobile live canvas: still horizontally slidable (per the
    explicit user requirement in feedback_canvas_must_be_slidable.md).
  - Header counter: ✅ shows actual node/edge count (was "0/0").
  - rankdir=LR in the system prompt → state machines / Turing
    machines are wide + short, much better fit for portrait mobile.

**Routing prompt examples that DO take the graphviz path**:
  "draw a DFA for L = (a|b)\* ending in ab"
  "show a Turing machine that decides L = {0^n 1^n}"
  "binary search tree for [5, 3, 8, 1, 4, 7, 9]"
  "Hasse diagram for the divisibility lattice on 12"
  "show the Petersen graph"
  "Cayley graph of D_4"

**Routing prompt examples that do NOT** (fall through to existing paths):
  "illustrate the Pythagorean theorem"     → LLM-SVG
  "matrix inverse of [[1,2],[3,4]]"        → matrix template router
  "graph y = sin(x) from 0 to 2π"          → LLM-SVG (function plot)
  "Venn diagram for A union B"             → LLM-SVG (no DOT keyword)

**Operational**:
  - Disable: `SEVIM_GRAPHVIZ_ROUTE=off` env var in ECS task def.
  - The `dot` binary in the runtime image is ~5 MB.
  - Per-request cost: 1 × gpt-4o-mini call (~$0.001) + local
    Graphviz render (~10 ms). No vision audit needed.
