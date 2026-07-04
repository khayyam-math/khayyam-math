---
name: 2026-05-11 — v4 training overnight + Qwen-on-AWS scaffolding + admin page + cookie banner removal + fresh-screenshot IP package
description: Full-day continuation of the v4 fine-tune cycle plus a separate PR23/24 cycle that deployed admin-only model switching to khayyammath.com
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Full-day cycle, mostly autonomous. Highlights and end-of-day state:

**v4 fine-tune cycle (overnight 02:38 → 07:48):**

* Synth round-2 converged at 4{,}000 / 4{,}017 prompts (cut early to
  avoid retry-loop diminishing returns); textbook refs at 1{,}528.
* Merged into `~/.local/share/sevim/distill/teacher_v4_combined.jsonl`
  --- 5{,}528 deduplicated examples.
* Trained `qwen_lora_v4` (rank 16, alpha 32, lr 2e-4, 3 epochs,
  seq 6144, batch 1\,\*4) for 5\,h\,07\,min on the RTX 5090. Final
  train loss 0.057, mean-token accuracy 98.6\%.
* Eval round on the 20-prompt held-out set: **v3 mean 20.20/30
  vs v4 mean 19.45/30**. v4 wins 10 prompts to v3's 8 with 2 ties,
  and is **+2.05 points better conditional on producing valid output**
  (21.61 vs 19.56 over the 18 prompts where both produced SVGs).
* **Known v4 failure mode:** 2/20 prompts (eigendecomp 2x2, Venn
  A\,$\cup$\,B\,$\cap$\,C) trigger empty-SVG generations. v5 plan is
  to append \textasciitilde{}300 repair pairs targeting those two
  prompt families.
* Paper updated with v4 results table and pushed to
  `arashkermaniprojects/Khayyam` (commit `c8e20ca`).
* v4 adapter (155 MB) uploaded to
  `s3://sevimstack-lorabuckete14b3a5d-yhibapco1six/qwen_lora_v4/`.

**PR23 + PR24 (deployed to khayyammath.com):**

* **Admin page** at `/studio/admin`, e-mail-whitelisted via
  `SEVIM_ADMIN_EMAILS` (set to
  `arash.kolankeh@cud.ac.ae,arash_kermani@yahoo.com`). Non-admins
  get a plain 404, so the URL is undiscoverable.
* Page shows per-model usage roll-ups (24h / 7d / 30d / all-time)
  and a radio-button selector for the active backend. Selection
  persists in a new `settings` table and takes effect on the next
  chat request.
* Catalog has four entries: **`qwen_lora_v4`** (marked default,
  unavailable until the GPU instance is up), `gpt-4o`,
  `gpt-4o-mini`, `qwen_base`. End-users never see a selector;
  resolution happens server-side via `resolve_backend()` with the
  fallback chain: admin setting → marked default → first available
  → marked default regardless.
* Telemetry tables (`turns`, `canvases`, `repairs`) gained a
  `model_id` column with `DEFAULT 'gpt-4o'`; `ALTER TABLE ... ADD
  COLUMN IF NOT EXISTS` migrates pre-existing prod rows on boot.
* CDK construct for a g6.xlarge spot vLLM instance is in
  `infra/sevim_stack.py`, **gated behind `-c enable_qwen=1`** so
  default `cdk deploy` stays GPU-free. Not yet enabled.

**Cookie banner removed entirely.** Original banner had a
`hidden` vs inline `display:flex` conflict that kept it visible
even after a "Got it" click; the patched JS shipped but was
unreachable for cached browsers, and the banner was actively
blocking chat input on short viewports. Site uses only ONE
strictly-necessary cookie (`sevim_auth`) so no consent banner is
legally required (UAE PDPL, EU GDPR, UK PECR). Disclosure remains
on `/terms`.

**Responsive-layout fix.** Landing-page hero breakpoint moved from
`max-width: 820px` (which made partially-maximised desktop windows
look mobile) to `max-width: 640px`.

**Deploy timestamps for the day:**

* 16:11 --- first Fargate deploy (admin page + per-model
  telemetry + Qwen scaffolding + cookie-banner-fix attempt 1).
* 16:26 --- second deploy (cookie-banner-fix attempt 2 for the
  inline-HTML magic-link page that the first pass missed).
* 16:40 --- third deploy (cookie banner removed entirely +
  responsive breakpoint tightened). All three deploys succeeded
  through ALB rolling-update with zero downtime.
* MCP server pid 3813 on the dev box was **not restarted** at any
  point per the user's overnight instruction; its stale env still
  shows "vllm unreachable" but that's local-only.

**TTS / voice fallback (verified, no code change needed):**

* Production uses OpenAI `tts-1-hd` with voice `alloy`. The
  fallback to piper-tts (with `en_US-lessac-medium.onnx`,
  baked into the Docker image at `/opt/sevim/voices/`) is already
  implemented in `sevim/narrate.py`: any exception from the OpenAI
  call falls through to piper for that phrase. No edit needed.

**UAE IP registration package (re-cut with fresh screenshots):**

* `application_forms_and_screens.pdf` rebuilt with live screenshots
  of `/`, `/studio/auth/login`, `/terms`, `/contact` captured from
  khayyammath.com after the cookie-banner removal. Screens 3 and 4
  (chat surface + canvas viewer) remain wireframes --- both need an
  authenticated session and a live tutoring turn to render
  meaningfully.
* Final user-shippable artefacts dropped to `/tmp/` for remote
  download:
    - `/tmp/khayyam_math_application_forms_and_screens.pdf` ---
      788 KB, 8 pages, standalone forms/screens PDF.
    - `/tmp/khayyam_math_uae_ip_package.zip` --- 5.97 MB, the whole
      `uae_ip_registration/` directory bundled.

**Open items left for next session:**

* GPU deploy via `cdk deploy -c enable_qwen=1` (\textasciitilde\$216/mo
  worst-case spot). Code is ready; user wants this but the meter
  hasn't been started.
* Once GPU is up, `get_active_model()` will pick `qwen_lora_v4`
  automatically because it's the marked default; nothing else
  required to make Qwen the production default.
* The v4 known-failure repair pass (\textasciitilde{}300 prompt
  family-targeted repair pairs for eigendecomp + Venn-AuBnC) is
  the natural v5 experiment.
* `project_uae_ip_deferred.md` items (verbatim code excerpt,
  source-code-explanation truncation fix, mcp\_server stale-env
  warning) are still open --- low priority.

**Latest commits (sevim-plugin repo):**

* `d341dd2` --- remove blocking cookie banner from all pages
* `acfe80e` --- cookie banner fix: also handle /studio/auth/login banner
* `94202d1` --- admin page + per-model telemetry + Qwen-on-AWS scaffolding + cookie-banner fix
* `c8e20ca` --- v4 fine-tune results (paper repo, on Khayyam)
* `a12a4f5` --- v0.5 → v0.6: shipped publicly as Khayyam Math + headless self-distillation
