---
name: 2026-05-14 — neural layout correction kicked off (LayoutDM plan + Phase A)
description: New major initiative — train a graph-conditioned discrete-diffusion model for layout repair, distilling the slow LLM iterate-loop into <1s CPU inference. Plan + Phase A schema/parser/exporter complete. Overnight PROMPTS_V5 corpus build in flight.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
**Goal**: replace the slow LLM iterate-with-vision-review fix loop
with a small NN that proposes layout corrections in <1 s on CPU.

**Architecture chosen**: hierarchical graph-conditioned discrete
diffusion (LayoutDM-style), 30–60 M params, CP-SAT projection
between denoising steps. **Train on 5090 (free); deploy on CPU
Fargate (<$10/month).** AWS Qwen serving stays sunset.

**Canonical plan**: `studio/neural_layout/PLAN.md` — read this
before changing direction.

## Phase A — DONE
- `studio/neural_layout/{schema,svg_to_graph,exporter,perturb}.py`
- 22 tests pass (`tests/test_neural_layout_schema.py`)
- `scripts/extract_layout_starter_pairs.py` — extracts
  `mode=corrected` rows from `teacher_v6_mini.jsonl`
- `scripts/rebucket_pairs.py` — re-classify math_bucket after
  keyword expansion
- `scripts/augment_pairs_synthetic.py` — perturbation-based
  augmentation (jitter, displace, viewbox_compress, scale_one,
  stack_overlap)

## Data so far on disk (`data/neural_layout/`)
| File | Pairs | Bytes | Source |
|---|---|---|---|
| `starter_pairs.jsonl` | 1039 | 29 MB | from teacher_v6_mini corrected rows |
| `synthetic_aug_v1.jsonl` | 3114 | 93 MB | 3× perturbation of starter targets |
| `corpus_v1.jsonl` | growing | growing | overnight run via PROMPTS_V5 |

**Total ready to train on by morning: ~10–12 K real-prompt pairs +
~10 K synthetic = ~20 K pairs.**

## Overnight corpus build — kicked off 2026-05-14 ~late evening

- Command: `scripts/build_layout_corpus.py --model gpt-4o-mini
  --pool-module scripts.expanded_prompts_v5:PROMPTS_V5
  --out data/neural_layout/corpus_v1.jsonl --max-retries 4
  --concurrency 12`
- Log: `/tmp/corpus_build.log`
- Pool: PROMPTS_V5 (4017 prompts, full math curriculum)
- Budget: $80 GPT-4o vision audit + ~$5 gpt-4o-mini SVG gen,
  approved by user.
- Expected wall time: 5–10 h.

## Math bucket coverage (after expanded keyword set)

Real-prompt classifier now uses 20 buckets including
`differential_equations`, `optimization`, `statistics_ml`,
`complex_analysis`, `signal_processing`, `physics`, `function_plot`.
"Other" residual: 254 of 1039 starter pairs (24 %), down from 501
(48 %). Distribution after rebucket:
- geometry 108, differential_equations 103, statistics_ml 86,
  complex_analysis 71, optimization 60, calculus 53, linalg 49,
  signal_processing 38, number_theory 32, combinatorics 31,
  topology 28, function_plot 27, probability 27, physics 23,
  set/logic 22, group_theory 10, real_analysis 10, proof 5,
  complexity 2, other 254.

## User's explicit constraints
- **AWS cost is the only constraint** — go for the most
  sophisticated method (LayoutDM, not just GNN baseline).
- **Real prompts are the spine** — no matrix bias; synthetic
  perturbation is augmentation, not primary.
- **Train on 5090, deploy on CPU Fargate** — no GPU on AWS.
- Qwen v4 LoRA storage on AWS already torn down (cost) — don't
  re-host it.

## Open task list
- #57 Phase A — DONE
- #58 Starter extraction — DONE
- #59 PROMPTS_V5 corpus build (light) — IN FLIGHT (PID 190901,
       162 pairs at 37min, projecting ~600 pairs at completion ~2h
       more, slower than first run after max_retries was cut to 1)
