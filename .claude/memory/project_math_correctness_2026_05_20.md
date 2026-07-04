---
name: 2026-05-20 — math-correctness architecture (Tiers 2/3/4 all shipped)
description: Solve-then-draw + SymPy verifier + graph-homomorphism deterministic check + math-first vision review — the full layered architecture for "math correctness comes first".
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The user's principle: **math correctness is non-negotiable.  Solve,
then draw.  A wrong-math figure is worse than no figure.**  Four
layers now enforce it, in order from cheapest to last-resort:

**Tier 1 — correct by construction.** Deterministic engines
(`symbolic_route.py` for calculus, `plotly_render.py` for graphs,
`matrix.py` / `graph_homomorphism.py` / templates).  The math here is
unfalsifiable.  In a 20-problem benchmark, **16/20 routed here.**

**Tier 2 — symbolic verifier.** The express LLM contract demands
`problem_statement`, `solution`, and `math_claims` alongside the
SVG.  `studio/templates/math_verifier.py` runs SymPy on each claim
(``identity`` or ``value``).  False claim → blocks the figure +
critique-retry.  In the benchmark, of the 4/20 prompts that hit the
LLM path, **all 4 claims verified**, one only after the verifier
caught two prior wrong attempts.

**Tier 3 — per-domain deterministic verifiers.** First instance:
`graph_homomorphism.py`.  LLM emits G, H, and f; O(|E_G|) check
confirms f preserves edges; clean Graphviz figure with vertices
colour-coded by image (no tangled arrows — fixes the original
complaint).  Future siblings: graph colouring, isomorphism,
bipartiteness, planarity — same pattern.

**Tier 4 — math-first vision review.** The reviewer prompt now
opens with "MATH CORRECTNESS COMES FIRST" with an explicit FAIL list,
and the reviewer receives the LLM's own stated solution + verified
claims as reference truth to cross-check the rendered figure.
Same single LLM call — no extra cost or latency.

**System prompt anchor**: the express system prompt now leads with
"MATH CORRECTNESS IS NON-NEGOTIABLE … Solve, then draw" and tells
the LLM that claims must be unconditional/concrete (instantiate a
specific case if the theorem has parameters).

**Tested**: 20-problem benchmark (mix of calc / algebra / geometry /
plots / linear algebra / graphs).  16/20 deterministic.  4/20 LLM
with verifier — all verified.  Homomorphism case fixed
(`/tmp/math_bench/homom_test.png` shows the colour-coded result).

**Bench artefacts** at `/tmp/math_bench/`: 20 screenshots + report.

**Remaining work**: more per-domain verifiers as use exposes them
(colouring, isomorphism, etc.); the existing pattern in
`graph_homomorphism.py` is the template.

**Commits**: Tier 2 = b6da13e (verifier + schema); Tier 3 = 7b144dd
(homomorphism); Tier 4 = following Tier 3 (math-first reviewer).
All on origin/main, live on khayyammath.com.
