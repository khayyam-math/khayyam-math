---
name: project_taxonomy_system
description: "Deterministic category→template taxonomy (answer cache, recognition, curation, renderer-first) — all 4 phases built"
metadata: 
  node_type: memory
  type: project
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-11: built the full category→template taxonomy from docs/TEMPLATE_TAXONOMY_PLAN.md, all 4 phases, tagged baseline [[Success3]]. Design splits "template" into **renderer** (parameterized program) vs **exemplar** (curated known-good figure, retrieved+adapted).

- **Phase 1 — answer cache.** `sevim/embeddings.py` (text-embedding-3-small via httpx, hash-cached, None without key). `canvas_index` table + `Telemetry.index_canvas/iter_canvas_index`. `studio/answer_cache.py` (numpy cosine over accepted canvases; lookup_figure fetches stored SVG). Wired into `express_figure` before the cascade. Indexes shipped canvases in app.py off-thread. `scripts/backfill_answer_cache.py`. Flag `SEVIM_ANSWER_CACHE` (default OFF), `SEVIM_ANSWER_CACHE_TAU` (0.93).
- **Phase 2 — taxonomy + recognition.** `categories`/`templates`/`template_examples` tables. `studio/taxonomy.py` (2-level: nearest category centroid → nearest template). `studio/taxonomy_seed.py` seeds ~9 categories from existing routes. Serves exemplar hits; renderer matches advisory (cascade stays authority). Flag `SEVIM_TAXONOMY` (default OFF).
- **Phase 3 — curation.** `taxonomy_candidates` table. `studio/curation.py`: find_gaps→cluster_gaps→propose→dedup_templates(cross-category)→suggest_migrations→promote(optional quality-gate on golden prompt). Admin: `/studio/admin/taxonomy` (JSON), `/admin/taxonomy/candidate` (approve/reject), `/admin/taxonomy/refresh`, `/admin/taxonomy/view` (HTML). `scripts/curate_taxonomy.py` cron job. NOT live mutation — offline + admin-approved.
- **Phase 4 — renderer-first.** `studio/templates/np_completeness.py`: deterministic renderer for "prove X is NP-complete" (in-NP + NP-hard reduction diagram + equivalence + QED), LLM extracts only content fields. Wired as `SEVIM_NPC_ROUTE` (default **ON** — this one ships active, verified clean/legible). Fixes the worst LLM-SVG class.

**2026-06-11 later — NOW LIVE (was dormant; user called it out).** First pass shipped phases 1-3 flag-OFF + unseeded = did nothing. Fixed: added the missing retrieve+IMPROVE (`polish_svg()` re-runs safe deterministic passes on the served SVG; `dedup_index_by_prompt` keeps newest = versioning); `@app.on_event('startup')` in service/app.py idempotently auto-seeds categories + loads the index in a bg thread (no manual prod-DB step — verified in prod: 10 categories / 22 templates / 69 examples seeded at boot). Set `SEVIM_ANSWER_CACHE=1`, `SEVIM_ANSWER_CACHE_TAU=0.93`, `SEVIM_TAXONOMY=1` in `infra/sevim_stack.py`. Confirmed live: same question twice → `answer-cache HIT cos=0.96`.

**Precision finding (important):** embedding sweep showed surface-similar but DIFFERENT math questions (vertex-cover vs 3-SAT; multiply vs transpose) hit ~0.70-0.78 cosine on text-embedding-3-small, overlapping genuine rewordings → broad paraphrase retrieval is UNSAFE (mis-serve risk). So TAU=0.93 = near-verbatim repeats only, zero cross-retrieval. Broader retrieval needs a reranker / subject-conditioned key (future work). Exemplar serving still admin-gated via curation. Paper sec:taxonomy + Fig 3 updated to 'live' + this finding; Fig 3 overlap fixed. See [[feedback_deterministic_routes_no_fallback]] and [[feedback_mature_tools_first]].
