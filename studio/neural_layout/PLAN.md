# Neural Layout Correction — Implementation Plan

Last updated: 2026-05-14
Origin: user request for the most sophisticated, scientifically-sound
neural approach to one-shot layout repair, constrained only by AWS cost.

## Goal

Replace the slow `iterate-with-LLM-until-the-vision-reviewer-passes`
fix loop with a small neural network that proposes layout corrections
in <1 s on CPU. Train locally on the RTX 5090, deploy on CPU Fargate.

## Architecture — decided

**Hierarchical graph-conditioned discrete diffusion (LayoutDM-style),
30–60 M params, with CP-SAT projection between denoising steps.**

References that justify the choice:

- LayoutDM (arXiv 2303.11589) — discrete diffusion for graphic layout.
- LayoutTransformer (arXiv 2006.14615), LayoutFormer++ (2208.08037) —
  transformer-on-layout-tokens baselines.
- Constrained Layout via Latent Optimisation (2108.00871) — the
  "diffuse then project through constraint solver" recipe.
- DeepGD / CoRe-GD — graph drawing via GNNs.

### Input representation

Parse the **broken SVG** into a `SceneGraph`:

- **Nodes**, one per meaningful element or rigid group:
  - `type` ∈ closed vocabulary (`rect`, `text`, `line`, `circle`,
    `path`, `g`, `matrix-group`, `axis`, `caption`, …)
  - `bbox` quantised to a 256-bin grid (LayoutDM-style)
  - `text_content`, `text_len`, `font_size`, `stroke_width`
  - `is_narration_anchor`, `is_caption`, `is_protected`
  - `parent_id`, `top_level_group_id`
  - viewport (`canvas_w`, `canvas_h`) — phone vs desktop
- **Edges**:
  - `parent_of` (hierarchy)
  - `sibling_of` (same parent)
  - `narration_co_anchor` (highlighted together)
  - `semantic_relation` (when known, e.g. matrix-cell-of, axis-tick-of)

### Output

Per-node **position correction**: either delta `(Δx, Δy, Δw, Δh)` (GNN
baseline) or quantised position tokens (LayoutDM target).

### Hard constraint enforcement

After every K denoising steps, project the predicted positions through
the existing CP-SAT planner (`studio/layout_planner.py`) to enforce:

- no overlap
- in-canvas / in-viewBox containment
- group-cohesion (children move with their parent group)
- narration-anchored ids stay near their original positions
- minimum spacing

The model proposes; CP-SAT enforces. This is the AlphaFold-style
pattern: neural prediction + physics-grade refinement.

## Data strategy — REAL prompts, all math concepts

**No matrix bias.** Real-world prompts from the full math curriculum.
Generate `(broken, fixed)` pairs by running the express loop and
capturing every intermediate iteration.

### Sources

1. **`scripts/expanded_prompts_v5.py:PROMPTS_V5`** — ~4000 hand-curated
   prompts already covering geometry (Euclid, Pythagoras, conic
   sections, trig identities), calculus (Riemann sums, Taylor series,
   FTC, L'Hôpital), linear algebra (matrix ops, SVD, eigen, Gram-
   Schmidt), set theory + logic (Venn, truth tables, Hasse), discrete
   math (combinations, pigeonhole), probability, real analysis,
   topology, group theory, complexity classes, proof techniques, …
2. **Existing `data/distill/teacher_v6_mini.jsonl`** — 1045 rows already
   in `mode=corrected` (broken→critique→fixed triples). Free starter.
3. **Live express-loop telemetry** — every production turn that retried
   already logs `result["repairs"]`. Backfill from CloudWatch.

### Generation pipeline (Phase A.5)

For each prompt in PROMPTS_V5:

1. Run `studio.express.express_figure` with `max_retries=4` so the loop
   accumulates real (bad, critique, good) repairs.
2. **SVG generator**: local Qwen v4 LoRA on 5090 (cheap, free).
3. **Vision auditor**: GPT-4o (since Qwen LoRA is text-only). Renders
   the SVG to PNG via CairoSVG, sends to gpt-4o, gets pass/fail +
   structured critique.