- #60 Phase B GNN baseline — DONE as code artifact, negative
       result: -3% to -6% vs no-op on synthetic data (multiple
       valid repairs ⇒ one-shot regression fails)
- #61 Phase C LayoutDM diffusion — DONE as code artifact,
       47.5% token accuracy, regenerated layouts stay in-bounds
       (0 OOB / graph — model genuinely learned that constraint)
       but produce higher text-bbox overlap than targets
- #62 Phase D CPU Fargate deployment — server.py scaffolding
       written, untested deployment

## Status of training artifacts
runs/gnn_v1     — 2.7M-param GNN baseline (3:54am train time)
runs/gnn_v2     — 6.3M-param GNN with delta-loss×4 + larger model
runs/gnn_real_only — same architecture trained only on 1039 real
                     starter pairs
runs/layoutdm_v1 — 6.9M-param graph-conditioned discrete-diffusion
                   denoiser

## Code on disk
studio/neural_layout/
  PLAN.md               — full design + phases (read first)
  schema.py             — TrainingPair / SceneGraph / classifier
  svg_to_graph.py       — SVG → SceneGraph parser (22 tests pass)
  exporter.py           — express-loop / corpus → TrainingPair
  perturb.py            — synthetic augmentation (5 perturbation kinds)
  data.py               — PyTorch dataset, BucketBalancedSampler
  losses.py             — delta MSE + soft overlap + OOB + protected
  eval.py               — overlap-count / OOB / displacement metrics
  inference.py          — GNN baseline inference
  diffusion.py          — D3PM absorbing-state, schedule, denoise loop
  inference_layoutdm.py — LayoutDM repair inference
  server.py             — FastAPI (Phase D scaffolding)
  models/
    gnn_baseline.py     — edge-aware MHA + FFN + delta head
    layoutdm.py         — same backbone + position tokens + FiLM + classifier head
  train_gnn.py / train_layoutdm.py
scripts/
  extract_layout_starter_pairs.py
  extract_clean_targets_for_perturbation.py
  augment_pairs_synthetic.py
  rebucket_pairs.py
  build_layout_corpus.py — PROMPTS_V5 → real iteration pairs
  eval_gnn.py — per-bucket eval

## Confirmed: structural under-determination, not a data problem
Real-only retrain (runs/gnn_real_v2, runs/layoutdm_real_v1) on the
full 1519 real iteration pairs produces an EVEN WORSE no-op gap:
-8.3% on real, vs -3.0% on synthetic. Per-bucket every single
category is negative.

LayoutDM val_acc dropped slightly with real data (47.0% vs 47.5%
on clean-only). Real targets have larger absolute deltas than
synthetic, so absolute errors are larger but RELATIVE accuracy
is the same.

