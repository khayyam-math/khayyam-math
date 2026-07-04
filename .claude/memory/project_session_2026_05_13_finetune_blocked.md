---
name: 2026-05-13 overnight — OpenAI fine-tune BLOCKED (platform sunset)
description: Overnight fine-tune plan died at 04:48 when OpenAI returned 403 training_not_available — self-serve fine-tuning is being shut down org-wide. Corpus (3395 ex) is ready; need a new path on wake.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---

## TL;DR for wake

- Corpus generation **finished cleanly** at 04:16:35 → 3395 examples
  in `data/distill/teacher_v6_mini.jsonl` (2345 clean + 1042
  corrected, 130 fail, 1537 rejected by deterministic critic over an
  11857-second run).
- OpenAI fine-tune **cannot run**. The org returns 403
  `training_not_available`:
  > "OpenAI is winding down the fine-tuning platform and your
  >  organization is no longer able to create new fine-tuning
  >  training jobs."
  Reference link in error: `developers.openai.com/api/docs/deprecations#update-to-openais-self-serve-fine-tuning`
- File DID upload successfully (file-SuxowzxDtfW1Qtm391f4ss, 61MB)
  — only the **job creation** step is blocked.
- Production is still on `gpt-4o-mini` via the admin RDS setting
  (no force-active-model env var was deployed).
- All deterministic layout passes from yesterday's audit loop are
  in `main` (autofit shrink, compose-transform reflow, clamp passes,
  text wrap, group obstacles).

## Where the artefacts live

| Artefact                             | Path                                                                                 |
|--------------------------------------|--------------------------------------------------------------------------------------|
| Raw corpus                            | `data/distill/teacher_v6_mini.jsonl`                                                |
| Cleaned (messages-only) corpus        | `data/distill/teacher_v6_mini.openai.jsonl`                                         |
| Generator run log                     | `/tmp/teacher_corpus.log`                                                            |
| Fine-tune attempt log (with 403)      | `/tmp/auto_finetune.log`                                                             |
| Uploaded OpenAI file id (orphan)      | `file-SuxowzxDtfW1Qtm391f4ss`                                                        |
| Finetune script (unchanged)           | `scripts/finetune_openai.py`                                                         |
| Promote script (unchanged)            | `scripts/promote_finetune.py`                                                        |

## Options for the next session

1. **Qwen LoRA on the 5090, same corpus** — the path used for
   v1-v4. The user explicitly asked earlier: "can we use the same
   data?" — answer is yes; the JSONL is in the v1-v4 format. But:
   Qwen serving on L4 was 16 t/s in prod, which is why we flipped
   to `gpt-4o-mini` in the first place. A new LoRA improves figure
   quality but does NOT fix serving latency.

2. **Few-shot / RAG over the corpus** — embed the prompt, retrieve
   2-3 nearest "good" examples from the 3395, prepend as in-context
   examples to the gpt-4o-mini system message. Cheaper, no
   re-training, runs in production today. Likely smaller quality
   lift than fine-tuning but real.

3. **Anthropic / Google fine-tune** — neither offers self-serve
   fine-tuning for the relevant models. Off the table.

4. **OpenAI enterprise/dedicated** — would need a sales contact;
   not actionable autonomously.

## What I did NOT do autonomously

- I did NOT kick off a Qwen LoRA train — that's an architectural
  decision (the prod runtime would have to change). User should
  pick on wake.
- I did NOT delete the orphan OpenAI file upload — it sits in
  storage but costs effectively nothing.
- I did NOT modify `MODEL_CATALOG` or `SEVIM_FORCE_ACTIVE_MODEL`
  — there is no fine-tuned model to promote.

## Earlier audit-loop status (still valid)

6 audit passes brought the defect count from 122 → 7. The remaining
defects are at the limit of deterministic layout post-processing
(model emits "thin" figures — e.g. only three lines of text for
"3SAT → vertex cover" instead of a real graph). Fine-tuning was the
proposed next step; with that blocked, the layout pipeline is the
ceiling for stock `gpt-4o-mini`.