4. For each `repair` entry, emit a `TrainingPair`:
   - source = scene graph of `bad_svg`
   - target = scene graph of `good_svg`
   - prompt, viewport, math-concept bucket, audit scores in metadata.
5. Also emit the final accepted SVG paired with the **first** iteration
   (long-distance repair pair) — teaches more aggressive corrections.

### Augmentation

Synthetic perturbation **on top of** real-prompt accepted layouts
(not as the spine — the user explicitly does not want synthetic-only):

- jitter all node positions by U(-40, 40) px
- group displacement
- viewBox compression
- z-order scramble

Adds ~3–5× pairs; ground truth is the unperturbed original.

### Stratification

Bucket every prompt by math concept (regex / keyword on the prompt
string):

```
geometry / calculus / linear_algebra / set_theory_logic /
combinatorics / probability / real_analysis / topology /
group_theory / number_theory / complexity / proof / other
```

Equal-count sampling at train time so matrices don't dominate.

### Filtering — 3-of-4 consensus

A pair is admitted to the training set only if the "fixed" SVG passes
**at least 3 of 4** independent checks:

1. Structural critic (deterministic — overlap, OOB, narration ids).
2. CairoSVG render at desktop 900px, overlap check on rendered bboxes.
3. CairoSVG render at phone 375px, overlap check.
4. GPT-4o pairwise "fixed vs broken" comparison.

### Coverage controls

- ~10 % identity pairs (good in, good out) — model must learn that
  "do nothing" is a valid action.
- ~5 % adversarial extremes (everything stacked, half off-canvas).
- Phone + desktop viewport rendering for every example.

### Target dataset size

| Source                              | Approx. pairs |
| ----------------------------------- | ------------- |
| Existing teacher_v6_mini corrected  | 1 045         |
| PROMPTS_V5 × ~4 iterations × filter | 12 000–18 000 |
| Synthetic perturbation augmentation | 8 000–12 000  |
| Identity + adversarial              | 3 000         |
| **Total after filter**              | **≈ 30 000**  |

Manual gold validation set: 300 pairs, hand-rated.

### Cost of data generation

- Local Qwen on 5090: free (electricity).
- GPT-4o vision audit: 4000 prompts × ~4 iterations × ~$0.005 / image
  ≈ **$80** total.
- Existing OpenAI budget already covers this.

## Phases

### Phase A — schema + exporter (this session)

Files:
- `studio/neural_layout/__init__.py`
- `studio/neural_layout/schema.py` — dataclasses for `NodeFeatures`,
  `EdgeFeatures`, `SceneGraph`, `TrainingPair`. JSON / JSONL
  serialisation. Versioned (`schema_version: 1`).
- `studio/neural_layout/svg_to_graph.py` — SVG XML → `SceneGraph`.
  Uses `xml.etree.ElementTree` only. No LLM. Pure function.
- `studio/neural_layout/exporter.py` — express-loop result
  (the dict returned by `express_figure`) → list of `TrainingPair`.
- `studio/neural_layout/extract_from_corpus.py` — script: convert
  the 1045 `mode=corrected` rows in `teacher_v6_mini.jsonl` to
  TrainingPair JSONL. **Free starter dataset, runs in seconds.**
- `tests/test_neural_layout_schema.py` — round-trip + a few
  hand-built SceneGraphs.

### Phase A.5 — full corpus generation (after A; runs in parallel with B)

Files:
- `scripts/build_layout_corpus.py` — adapted from
  `scripts/generate_teacher_corpus.py`. New CLI:
  - `--pool-module scripts.expanded_prompts_v5:PROMPTS_V5`
  - `--svg-model qwen-v4 --base-url http://localhost:8000/v1`
    (local vLLM server)
  - `--vision-model gpt-4o` (audit, falls back to gpt-4o-mini)
  - `--out data/neural_layout/corpus_v1.jsonl`
  - `--max-retries 4 --concurrency 4`