**Conclusion**: source-feature-only one-shot delta regression
(GNN baseline) and source-aware absorbing diffusion (LayoutDM
Phase C) BOTH cannot beat the trivial no-op baseline on the
layout-repair task, because:
  - Many distinct destinations are equally valid for one broken
    layout (the "fix" depends on context the model can't see)
  - The model averages over the modes and outputs something
    closer to no-op than any of them

This is not a tuning problem — more epochs / larger model /
better data won't fix structural ill-posedness.

## Hybrid integration (DONE — also no-op-equivalent)

studio/neural_layout/hybrid.py shipped. Runs LayoutDM denoise →
rewrites top-level <g> transforms + <text> x/y in the SVG XML →
hands to plan_layout for hard-constraint enforcement.

Benchmark (scripts/benchmark_hybrid_real_svg.py, 30 real broken
SVGs from teacher_v6_mini.jsonl `mode=corrected` rows):

  mode              ovlp   oob
  no_op             21.0   1.2
  planner alone     21.0   1.0   ← workhorse, ties no-op 30/30
  model_only        21.3   1.7   ← regresses slightly
  hybrid            21.9   1.5   ← regresses (model nudges off planner candidate grid)
  ground_truth      20.7   0.4   (reference)

Hybrid wins overlap vs no-op on 7/30 pairs but loses average.
Planner is the strongest baseline on its own. The trained LayoutDM
checkpoint lacks enough learned signal to materially help.

## Production recommendation (UPDATED — after Phase E + vision audit)

**Deploy: CP-SAT planner + quality-scorer re-ranker.**
Pass rate on gpt-4o vision audit (150 pairs):
- no_op                : 20.0%
- planner alone        : 21.3%
- **rerank_planner**   : **23.3%**  ← +2pp win, the production target
- rerank_full (w/ LDM) : 16.0%  ← do NOT include LayoutDM in pool
- ground_truth         : 21.3%

The trained QualityScorer (runs/quality_scorer_v1/best.pt, 1.83M
params, 71.4% pairwise win on real labels) genuinely picks better
layouts among planner candidates. Cost-per-figure: ~5-10s CPU
(4 planner runs + 5 scorer calls). No GPU needed.

**Do NOT deploy the trained LayoutDM model.** Every benchmark
showed it regresses or no-ops; when included in the rerank pool
it makes pass rate WORSE (rerank_full 16% vs planner 21%).
The scorer's preference for LayoutDM outputs is a learned bias
that doesn't match gpt-4o's actual preferences.

## How to ship rerank_planner

1. studio/neural_layout/rerank.py is ready (rerank() function).
2. Add SEVIM_RERANK=on env flag to express.py post-generation step.
3. Bundle runs/quality_scorer_v1/best.pt into the Fargate image
   (1.83M params, ~7 MB on disk).
4. CPU-only inference, no model server needed (in-process call).
5. A/B test on a fraction of live traffic.

## Session outcome (final)

After all this neural-layout work, the **production win came from
the Graphviz route** (see project_graphviz_route_live.md), not from
the trained models. The neural research artifacts stay on disk as
deployable options for the future but are NOT in production.

The 2026-05-14 session ended with two commits pushed to main:
  - `2153181` — graphviz route (DEPLOYED, ECS rev 91)
  - `1b99d0d` — neural layout research (CODE COMMITTED, NOT
    DEPLOYED)

`data/neural_layout/` (training pairs, 760 MB) and `runs/` (model
checkpoints, 230 MB) are .gitignored — they live on the 5090
locally. To reproduce, run `scripts/extract_layout_starter_pairs.py`
then `scripts/build_layout_corpus.py` to regenerate the pairs.

## What would actually move the needle

The neural pipeline is structurally sound. The bottleneck is the
training signal:

  a. Human-rated (broken, fixed) pairs — gold supervision.
  b. Vision-feedback in the training loop (RLHF-style with
     gpt-4o judge in the gradient path).
  c. Predict DIFFERENT outputs than positions: e.g., "which two
     nodes should be GROUPED together?" — then CP-SAT decides
     where to place those groups.

All three are bigger initiatives than this session. The pipeline,
data factory, training scripts, eval harness, and CPU Fargate
server scaffolding are all in place to support any of them
without further infrastructure work.

## Resume hint
Next session: check `wc -l data/neural_layout/corpus_v1.jsonl` and
`tail /tmp/corpus_build.log`. If overnight run completed, run
`scripts/rebucket_pairs.py` on `corpus_v1.jsonl`, then either:
1. Run `scripts/augment_pairs_synthetic.py` on `corpus_v1.jsonl`
   to get another ~3× synthetic expansion, then start Phase B
   (GNN baseline scaffolding).
2. Or skip ahead to Phase C LayoutDM if the user wants the
   sophisticated ceiling immediately.

Total dataset shape at training time: cat starter + corpus_v1 +
synthetic_aug into one JSONL, stratified-sample by `math_bucket`,
split 90/10 train/val. The 300-pair manual gold set is still TODO.
