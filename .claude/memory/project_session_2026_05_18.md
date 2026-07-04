---
name: 2026-05-18 — multi-component routes shipped (table / panels / sequential)
description: Three new express routes built and deployed to fix the multi-component figure failures from the 40/55-prompt vision-judge batches.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Shipped three new `express_figure` routes to address the multi-component
figure failures the vision judge surfaced (crossref 3.4, sequential 3.6,
multipanel 4.8 out of 10).

- **data_table template** (`studio/templates/table.py`, commit 0dba05d) —
  pixel-perfect grid for truth tables, Cayley/group tables, mod-n tables,
  Karnaugh maps. Registered in router.py `_DISPATCH` + `_ROUTER_SYSTEM`.
- **panels route** (`studio/templates/panels_route.py`, commit b018263) —
  "compare X and Y side by side" → LLM decomposes into 2-4 sub-prompts,
  each generated independently, composited into a deterministic grid of
  namespaced nested `<svg>` cells.
- **sequential route** (`studio/templates/sequential_route.py`, commit
  ad6e3b3) — "show X step by step" → ordered 3-6 steps, each a clean
  standalone figure, stacked vertically in bordered "Step N" cells.

Both panels/sequential sub-figures run with `max_retries=1` (parallel via
asyncio.gather, so one retry's latency total). Nested-panel `<svg>` has
`overflow="hidden"` so an oversized sub-figure is clipped to its cell.

**Why:** the LLM-SVG path crammed every step/panel into one canvas and
overlapped them; deterministic compositing makes layout correct by
construction.

**How to apply:** these routes fire before the single-figure routes in
`express_figure`, gated by `is_panels_prompt` / `is_sequential_prompt`
keyword checks and the `SEVIM_PANELS_ROUTE` / `SEVIM_SEQUENTIAL_ROUTE`
env flags. The route propagates whatever model `express_figure` is
called with into both decomposition and sub-figures — in production
that is gpt-4o (`SEVIM_FORCE_ACTIVE_MODEL=gpt-4o` in
`infra/sevim_stack.py:223`), NOT gpt-4o-mini. Always test with gpt-4o
to be production-representative. Routes fix layout, not content;
residual issues on gpt-4o: occasional arithmetic errors (vision-judge
flagged), some steps render as text lists, wasted top whitespace in
short sub-figures.

All 266 tests pass. Deployed to khayyammath.com (CDK, ECS Fargate)
2026-05-18. Pre-existing uncommitted diffs in `infra/cdk.context.json`
and `scripts/generate_reference_corpus.py` were left untouched (not this
session's work).
