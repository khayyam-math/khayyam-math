---
name: Figure-quality R&D plan (Tracks D/E/A/B/C)
description: Five-track plan to fix dense-figure overlap, oversized/irrelevant elements, and weak 3D. Source of truth is docs/RND_PLAN.md in the repo. User authorised autonomous implementation of the whole sequence on 2026-05-17.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
After the 2026-05-17 stress test, the user asked for an R&D plan to
fix three figure-quality problem classes and then authorised
implementing the whole sequence autonomously ("I am going to bed.
Make decisions and go ahead").

**Plan doc:** `docs/RND_PLAN.md` (committed) is the source of truth.

**Three problems:** dense-figure caption/column overlap; oversized or
irrelevant elements; "3D" prompts rendering as 2.5-D.

**Guiding principle:** LLM decides *what*, deterministic engines decide
*where* / render — never train a neural layout model (that negative
result is already in the paper).

**Tracks, in sequence D → E → A → B → C:**
- D — audit hardening: relevance + sizing checklist in the reviewer
  prompt; deterministic guard for any primitive >40% of viewBox.
- E — permanent regression benchmark: `scripts/figure_benchmark.py`.
- A — matplotlib render route (highest leverage): a 4th route beside
  template/Graphviz/LLM-SVG. LLM emits a closed-vocabulary plot SPEC
  (never executable code — security), deterministic matplotlib
  backend renders SVG. Fixes curves + real 3D + oversized blobs.
- B — plot templates: instant fast-path, largely subsumed by A.
- C — structured-scene layout (largest): LLM emits typed regions, a
  deterministic packer assigns coordinates; captions to margins by
  construction; canvas auto-grows. An "express v2" change.

**Why matplotlib spec not code:** executing LLM-emitted Python is an
RCE vector. The route accepts only a structured JSON spec; the
backend computes points with numpy from named forms / explicit data.

**Status 2026-05-17 (all implemented, tested, deployed):**
- D, E, A, B — DONE. Commits 43465de (D+E), 3117069 (A+B).
- C — PARTIAL: autogrow_viewbox landed (276be65); the full
  typed-region packer is DEFERRED (express-v2 change, too large to
  land unsupervised).
- matplotlib is now a dependency. The matplotlib route
  (studio/templates/matplotlib_route.py) is the 4th render route.
- scripts/figure_benchmark.py is the regression benchmark; after all
  tracks it scored 26/26 prompts clean (0 hard defects).
- Open future work: full Track C region packer; wire the Phase-E
  layout-quality scorer into the benchmark as a graded metric.

Resume by reading docs/RND_PLAN.md and checking git history.
