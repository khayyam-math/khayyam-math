---
name: 2026-05-12 — layout-fix marathon + fine-tune kicked off
description: All-day push on figure-layout reliability (autofit, reflow, matrix-grid, overlap detection, latex-source, sup/sub, visibility restore), www subdomain, word-level highlight, and a "Preparing visualization" overlay. Ended by kicking off an OpenAI fine-tune corpus locally (gpt-4o-mini teacher, PROMPTS_V5 pool, inspector-filter ON).
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---

Long session — most of it was incremental hardening of the
deterministic-layout pipeline plus visible UX fixes the user
surfaced live.  Ended by setting up the OpenAI fine-tune machinery
and running the corpus generation in the background on this laptop.

## Deployed today (chronological, all on khayyammath.com)

* PR25 progressive-svg streaming (deployed 06:19 morning)
* admin-page + per-model telemetry already live from yesterday
* Active model still `gpt-4o-mini` (user briefly tried gpt-4o earlier
  but reverted via /studio/admin).
* Reverted max_retries 2 → 1 after a 2 min 20 s turn was reported.
* Many overlap/layout fixes (commits listed below).
* New "Preparing visualization…" overlay (commit `fe535ae`,
  deployed 12:26 AM 2026-05-13 UTC).
* www.khayyammath.com → 301 → apex (commit `74c84b9`, deployed
  ~midnight UTC).

## Key commits (newest → oldest)

```
fe535ae  ui: "Preparing visualisation" overlay
2fd1fb9  layout: stop two long-standing overlap gaps (vertical
          fallback for too-wide group reflow; group-internal text
          now obstacles for top-level reflow)
a8c94fa  finetune: inspector-passing filter + OpenAI fine-tune
          runner (scripts/finetune_openai.py + --reject-failed-
          review flag on generate_teacher_corpus.py)
172cd7e  viewer: karaoke-style word-level highlight (tickWord +
          .sevim-word tspan)
b3b3b0b  highlight: substring fallback + canvas restore on page
          resume (visibilitychange listener)
a763d9b  layout: matrix-grid normaliser (re-emits cells on a true
          N×M lattice from `a_{ij}` / Unicode-subscript content)
1077fb4  layout: fix HTML <sup>/<sub> + slide overlapping <g>
          groups apart (reflow_overlapping_groups)
0534920  layout: autofit_group_rects pass + tighter overflow
          thresholds (0.55→0.6 em width)
3b9d681  inspector: fragmented-matrices prompt rule +
          bottom_overflow_with_unused_right + main {overflow:auto}
c6f936d  fix: structural critic now sees single-quoted SVG; new
          latex_source_in_text check
349d56f  canvas: drop the fixed 38vh — let figure decide its size
74c84b9  domains: www.khayyammath.com 301 → apex
3d9bd00  layout: greedy reflow nudges overlapping text apart
2715515  inspector: out-of-bounds + caption-overlap checks
```

## Fine-tune state at end of session

* User picked: **gpt-4o-mini as teacher AND student** (self-
  distillation), **full PROMPTS_V5 pool** (4017 prompts), inspector
  filter ON.  Books NOT included this run.
* Corpus generation kicked off at ~12:23 AM local; expected ~1.5 h.
* Output path: `data/distill/teacher_v6_mini.jsonl` (local disk
  only — nothing in S3).
* OpenAI fine-tune runner ready: `scripts/finetune_openai.py`.
  Usage: `.venv/bin/python scripts/finetune_openai.py --in
  data/distill/teacher_v6_mini.jsonl --base gpt-4o-mini-2024-07-18
  --suffix khayyam-v1`
* Expected total cost: ~$10 generation + ~$3 training = ~$13.
* Expected timeline: 1.5 h corpus → 30-90 min training → 10 min
  wire-up + cdk deploy.

## When you next sit down

1. Check the corpus completed: `wc -l data/distill/teacher_v6_mini.jsonl`
   should be in the 2800-3400 range (inspector filter drops some).
2. Run the fine-tune runner (command above).  It'll print the
   FINETUNED_MODEL_ID when training completes.
3. Add the model id to `studio/app.py` MODEL_CATALOG.  Example
   entry shape:
   ```python
   {"id": "ft:gpt-4o-mini-2024-07-18:khayyammath:khayyam-v1:abc123",
    "label": "Khayyam-tuned 4o-mini", "default": False,
    "available": True}
   ```
4. `cd infra && AWS_PROFILE=sevim CDK_DEFAULT_ACCOUNT=332504859695
   CDK_DEFAULT_REGION=us-east-1 SEVIM_DOMAIN=khayyammath.com npx
   aws-cdk deploy --require-approval never`
5. Flip active model to the new id via /studio/admin.
6. A/B test against base mini on a few hard prompts (4×4 matrix
   inverse, 3SAT, determinant expansion).

## If you want books in the next run

`scripts/extract_textbook_figures.py` + `scripts/generate_reference_corpus.py`
pull from Strang, ESLII, OpenStax, Goodfellow PDFs.  Add the output
to `data/distill/teacher_v6_mini.jsonl` (it's append-only) and
re-run finetune_openai.py.  ~30 min more wall clock, ~$3-5 more
cost, ~30 % more examples.

## Open follow-ups

* UAE IP package deferred polish items (task #30) — verbatim
  excerpt, source-code-explanation truncation, real screenshots.
* AWQ-quantise Qwen for ~2.5× speedup (instance currently
  TERMINATED — `cdk deploy -c enable_qwen=1` to bring it back).
* `gpt-4o`-as-teacher down-distillation if v1 fine-tune doesn't
  produce a quality jump (would cost ~$60 for 800 examples or
  ~$300 for full corpus; gives mini genuinely 4o-style outputs).
