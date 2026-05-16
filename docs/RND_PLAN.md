# Figure-Quality R&D Plan

Addresses three problem classes surfaced by the 2026-05-17 stress test
(78 prompts across geometry, graph theory, automata, formula-rich,
dense, regression/SVM/RBF curves, 3D):

1. **Dense-figure overlap** — captions land on diagrams, formula
   columns collide.
2. **Oversized / irrelevant elements** — e.g. an SVM figure whose
   class blobs fill the whole canvas; stray coordinate axes drawn on a
   pure-algebra derivation.
3. **"3D" prompts** render as stylised 2.5-D side views, not true 3D.

## Guiding principle

The project already paid for the lesson that neural layout loses to
mature tools (the LayoutDM / GNN negative result). The pattern that
works: **the LLM decides _what_; deterministic engines decide _where_
and do the rendering.** Two of the three problems are solved by
extending routing to more mature renderers; the third needs a layout
contract change.

## Tracks

### Track D — audit hardening  (cheap, first)
Now that the vision audit rasterises with headless Chrome (sees real
pixels), add explicit checklist items to the reviewer prompt: element
*relevance* ("does this element belong?") and *sizing* ("does any
element dominate the canvas?"). Add a deterministic guard that flags
any single primitive covering >40 % of the viewBox.
Effort: ~2 days. Risk: low.

### Track E — permanent regression benchmark
Promote the throwaway 78-prompt stress harness into a checked-in
benchmark (`scripts/figure_benchmark.py`) with automated checks (XML
validity, OOB, LaTeX leak, highlight-id resolution) and a route
breakdown. Gives every later change a measurable before/after.
Effort: ~3 days. Risk: low.

### Track A — matplotlib render route  (highest leverage)
Mirror the Graphviz route with a **matplotlib route**. Graphviz owns
graph-shaped figures; matplotlib owns *plot*-shaped ones. The LLM
emits a structured plot spec (closed-vocabulary: 2-D plots, classified
scatter with a decision boundary, 3-D surfaces, contour plots) — never
executable code — and a deterministic backend renders SVG via
matplotlib (`plot`, `scatter`, `contourf`, `quiver`, `plot_surface`,
`Axes3D`).

Solves most of the curve prompts (regression, logistic, SVM margins,
decision boundaries, ROC, Gaussians) **and** all the "3D" prompts
(real projected surfaces, saddle points, gradient descent on a
surface, contour descent, vector fields). Axes-bounded plotting also
kills the oversized-blob class.
Effort: ~1–2 weeks. Risk: low (mature library; no code-exec — spec
only).

### Track B — plot/diagram templates
For the most common recurring plot classes, add deterministic
templates (like the matrix / Pythagoras family). Largely subsumed by
Track A's closed-vocabulary named forms — B is the instant fast-path,
A is the general renderer.
Effort: ongoing.

### Track C — structured-scene layout  (largest)
What remains after A+B: dense *non-plot* figures — derivations,
proofs, reductions — where captions overlap diagrams and columns
collide. Change the contract: the LLM emits **typed regions** (title /
diagram / caption-band / formula-column / legend) with intrinsic sizes
but no absolute coordinates; a deterministic region packer (extending
the CP-SAT planner) assigns positions. Captions land in margins by
construction; columns cannot collide. Canvas auto-grows when content
does not fit.
Effort: ~3–4 weeks. Risk: medium (an "express v2" pipeline change).

## Sequence

**D → E → A → B → C.** D and E are cheap and make everything after
measurable. A is the biggest single quality jump. C is the largest
investment and is only worth doing once A+B have absorbed the
plot/graph prompts, leaving a smaller, well-defined set of
dense-composition figures.

## Status (2026-05-17)

- **Track D — DONE.** Reviewer prompt flags oversized/irrelevant
  elements; deterministic `oversized_element` structural check.
- **Track E — DONE.** `scripts/figure_benchmark.py` — 26-prompt
  benchmark, non-zero exit on any hard defect.
- **Track A — DONE.** `studio/templates/matplotlib_route.py` — a 4th
  render route (plot2d / scatter / surface3d / contour) wired into
  `express_figure`. matplotlib added as a dependency.
- **Track B — DONE.** Named curve/surface forms folded into Track A.
- **Track C — PARTIAL.** `autogrow_viewbox` landed (canvas grows to
  fit overflowing content). The full typed-region packer is
  deliberately DEFERRED — it is an express-v2 pipeline change too
  large to land without supervision. That remains the open item.

Benchmark after all tracks: 26/26 prompts clean (0 hard defects,
0 soft warnings). Deployed to khayyammath.com.

### Open future work
- Track C full structured-scene region packer (typed regions → CP-SAT
  packer; captions to margins by construction).
- Wire the Phase-E layout-quality scorer into `figure_benchmark.py`
  as a graded metric alongside the binary defect checks.
