---
name: 2026-05-10 night — UAE IP filing + v4 max-data corpus + overnight training
description: Long evening session (UAE IP package built and most-compiled, v4 corpus at ~3.8K/5.5K with synth still running, LoRA training queued for autonomous overnight kick)
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Picked up the v4 fine-tune at the start of the evening (synth round-1 was
running on PROMPTS_V4 / 1,545 prompts at concurrency 8). User then
pivoted to the **UAE Ministry of Economy & Tourism IP-registration
package**, which absorbed most of the evening:

* Built the package under `uae_ip_registration/` with shared LaTeX
  preamble (DejaVu fonts, Noto Naskh Arabic for the title \arName, listings
  with Python+JSON languages, colortbl via xcolor[table]).
* 15 PDFs total compiled (335 pages, 4.1 MB), assembled by `build_all.sh`.
* MoET filing route confirmed: service description at
  `https://www.moet.gov.ae/en/w/register-compilations-`, online portal at
  `https://eservices.moec.gov.ae/IP/IPRegistration`, fee AED 50,
  processing 3 working days.
* Per user clarification: software is **sole-author Arash** — Rita Zgheib
  is on the Zenodo SeVim preprint but never participated in the registered
  software. All Rita / preprint references stripped; supporting evidence
  is now Git timestamps + live deployment.

Mid-session the user noticed "vllm unreachable" badge in the local
Studio. Diagnosed as: MCP server pid 3813 started 2026-05-10 07:08:41
*before* `.env` was last touched at 07:23:34, so its cached env lacks
`SEVIM_VLLM_URL`. User said **do not restart** — let fine-tune finish
first; document polish deferred (see `project_uae_ip_deferred.md`).

**Pipeline state at user-bedtime (~23:55 local):**

* Synth round-2 (`scripts.expanded_prompts_v5:PROMPTS_V5`, 4,017 prompts,
  concurrency 8, model gpt-4o-mini): pid 35109 alive, ~2,261 / 4,017
  (56 %), rate ~10 prompts/min, ETA ~02:50.
* Textbook refs round-2: **DONE**, 1,528 / 1,530 rows.
* Goodfellow PDF replaced (full 800-page version), figures extracted
  (+308 figures), included in textbook_figures.jsonl.
* Combined corpus at convergence: ~5,545 examples (4,017 synth + 1,528
  refs).

**Autonomous overnight plan:**

1. ~02:50 — synth converges. Merge synth + textbook refs into
   `~/.local/share/sevim/distill/teacher_v4_combined.jsonl` (cat +
   shuffle, dedup by `meta.prompt`).
2. Kick v4 LoRA training with the **stable config from the original loop
   instruction** (not the experimental rank-32 plan, since user is
   asleep and asked for safe handling):
   ```
   .venv/bin/python scripts/train_lora.py \
       --dataset ~/.local/share/sevim/distill/teacher_v4_combined.jsonl \
       --out ~/.local/share/sevim/loras/qwen_lora_v4 \
       --epochs 3 --lr 2e-4 --rank 16 --alpha 32 --max-seq-len 6144
   ```
   ETA ~3-4 h on the 5090.
3. Periodic 1-h wakeup checks during training: smoke-test that the pid
   is still alive and the train log is progressing (`Step / Epoch`
   updates). Don't intervene unless OOM / crash.
4. ~07:00 — training completes. Run judge:
   `python scripts/judge_lora_variants.py` (compares v4 vs v3 vs v2 vs
   base on the 20-prompt rubric).
5. Write a brief overnight-summary note to memory and present it to the
   user when they wake up.

**Files of record:**

* Corpus jsonl: `~/.local/share/sevim/distill/teacher_v4_synth.jsonl`,
  `~/.local/share/sevim/distill/teacher_v4_textbook_refs.jsonl`.
* Logs: `/tmp/teacher_v4_synth_r2.log`,
  `/tmp/teacher_v4_textbook_refs_r2.log`.
* Last commit: `a12a4f5` (v0.6, 2026-05-10).
* MCP server pid 3813 — DO NOT restart (loop instruction).

**Deferred for next session:** `project_uae_ip_deferred.md` lists the 4
UAE-package polish items (verbatim excerpt, source-code-explanation
truncation, screenshots for chat surface + canvas viewer, mcp_server
stale-env warning).