- Resumable, append-only JSONL, same pattern as existing script.

Run overnight on 5090; expected wall time ~6–10 h.

### Phase B — GNN baseline (after data exists; ~1 week)

Files:
- `studio/neural_layout/models/gnn_baseline.py` — GraphSAGE or GAT
  (PyTorch Geometric), 5–15 M params, predicts per-node delta.
- `studio/neural_layout/train_gnn.py` — train loop with mixed loss
  (bbox MSE + soft overlap penalty + OOB penalty + rigidity penalty).
- `studio/neural_layout/eval.py` — overlap-count, OOB-count, mean
  displacement, narration-anchor preservation, per-bucket metrics.

This is the **feasibility check** — if the GNN can't get the overlap
count down by 80 % on the validation set, the full LayoutDM probably
won't either, and we need to revisit the data.

### Phase C — full LayoutDM (after Phase B numbers; ~2 weeks)

Files:
- `studio/neural_layout/models/layoutdm.py` — graph-conditioned
  discrete diffusion. 30–60 M params. 8–16 denoising steps. CP-SAT
  projection every K steps.
- `studio/neural_layout/train_layoutdm.py` — diffusion training loop.

### Phase D — CPU inference (after C trains)

Files:
- `studio/neural_layout/server.py` — FastAPI app. Endpoint
  `POST /layout/correct` → input scene graph → output corrected.
- `studio/neural_layout/inference.py` — model loading, ONNX export,
  optional int8 quantisation.
- `infra/lib/neural-layout-stack.ts` — CDK definition for a 0.5 vCPU
  / 1 GB Fargate service, no GPU. Estimated cost <$10/month.
- `studio/express.py` — `SEVIM_NEURAL_LAYOUT=on` env flag, calls the
  new endpoint after the planner pass. Deterministic fallback if it
  errors or times out.

### Phase E — A/B test in production

50 % traffic to neural, 50 % to current planner-only. Compare:

- median figure latency
- vision-review pass-rate
- explicit-thumb-up rating (the existing 👍 / 👎 UI)

Promote if neural wins on ≥2 of 3.

### Phase F — viewport conditioning (task #52)

Re-train with viewport as an explicit conditioning input. Phone vs
desktop layouts diverge meaningfully (vertical stacks on phone,
horizontal on desktop).

## Cost summary

- Local training: **$0** (5090 already owned).
- Data generation: **~$80** GPT-4o vision audit + free local Qwen.
- AWS serving: **<$10/month** CPU Fargate (no GPU).
- AWS storage: **<$1/month** S3 for model weights.

This is ~98 % cheaper than the Qwen-on-g6.xlarge serving we already
sunset.

## Risks

- **Vision-review false positives during data gen** — mitigated by
  the 3-of-4 consensus filter.
- **Distribution gap between PROMPTS_V5 and live traffic** — mitigated
  by also backfilling from CloudWatch telemetry.
- **GNN can't model long-range layout dependencies** — Phase B is the
  cheap check; if it fails, jump straight to LayoutDM.
- **CP-SAT projection introduces latency** — if >1 s/figure on CPU,
  use the GNN-baseline as a faster proposer ahead of LayoutDM.

## Open decisions (deferred until after Phase A)

- Qwen v4 vs gpt-4o-mini as the SVG generator during data gen.
  (Qwen is free but currently slower; gpt-4o-mini costs ~$5 total.)
- 8 vs 16 vs 32 denoising steps.
- 2 vs 3 viewport buckets (phone 375 / tablet 768 / desktop 900).

## Non-goals

- We do **not** generate SVGs from scratch (the current LLM pipeline
  remains the generator).
- We do **not** rewrite `studio/layout_planner.py` — it becomes the
  projection oracle for the diffusion model.
- We do **not** replace the matrix templates — those bypass the model
  entirely and stay deterministic.

## Resume hint for future sessions

Status of each phase is tracked in tasks #57–#62. Schema lives in
`studio/neural_layout/schema.py` once Phase A lands. Read this plan
top-to-bottom before changing direction.
