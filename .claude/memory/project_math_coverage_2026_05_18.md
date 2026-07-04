---
name: 2026-05-18 — math coverage sweep (primary → PhD) + adversarial attack test
description: 48-prompt math coverage map and 40-prompt adversarial test; routing gaps found and fixed.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Two stress tests run 2026-05-18, both gpt-4o vision-judged:

**40-prompt adversarial attack** (7.60/10) — found systematic routing
bugs, all fixed + deployed:
- algorithm_trace hijacked "compare bubble/merge/quicksort" and
  "binary search tree" → added comparison-cue + BST guards.
- panels route missed "each of the four ..." → added keywords.
- merge sort added to algorithm_trace (bottom-up).
- graphviz viewBox padded ~4% (edge-label clipping).

**48-prompt math coverage** (7.40/10), primary→PhD:
- primary 6.1, middle 7.6, highschool 7.5, calculus 8.6,
  undergrad 6.8, phd 7.8.
- KEY: 38/48 fell through to the LLM-SVG catch-all. Deterministic
  routes only fired 10×.
- Fixed + deployed: function graphing (parabola/line/quadratic/
  slope) now routes to matplotlib (was LLM-SVG, rendered parabolas
  upside-down); new matplotlib "vectorfield" kind (quiver) for
  phase portraits / slope / direction fields, with a locked-down
  numpy expr evaluator; number_line no longer claims multi-digit
  regrouping.

**Gaps now CLOSED (deterministic templates built + deployed
2026-05-18):**
- `place_value(number)` and `multiplication_array(a,b)` in
  studio/templates/primary.py — wired into router.
- `venn_diagram(labels, regions)` in studio/templates/venn.py —
  2/3-set, hand-placed circles, all 7 regions — wired into router.
- matplotlib `sigma_bands` spec field — normal-distribution bell
  curve with 68-95-99.7 shaded bands.

PhD-tier abstract diagrams score HIGHER (7.8) than primary (6.1) —
abstraction forgives imprecision; a wrong parabola or miscounted
dot array is obviously broken.

**How to apply:** the deterministic-template library now covers
matrices, graphs (state/adjacency), algorithms, processes, tables,
geometry, primary arithmetic, Venn, and the plot/3D/vectorfield
families. Genuinely free-form concept diagrams remain LLM-SVG.
