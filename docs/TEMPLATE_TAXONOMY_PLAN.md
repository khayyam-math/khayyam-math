# Category → Template taxonomy with retrieval, growth, and dedup

Design proposal (2026-06-11). Status: proposal, not yet implemented.

## 1. The core reframing

"A deterministic template for everything" is the right north star, but
"template" means two different things, and conflating them is what makes
the problem feel impossible:

- **Renderer templates** — a *parameterized* program that draws the figure
  correct-by-construction from extracted args. `matrix_multiplication`,
  `unit_circle`, `graphviz DOT`, `fraction`. These work only when the
  question has a *fixed structure with a few variables*. ~27 exist today.

- **Exemplar templates** — a *curated known-good figure* for an open-ended
  question class that does NOT reduce to a few parameters ("prove vertex
  cover is NP-complete", "explain the spectral theorem"). The "template"
  is a stored SVG + narration + a layout schema; on a repeat/similar
  question we **retrieve it and lightly adapt**, instead of re-drawing
  from scratch.

Both deliver the two things you want — **determinism** (same input →
same figure) and **consistency** (the platform answers the same question
the same way). The taxonomy holds both kinds side by side.

This directly answers "if a question is asked before, retrieve the
answer, improve it slightly": that is the **exemplar** path. And "make a
new template when something new comes" is: try a renderer first; if the
class isn't parameterizable, promote the first good answer to an exemplar.

## 2. What we leverage (already in the repo)

- `sevim/telemetry.py`: `turns.user_prompt`, `canvases.svg`,
  `canvases.accepted`, `repairs` (prompt → bad/good SVG). This is the
  seed corpus — every accepted canvas is a candidate exemplar.
- `studio/templates/router.py`: LLM classifier + `_DISPATCH` of 19
  renderer templates; the extension pattern is known.
- `studio/express.py`: the 9-gate route cascade and LLM-SVG fallback.
- `studio/neural_layout/schema.py`: `MATH_BUCKETS` (20) + `archetype`
  (9) — a **ready-made seed taxonomy**. Categories ≈ curated subset of
  (bucket × archetype), e.g. `complexity/proof`, `linear_algebra/concept`.
- OpenAI SDK + key already wired (`khayyam_math/providers`). Embeddings
  (`text-embedding-3-small`, 1536-d, ~$0.00002/query) are a 3-line add.

What's missing and must be built: an embedding index, a persisted
taxonomy (categories/templates), a recognition layer in front of the
cascade, and an offline curation pipeline.

## 3. Architecture (four components)

```
                       ┌─────────────────────────────────────┐
   user prompt ─embed─▶ │  RECOGNITION (new, in front of       │
                        │  express_figure)                     │
                        │  1. answer-cache hit?  ──yes──▶ serve │
                        │  2. recognize category               │
                        │  3. pick template in category        │
                        │  4. else → existing cascade + log gap │
                        └─────────────────────────────────────┘
                                        │ logs
                                        ▼
   ┌──────────────┐   reads   ┌────────────────────┐   feeds   ┌──────────────┐
   │ TAXONOMY     │◀──────────│ EMBEDDING INDEX     │◀──────────│ CURATION     │
   │ categories   │           │ (numpy cosine over  │           │ (offline /   │
   │ templates    │──────────▶│  a few-hundred-row   │──────────▶│  admin):     │
   │ exemplars    │           │  catalog; no pgvector│           │ create/move/ │
   └──────────────┘           │  needed at this size)│           │ dedup/promote│
                              └────────────────────┘           └──────────────┘
```

### 3a. Taxonomy store (new Postgres tables)

```
categories(
  category_id   TEXT PK,         -- 'complexity.np_completeness'
  parent_id     TEXT NULL,       -- optional 2-level nesting
  title         TEXT,            -- 'NP-completeness proofs'
  centroid      JSON,            -- mean embedding of its templates (1536 floats)
  created_at, updated_at REAL
)

templates(
  template_id   TEXT PK,         -- 'np_complete_reduction'
  category_id   TEXT FK,
  kind          TEXT,            -- 'renderer' | 'exemplar'
  renderer_name TEXT NULL,       -- dispatch key when kind='renderer'
  exemplar_svg  TEXT NULL,       -- canonical SVG when kind='exemplar'
  exemplar_narration JSON NULL,
  layout_schema JSON NULL,       -- region/role spec for adaptation
  embedding     JSON,            -- centroid of its example prompts
  golden_prompt TEXT,            -- canonical prompt; used to re-test on change
  version       INTEGER,
  status        TEXT,            -- 'live' | 'candidate' | 'retired'
  created_at, updated_at REAL
)

template_examples(             -- many prompts map to one template
  template_id FK, prompt TEXT, embedding JSON, source_canvas_id TEXT
)
```

Vectors stored as JSON arrays. The catalog is small (tens→low hundreds of
templates, a few thousand example prompts); an in-process numpy cosine
scan is <2 ms and avoids the operational cost of pgvector. Move to
pgvector only if the example set exceeds ~100k rows.

### 3b. Embedding index

- `sevim/embeddings.py`: `embed(text) -> list[float]` via
  `text-embedding-3-small`, with an LRU + a persistent cache keyed by
  `sha256(text)` (so repeated prompts cost nothing).
- Load all template/category/example vectors into a numpy matrix at boot;
  refresh on curation changes. Cosine = normalized dot product.

### 3c. Recognition layer (the live path)

Inserted at the top of `express_figure`, before the existing cascade:

```
e = embed(prompt)

# (1) Answer cache — consistency for repeats
cand = nearest(e, accepted_exemplars)
if cand.cosine >= TAU_CACHE (~0.93):
    return refine_lightly(cand)          # same figure, optional polish

# (2) Category recognition
cat = nearest(e, category_centroids)
if cat.cosine < TAU_CAT (~0.78):
    log_gap(prompt, e, reason='no_category')   # → curation: maybe new category
    fall through to existing cascade + LLM-SVG

# (3) Template within the category
tpl = nearest(e, templates_in(cat), )
if tpl.cosine >= TAU_TPL (~0.85):
    if tpl.kind == 'renderer':
        args = extract_args(prompt, tpl)      # existing LLM-extract pattern
        return render(tpl.renderer_name, args)
    else:  # exemplar
        return adapt_exemplar(tpl, prompt)    # retrieve + light LLM edit
else:
    log_gap(prompt, e, cat, reason='no_template')  # → curation: new template in cat
    fall through to cascade + LLM-SVG
```

Key point: recognition **augments**, it does not replace, the current
cascade. Anything it can't confidently route still gets today's behavior,
and every fall-through is logged as a curation candidate. Risk stays
bounded; coverage grows monotonically.

`TAU_*` thresholds are tuned offline against the telemetry corpus (label
a few hundred prompts, pick thresholds that maximize precision; recognition
should be high-precision — when unsure, fall through, never mis-serve).

### 3d. Curation loop (offline / admin — NOT live mutation)

This is where "create a template", "create a category", "move a
template", "dedup" happen. Doing these *at request time* would be slow and
unsafe (matches the standing rule: self-correcting within bounds, never
autonomously self-modifying live). Instead, a periodic job + an admin
view under `/studio/admin/taxonomy`:

1. **Gap clustering** — cluster `log_gap` prompts by embedding (e.g.
   HDBSCAN / simple agglomerative at cosine 0.85). A dense cluster with no
   matching template ⇒ a *candidate template*; a dense cluster matching no
   category ⇒ a *candidate category*.
2. **Candidate synthesis** — for a cluster, an LLM proposes: a category
   name (or "fits existing X"), a template (prefer a *renderer* if the
   class is parameterizable; otherwise pick the best accepted canvas in
   the cluster as the *exemplar*), and a golden prompt.
3. **Dedup across categories** — before promoting, compare the candidate's
   embedding to every existing template centroid across ALL categories.
   If max cosine ≥ TAU_DUP (~0.90), surface "near-duplicate of T in
   category C": admin merges, or moves T to the better category. This is
   the "avoid similar templates in different categories" guarantee, and it
   runs on every promotion + as a periodic full-catalog sweep.
4. **Migration** — a periodic check recomputes each template's nearest
   category centroid; if a template is closer to a different category than
   its own, flag "suggest move T: C1 → C2" for admin approval.
5. **Promotion gate** — a candidate template goes `live` only after it
   passes the existing quality gate on its golden prompt (and, for
   renderers, unit tests). This reuses `infra/quality_gate.py` so a new
   template can't regress production.

Admin approves/edits/rejects each proposal. Fully-autonomous promotion is
possible later behind a flag once trust is established, but human-in-the-
loop is the right default.

## 4. "Retrieve and improve slightly" — consistency vs. staleness

Two sub-modes on an exemplar hit:

- **Serve canonical** (default for τ ≥ 0.93): return the stored exemplar
  verbatim → perfect consistency, ~0 latency, ~0 cost. This is what makes
  the platform feel reliable as usage grows.
- **Light adapt** (0.85 ≤ τ < 0.93, or numbers differ): a *constrained*
  LLM edit — swap the specific instance (e.g. a different graph, different
  matrix) into the exemplar's fixed layout schema, never a free redraw.
  Re-run the cheap structural checks; if it regresses, fall back to the
  canonical exemplar.

"Improve it slightly" over *time* is handled by versioning: an admin (or a
nightly judge) can replace an exemplar's canonical SVG with a better one;
`version` bumps, future retrievals serve the improved one. Consistency is
per-version, quality ratchets up.

## 5. Phased rollout (each phase ships value independently)

- **Phase 1 — Answer cache (highest ROI, ~1 week).** `embeddings.py` +
  embed-and-index existing `accepted` canvases + the recognition step (1)
  only. Repeated/near-identical questions instantly retrieve the prior
  good figure. Delivers consistency + speed with zero taxonomy work.
  Measure: % of live prompts that are near-duplicates (likely high for a
  growing user base hitting the same textbook questions).
- **Phase 2 — Taxonomy + 2-level recognition.** Add the tables, seed
  categories from (bucket × archetype) + the 27 existing routes/templates
  as their first members, wire recognition steps (2)+(3). Existing cascade
  becomes the fallback.
- **Phase 3 — Curation pipeline + admin view.** Gap queue, clustering,
  LLM candidate synthesis, dedup, migration, promotion gate. This is the
  self-growth you described.
- **Phase 4 — Renderer-first hardening.** For each high-traffic exemplar
  category, invest in a true parameterized renderer (the north star).
  Exemplars are the bridge; renderers are the destination.

## 6. Honest tradeoffs / risks

- **Not everything becomes a renderer.** Open-ended proofs/explanations
  realistically stay *exemplars* (retrieve+adapt), not per-question
  programs. That's fine — it still gives determinism+consistency — but
  "a parameterized template for literally everything" is not achievable;
  the exemplar layer is the honest answer for that long tail.
- **Mis-retrieval is the main danger.** Serving the wrong cached answer is
  worse than drawing a fresh one. Mitigation: high τ_cache, precision-tuned
  thresholds, and "fall through when unsure" everywhere. Recognition must
  be high-precision, low-recall by design.
- **Embedding drift / taxonomy rot.** The periodic dedup + migration sweeps
  and the promotion gate keep the catalog clean; without them it degrades.
- **Cost is negligible** (embeddings ~$0.02 per 1k prompts; cache hits
  *save* the gpt-4o generation + vision-review cost, so it's net cheaper).
- **Don't mutate live.** Creation/migration/dedup are curated, gated by the
  quality gate, and admin-approved — never silent runtime self-modification.

## 7. First concrete step

Phase 1 is self-contained and low-risk: add `sevim/embeddings.py`, a
`canvas_embeddings` index built from `accepted` canvases, and the cache
hit-check in `express_figure` behind `SEVIM_ANSWER_CACHE` (default off
until thresholds are tuned on the real corpus). Everything else builds on
that index.
