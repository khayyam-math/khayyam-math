---
name: Prefer mature tools over training new models
description: For layout / drawing / graphing problems, reach for Graphviz, CP-SAT, matplotlib, or TikZ before training a neural network. Decades of human-engineered heuristics dominate small custom models.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
For LAYOUT / DRAWING / DIAGRAM-SHAPE problems, the right first move
is almost always an established tool, not a freshly trained model.

**Why:** Demonstrated empirically in the 2026-05-14 session.
Trained a 6.9M-param LayoutDM diffusion model, a 6.3M-param GNN
delta-predictor, and a 1.8M-param quality scorer on ~25K real +
synthetic pairs from PROMPTS_V5. The diffusion + GNN never beat
no-op. The scorer-reranker did manage +2pp gpt-4o pass rate but
took a $$$ vision audit to validate. **Same session: adding a
Graphviz route (one keyword classifier + `dot -Tsvg`) moved graph-
shaped prompts from 30-90s with retries to <10s clean — a much
bigger win than every neural component combined.**

**How to apply:** When the user asks for help with a layout /
drawing / graph problem:
  1. First ask: does Graphviz, CP-SAT, matplotlib, TikZ,
     Cytoscape.js, or D3 already solve this? Most likely yes.
  2. If those don't fit, write a deterministic template with the
     domain math expressed directly (the matrix family lives in
     `studio/templates/matrix.py` — pure Python, no learning).
  3. Only after exhausting (1) and (2) consider neural — and only
     for what the structured tools genuinely can't do (e.g.
     freeform conceptual figures).
  4. When training neural, prefer SCORING/RE-RANKING over GENERATING
     positions: scoring is well-posed (single label per layout),
     generation is structurally ill-posed (many valid layouts per
     prompt).

**Tools we've validated for math-figure work:**
  - Graphviz (`dot`, `neato`, `fdp`, `circo`) — graph-shaped figures
  - CP-SAT (`studio/layout_planner.py`) — label placement with
    no-overlap + in-bounds + group cohesion constraints
  - Hand-coded Python templates (`studio/templates/matrix.py`,
    `studio/templates/graph.py`) — matrix ops, state diagrams
  - matplotlib — function plots (not yet integrated)
  - TikZ → SVG — math figures (not yet integrated, AutomaTikZ has
    the recipe)

**Don't:** start with "train a model on this" without first
asking what mature tool exists for the same shape of problem.
