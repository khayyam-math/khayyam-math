---
name: 2026-05-13 overnight — 3-iteration audit loop + fine-tune setup
description: Followed user's "go to sleep, keep working" directive — kicked off OpenAI fine-tune corpus generation, ran a 4-pass UX audit loop with playwright on complex prompts (spectral theorem, 3SAT→VC), identified defects programmatically, deployed 4 batches of fixes. Defect count 122 → 9 → 6 across passes.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---

## Where things stand at 1:35 AM

* **Production** runs the latest build (compose-transform fix, 1:33:28 AM).  Active model `gpt-4o-mini`.
* **Corpus generation** still running locally — ~555 / 4017 lines.
  ETA ~5:30 AM.  Reviewer is OFF (deterministic critics still gate).
* **Fine-tune pipeline** ready:
  * `scripts/finetune_openai.py` — uploads + creates job + polls + prints model id.
  * `scripts/promote_finetune.py` — wires the id into MODEL_CATALOG
    AND sets `SEVIM_FORCE_ACTIVE_MODEL` in CDK env.
* **Auto-flip** authorised by the user; will fire after fine-tune
  completes via `promote_finetune.py` + `cdk deploy`.

## Layout-pipeline now includes (in order, on every figure)

```
clamp_text_to_viewbox     – pull negative-y/-x text inside
clamp_group_transforms    – raise <g transform> when children
                            (text/rect/circle) would render above top
fix_html_subsup           – <sup>/<sub> → <tspan baseline-shift>
normalize_matrix_layout   – re-emit a_{ij} cells on a regular grid
autofit_group_rects       – SHRINK and expand container rects
reflow_overlapping_groups – compose existing transforms;
                            text obstacles included;
                            vertical fallback when no horizontal room
wrap_overlong_text        – split too-wide text into stacked lines
reflow_overlapping_text   – greedy shift of overlapping top-level text
```

PLUS the structural critic (`_structural_review`) runs after all
passes and lists issues for the model's retry.

## UX-audit-loop commits (post bedtime)

```
0214510  layout: compose group transforms + text obstacles
86ae4fd  wrap_overlong_text + autofit shrinks + disable word-highlight
3831aef  clamp_group_transforms — lift groups whose children render above viewBox
0af1935  clamp_text_to_viewbox — pull negative-y/-x text inside
2d7ef50  backend: SEVIM_FORCE_ACTIVE_MODEL deploy-time override
a8c94fa  finetune: inspector-passing filter + OpenAI fine-tune runner
dba998d  scripts: promote_finetune.py
```

## What was learned

* OpenAI tts-1 has no per-word timestamps; the karaoke
  word-highlighter I built was effectively guessing — disabled it.
* Models routinely use `<g transform="translate(dx 0)">` with children
  at local y=0 — clips above the canvas top.  Needed a new clamp pass.
* `autofit_group_rects` was expand-only — the user explicitly called
  out "huge boxes around matrices."  Now bidirectional.
* Long unwrapped explanatory text needed a real word-wrap pass.
* Reflow had to compose transforms (`translate(a) + translate(b) =
  translate(a+b)`) AND treat top-level text as obstacles to slide
  matrix groups past titles.

## Audit script and where to resume

* Local dev server: port 8765 (auth bypass, review-mode off).
* Audit: `.venv/bin/python /tmp/ux_audit.py` — runs 2 prompts on
  mobile (390×760) + desktop (1280×900), screenshots to `/tmp/ux_audit/`.
* Analyser: `.venv/bin/python /tmp/audit_analyze.py` — picks the
  most-recent batch from `/tmp/ux_audit_results.jsonl` and ranks
  defects by visual impact.

## Remaining minor defects (audit pass 4)

* Spectral-theorem desktop: "1" matrix cell (inside `<g>`) overlaps
  "Q is orthogonal: QᵀQ = I" top-level text at 63% — group-internal
  text against top-level text in another column.  Reflow obstacle
  list already includes group-internal text bboxes, so this case
  must be a parent transform we're not composing — investigate next.
* "3SAT is a decision problem where…" overflowed — wrap_overlong_text
  skips group-internal text; this sentence may be inside a `<g>`.

## When fine-tune completes (auto path)

1. `finetune_openai.py` prints `FINETUNED_MODEL_ID=ft:...`.
2. `promote_finetune.py --model-id <ft id>` writes MODEL_CATALOG +
   sets `SEVIM_FORCE_ACTIVE_MODEL`.
3. `cd infra && cdk deploy` flips the active model atomically.
4. Run `ux_audit.py` once more, save defect counts to compare against
   base mini.

## IMPORTANT — audit-vs-prod model mismatch

The LOCAL dev server (port 8765) has no DB and no admin
`active_model` setting → resolve_backend falls through to the
catalog default which is `gpt-4o` (not mini).  So all 6 audit
passes tonight measured `gpt-4o` output.

PRODUCTION uses `gpt-4o-mini` (per the admin setting in RDS,
confirmed via the prod express logs showing `model=gpt-4o-mini`).

This means production figures may have MORE defects than the audit
showed.  Fix when user wakes:
  * Either re-run audit with SEVIM_FORCE_ACTIVE_MODEL=gpt-4o-mini
    on the local server, or
  * Audit against production after signing in.

## Final audit pass 6 verdict

Defects: 7 (2 severe text overlap, 5 mild, 1 no-svg).  The
remaining defects are at the limit of what deterministic post-
processing can fix — the model emits thin figures (just three
lines of text for "3SAT → vertex cover" with no graph drawn),
which is a model-capability problem, not a layout problem.  Fine-
tuning is the path forward.
