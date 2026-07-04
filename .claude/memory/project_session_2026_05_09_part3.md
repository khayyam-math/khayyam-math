---
name: 2026-05-09 overnight — public-deployment foundation + first LoRA + blind judge eval
description: Built telemetry/sessions/safety/export pipeline, ran 20 diverse prompts through gpt-4o, fine-tuned Qwen2.5-7B with LoRA in 42s, ran 3-way comparison + gpt-4o blind judge. Schema 0/20 → 18/20 with LoRA, but mean quality 17.1/30 → 13.8/30 (bimodal: 9 prompts improved, 5 catastrophically broke).
type: project
originSessionId: ddab3e35-4da7-437e-965d-3a536788200b
---
Picks up after Tag Success2 (commit c99873a) was pushed.  Goal: make Sevim
public-deployable, capture user data, fine-tune a local Qwen, compare to
gpt-4o.  AWS later — local first.

## What's in the repo now

* `sevim/telemetry.py` — SQLite event log (sessions, turns, canvases).
  Thread-safe, never raises into the request path.  Writes to
  `~/.local/share/sevim/telemetry.db`.  Backfills `refined_within_s`
  on the prior turn so the export filter knows what got accepted.
* `studio/sessions.py` — token-bucket `RateLimiter`, `check_cost_guard`,
  IP hashing with salt, `estimate_express_cost`.
* `studio/safety.py` — content-filter denylist (prompt injection, system
  extraction, adult, self-harm).
* `studio/preferences.py` — pre-router for backend-only requests
  (highlight color, audio speed/volume) so they bypass the LLM.
* `studio/export_finetune.py` — JSONL exporter for Qwen training.
  Default filter: `retries_used == 0` AND no refinement within 60s
  (proxy for "user accepted the figure").
* `studio/app.py` — `chat()` endpoint wires telemetry start/end,
  safety check, rate limit, cost guard.
* `service/canvas.py` — `is_raw_svg`, `raw_svg_ids`, `genesis_prompt`,
  `set_raw_svg()` (bypasses S3→S5).  `narrate()` validates highlights
  as list-or-string.
* `service/static/canvas.html` — multi-element highlights via array
  with `[id="..."]` selector, `/studio/preferences` polling.
* `studio/static/studio.html` — `sessionId` in localStorage,
  `pinnedCanvasIds` with 📌 button, `prior_canvas_ids` in chat POST.
* `tests/test_telemetry.py` + `tests/test_sessions_and_safety.py` —
  14 new tests; **51/51 total pass**.
* `scripts/diverse_prompts_test.py` — 20 prompt corpus (NP-completeness
  reductions, linear algebra, calculus, set theory, graph algorithms,
  probability, number theory, geometry).
* `scripts/train_lora.py` — LoRA fine-tune (PEFT rank 16/alpha 32,
  3 epochs, lr 2e-4, bf16).
* `scripts/compare_models.py` — 3-way comparison (gpt-4o vs Qwen base
  vs Qwen+LoRA), writes `/tmp/sevim_compare/REPORT.md`.
* `scripts/judge_figures.py` — blinded gpt-4o-as-judge over the
  per-prompt PNGs (random A/B/C labels).  Writes
  `/tmp/sevim_compare/judge_scores.csv` and appends a "Judge scores"
  section to REPORT.md.
* `docs/PUBLIC_DEPLOYMENT.md` — research/plan doc.
* `docs/AWS_MIGRATION.md` — ECS Fargate + RDS + S3 + ALB, ~$840/mo
  baseline (gpt-4o-dominated), drops to ~$200/mo with self-hosted
  fine-tuned Qwen.
* `docs/QWEN_VS_GPT4O_REPORT.md` — narrative report on the comparison
  run (the headline write-up the user asked for).

## What ran tonight

1. Studio launched at 127.0.0.1:7781 (still running in background).
2. `python -m scripts.diverse_prompts_test` sent 20 prompts → captured
   into telemetry DB.
3. `studio.export_finetune` → `/tmp/sevim_finetune.jsonl` (18/20 turns
   passed the filter; lost 2 to refinements/retries).
4. `/tmp/finetune_venv/bin/python3 scripts/train_lora.py` →
   `/tmp/qwen_sevim_lora/` adapter.  **42 s** wall-clock on RTX 5090.
   Loss 1.27 → 0.28; mean-token-accuracy 0.93.
5. `/tmp/finetune_venv/bin/python3 scripts/compare_models.py` →
   per-prompt artefacts under `/tmp/sevim_compare/<i>_<slug>/` plus
   `/tmp/sevim_compare/REPORT.md`.
6. Wrote narrative `docs/QWEN_VS_GPT4O_REPORT.md`.

## Headline result (with blind judge)

| Model | Renders to PNG | Schema-compliant | gpt-4o-judge mean / 30 | Top-rank |
|---|---|---|---|---|
| gpt-4o | 20/20 | 20/20 | **22.9** | 14/20 |
| Qwen base | 18/20 | **0/20** | 17.1 | 3/20 |
| Qwen + LoRA | 18/20 | **18/20** | **13.8** | 4/20 |

