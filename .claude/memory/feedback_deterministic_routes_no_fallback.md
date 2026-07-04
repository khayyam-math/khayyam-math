---
name: Deterministic routes must never fall back to LLM-drawn SVG
description: When a per-domain template (homomorphism / Pythagoras / Plotly / etc.) can't produce a valid output, render a canonical example via the SAME deterministic engine — never let the pipeline fall through to free-hand LLM SVG, which produces the floating-edge / off-centre-node class of regressions.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The free-hand LLM SVG path puts edge endpoints near — but not on —
node centres, which the user has called out repeatedly (most
recently 2026-05-20: "the ends of the edges should have the
coordinates of the centers of the nodes — investigate why this is
forgotten and permanently solve it").

`snap_edges_to_nodes` exists as a post-processor for the LLM-drawn
path, but it can only fix edges drawn as `<line>` or simple
`M…L…$` two-point paths.  Multi-segment paths, `<polyline>`,
and edges drawn between unrelated coordinates aren't reliably
caught.  The robust fix is upstream: keep the figure on a
deterministic engine (Graphviz, matplotlib, SymPy-rendered) end to
end.

**Why:** the floating-edge regression keeps re-appearing whenever a
deterministic route hits an edge case and silently falls back.  In
the homomorphism case, vague prompts like "explain graph
homomorphism visually" made the LLM invent invalid mappings
(non-bipartite G into K_2 — chromatically impossible), the
verifier rejected 3 attempts, the template returned None, and the
pipeline fell through to LLM-drawn SVG.

**How to apply:** every per-domain template that emits
`Optional[tuple[str, list[dict]]]` should keep a `_CANONICAL_SPEC`
constant — a guaranteed-valid example for its domain — and render
that via the deterministic engine instead of returning None.  The
LLM-drawn fallback path stays for prompts that no template
matches; templates that DO match must never hand off back to it.

**Concrete pattern (graph_homomorphism.py is the reference):**
1. Define `_CANONICAL_SPEC` with hand-verified content.
2. In the public generate function, if every LLM attempt fails,
   `return _render(_CANONICAL_SPEC)` rather than `return None`.
3. Strengthen the LLM system prompt with the domain rule that
   prevents the most common invalid output (here: chromatic
   constraint `chi(G) <= chi(H)`).

**CDK deploy gotcha noted same session:** when only Python files
change inside the Docker build context, CDK's DockerImageAsset
hash sometimes computes to the same tag as the deployed image
even with new content.  The image WAS rebuilt and pushed to ECR
under that tag, but CloudFormation sees the task def is unchanged
and won't trigger an ECS deployment.  Force one with
`aws ecs update-service --force-new-deployment` after the cdk
deploy reports "no changes".  Verify the running image has the
fix with `docker run … grep -c <marker> /app/path/file`.