Two findings in tension:
1. 42 s of LoRA training on 18 examples taught Qwen the JSON envelope
   (base model never honoured it — emitted raw SVG in ```xml fences,
   0 narration phrases, ever).
2. **The same fine-tune lowered mean figure quality by 19 %**.  The
   LoRA's effect is bimodal: dramatic wins on 9 prompts where
   structure resembled training (eigenvalue 12 → 25, SVD 18 → 25,
   BFS 16 → 24, Hamiltonian 0 → 11) and catastrophic blanks on
   prompts whose structure didn't (Euclidean 28 → 3, Bayes 24 → 3,
   ∫x² 18 → 0, truth table 27 → 10).  Classic small-corpus
   over-fit — memorised structural templates applied where they
   don't fit.

Also fixed a stale claim in the auto-REPORT.md: prompt 6 (matrix
mult) LoRA output was counted as "valid SVG, 5,516b" but actually
contains literal LaTeX inside `<text>` (`\begin{bmatrix}`, unescaped
`&`).  Cairosvg rejects it.  Real LoRA render success is 18/20 not
19/20.

## Operational state

* Studio process: still running on port 7781 (background bash from
  earlier).  `SEVIM_TELEMETRY=1`, OpenAI key in env.
* Telemetry DB: `~/.local/share/sevim/telemetry.db` — has the 20
  diverse turns.  Sessions all start with `diverse_`.
* LoRA adapter: `/tmp/qwen_sevim_lora/` (~40 MB).
* Compare artefacts: `/tmp/sevim_compare/` (20 dirs + REPORT.md).
* Fine-tune venv: `/tmp/finetune_venv/` (transformers + peft + trl).
* Training corpus: `/tmp/sevim_finetune.jsonl` (18 examples).
* Logs: `/tmp/lora_train.log`, `/tmp/compare_run.log`.
* Last commit on main: **a2a4f7f** ("v0.4 → v0.5: data pipeline +
  LoRA experiments + narration interrupt"), pushed to origin/main on
  2026-05-10 ~00:30.  Bundles all overnight work plus the
  chat-side narration-interrupt feature.

## Resume here next time

1. `git status` — review/commit tonight's changes (a lot of new files;
   no unrelated edits).
2. Check Studio still running: `curl -s 127.0.0.1:7781/`.
3. To re-run the comparison: `/tmp/finetune_venv/bin/python3
   scripts/compare_models.py` (regenerates artefacts in place).
4. To collect more training data: just use Studio normally — every
   accepted figure ends up in the DB.  Re-export with
   `python -m studio.export_finetune --out /tmp/new.jsonl`.
5. Re-train: `/tmp/finetune_venv/bin/python3 scripts/train_lora.py
   --dataset /tmp/new.jsonl --out /tmp/qwen_sevim_lora_v2`.
6. AWS migration: when ready, `docs/AWS_MIGRATION.md` has the full
   architecture sketch.

## Hyperparameter A/B (LoRA v2)

After v1's bimodal failure, trained v2 with rank 8, lr 1e-4, 2 epochs
(half the capacity, half the LR, fewer steps).  Final train loss
1.18 vs v1's 0.28 — much less memorisation.

Blinded judge (base vs v1 vs v2 only, no gpt-4o):

| Variant | Mean/30 | Top-rank | Produced |
|---|---|---|---|
| base | 18.3 | 8/20 | 18/20 |
| v1 (r16, lr2e-4, 3ep) | 15.2 | 4/20 | 18/20 |
| **v2 (r8, lr1e-4, 2ep)** | **17.8** | **10/20** | **19/20** |

v2 nearly matches base on average AND wins more head-to-head.  Fixed
most v1 catastrophes (matrix mult 0→9, Gaussian elim 4→20, Venn
5→26, truth table 3→19, ∫x² 0→7) but introduced a new regression
on prompt 1 (3SAT→VC: dropped JSON envelope and ran into a
token-limit loop emitting 36 phantom clauses).  Lower LoRA strength →
schema compliance becomes fragile.

**Practical conclusion**: both hyperparams AND data matter.  v2 is
the right starting point for retraining once the corpus grows —
lower capacity should generalise better at 200+ examples.

## Open follow-ups

* Need 200-500 user-accepted examples before LoRA can replace gpt-4o.
  v2 nearly matches base; gpt-4o still wins outright (22.9/30).
* **Set up a held-out eval set** (~20 prompts that never enter
  training).  Without this we can't detect over-fit.
* **Add render-success filter to training corpus** — cairosvg-render
  every SVG in `studio/export_finetune.py` before writing the JSONL.
* When retraining: start from v2 hyperparams (r8, lr1e-4, 2ep), not
  v1.
* Don't switch `SEVIM_VLLM_URL` to LoRA until held-out judge mean
  ≥ 22/30.
