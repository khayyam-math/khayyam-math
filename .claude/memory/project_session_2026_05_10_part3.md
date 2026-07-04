---
name: "2026-05-10 evening — distillation v3 + Khayyam Math rebrand"
description: Continuation after midday compact — shipped PRs 13-20 (typo redirect, conversational chat, landing+SEO, OpenAI TTS, mobile MemoryError fix, refinement narration delta, explicit-play, Khayyam Math rebrand + contact form + ToS + cookie banner, cost cap raise) and started PR21 (headless self-distillation pipeline running on the local 5090).
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
## Live status — three jobs in flight as of session end

| Job | Background ID | Status |
|---|---|---|
| **v3 LoRA training** on combined corpus (128 examples) | b7x7qqtux | running, ~step 18/64, 4.5s/step, ~3 min remaining; GPU 100%, 28.9/32 GB VRAM |
| **Synthetic top-up** (resume — fills 66 prompts that failed first pass) | bcwk73uyz | running, 5/62 done |
| Reference top-up | bcpvgz4m7 | ✅ done — corpus now 34 rows across 15 domains |

Last scheduled wakeup: 18:58 — will check training results, compute losses, and either kick off judge or v3.1 retrain on enriched corpus.

## All PRs shipped this session (13-20 ALL LIVE on https://khayyammath.com)

| PR | Effect |
|----|--------|
| **13** | Typo-domain redirect: `khayyamath.com` (one M) → 301 → canonical `khayyammath.com`. CDK now takes `SEVIM_REDIRECT_DOMAINS=` (comma-separated); per-domain ACM cert + Route 53 A-alias + ALB host-header listener rule. |
| **14** | Conversational chat. Express system prompt no longer forces `sevim_express` on every turn — chat-only replies for follow-up Q&A about the existing figure. Also dropped the unconditional `"And now please look at the diagram."` transition; only prepended when `transition_text` is explicitly set. |
| **15** | Public landing page at `GET /` (`service/static/landing.html`). SEO: `<title>`, meta description+keywords, canonical, Open Graph, Twitter Card, JSON-LD `WebApplication`. `/robots.txt` allow / + disallow `/studio` `/canvas/*`; `/sitemap.xml` lists / + /contact + /terms. |
| **16** | OpenAI `tts-1-hd` voice replacing piper. `sevim/narrate.py` got `_tts_backend()` selector with `auto` (prefer OpenAI when key present). |
| **17** | Mobile canvas-not-showing fix — bundle of three sub-fixes:<br>• **Chunked WAV read** (`readframes(65536)` loop) — OpenAI's WAV header has bogus `nframes=2147483647` (INT32_MAX placeholder); `readframes(2147483647)` was pre-allocating 4.3 GB → MemoryError on 2 GB Fargate. Read in chunks instead.<br>• **Parallel TTS** via `ThreadPoolExecutor(12)` — 11 phrases drop from ~22s sequential → ~3.7s.<br>• **SSE `ping=15`** keepalive on the chat EventSourceResponse so long tool calls don't get cancelled.<br>Plus diagnostic logging in `studio/app.py` chat-loop tool-failure path: `repr(exc) + traceback.print_exc(file=_sys.stderr)`. |
| **18** | Refinement narration emits ONLY new phrases on follow-ups. Express system prompt + `_build_user_content` REFINEMENT MODE block now make it explicit: SVG continues to keep all prior elements; `narration` field contains ONLY phrases describing this turn's change. Verified end-to-end via `/tmp/sevim-ux/two_turn.py`: turn-2 narration was 2 phrases / 6.86s with zero overlap with turn-1's 8 phrases / 38.45s. |
| **19** | Two fixes:<br>• **Explicit-play audio.** Removed document-wide `click/keydown/touchstart` autoplay listeners + `intro.canplay` autoplay attempt + `tryPlayChain()` calls in state-update path. Audio now ONLY starts when user clicks the header `▶ Play narration` button (always visible once the WAV loads) or the corner overlay pill.<br>• **Question-specific first phrase.** Express prompt got "FIRST NARRATION PHRASE" section banning generic openings (`Now let's…`, `Let's see…`, `First, let's…`, `OK so…`). Verified: turn-1 first phrase is now `"In any triangle, the three interior angles always add up to π radians — here's why."` |
| **20** | Khayyam Math rebrand:<br>• User-facing rename Sevim → Khayyam Math (landing, login, studio header/empty-state, canvas viewer header, magic-link email). Internal Python module names, AWS env vars (SEVIM_*), CDK stack name, LLM prompts stay as-is.<br>• `/contact` page with name/email/message form + math captcha (HMAC-signed token, no third-party JS) + SES send to `gradersystem@gmail.com`. End-to-end verified working.<br>• `/terms` page with 11-section ToS tailored to live-diagram-tutor service. UAE governing law.<br>• Cookie acceptance banner across landing + studio + login + contact + terms. |
| **cost-cap raise** | `SEVIM_COST_DAILY_MAX_USD=10.00` added to ECS task env (was default $1). User was hitting $1.05 cap; new cap is $10/day per session_id. ~100-150 turns/day at the real per-turn cost. |

## PR21 — Headless self-distillation pipeline (in progress)

Goal: train Qwen LoRA locally on the 5090 using gpt-4o-mini as the teacher; replace OpenAI in production once the local model judges as good as gpt-4o for figure generation.

### Infrastructure shipped this session
- `scripts/teacher_prompts.py` — 164 diverse prompts covering 15 domains (geometry, calc, linear algebra, set theory, discrete, number theory, probability, algorithms, complexity, LP, physics, statistics, trig, solids, complex analysis, game theory, info theory). Many reference textbook style explicitly: "in the style of Euclid's Elements I.32", "(Strang style)", "(CLRS pseudocode + figure)", "(Spivak / Apostol style)".
- `scripts/reference_figures.py` — 31 trusted Wikipedia/Commons figures across all 15 domains, each with a citation (e.g. "Euclid, Elements, Book I, Proposition 47", "Bayes' theorem, Bertsekas/Tsitsiklis §1.4").
- `scripts/generate_teacher_corpus.py` — async generator: prompt list → gpt-4o-mini express call → JSONL training rows. Captures clean rows + repair pairs (5-msg conversations: user → bad_assistant → critique → good_assistant). Resumable (skips prompts already in output JSONL).
- `scripts/generate_reference_corpus.py` — vision-grounded variant: fetches a reference figure from Commons (with API resolver + UA header for Wikimedia compliance), shows it to gpt-4o-mini, asks for an SVG that visually matches + textbook-style narration that cites the source. Falls back to text-only-with-citation when image fetch fails.
- `studio/express.py` got REFERENCE STYLE section in `_EXPRESS_SYSTEM` listing trusted textbooks per domain (Euclid, Spivak, Strang, Axler, Rudin, Concrete Math/Knuth, CLRS, Bertsekas, Munkres, Dummit&Foote, Hardy&Wright, Sipser, Feynman/Griffiths/Goldstein) + NARRATION TONE section requiring textbook author voice (no chatbot colloquialisms, lead with named theorem, standard read-aloud notation).
- `scripts/train_lora.py` already existed (TRL/PEFT against Qwen2.5-7B-Instruct base). Hyperparameters for v3: r=8, alpha=16, lr=1e-4, 2 epochs, max_seq_len=6144, bf16. Conservative settings for small dataset; v4 will use r=16, alpha=32, lr=2e-4, 3 epochs.

### Local artifacts (paths on this machine)
- Combined corpus: `~/.local/share/sevim/distill/teacher_v3_combined.jsonl` (128 rows at training start)
- Synthetic: `~/.local/share/sevim/distill/teacher_v3_smoke.jsonl` (98 clean + 5 corrected, growing via top-up)
- Reference: `~/.local/share/sevim/distill/teacher_v3_refs.jsonl` (34 rows, all 15 domains)
- v3 LoRA output: `~/.local/share/sevim/loras/qwen_lora_v3/`
- Training log: `/tmp/qwen_v3_train.log`
- Top-up logs: `/tmp/teacher_v3_smoke_topup.log`, `/tmp/teacher_v3_refs_topup.log`
- ML deps installed: torch 2.11+cu130, transformers, peft, trl, bitsandbytes, accelerate, datasets

### Cost so far
- Synthetic gpt-4o-mini calls (164 prompts × ~$0.0018/each, with retries): ~$0.30
- Reference gpt-4o-mini vision calls (~30 figures × ~$0.001 with image): ~$0.05
- Total OpenAI spend on v3 corpus: **~$0.35**

### v4 plan — 2000-5000 prompts mixing synthetic + real-data
**User explicitly asked for:**
1. Mix in real textbook figures, not just Commons
2. Use legal sources only (ESLII, ISLR/ISLP, OpenStax, Strang on MIT OCW, Bishop PRML free PDF, Goodfellow/Bengio/Courville Deep Learning, Trefethen, public domain classics)
3. Sipser/Spivak/Apostol/Concrete Math/Rudin/Munkres/Dummit&Foote: cite only, do NOT extract figures (paid books, no free PDFs)
4. Picture extraction itself uses gpt-4o-mini (per user instruction)

**Three scripts to build (NOT YET BUILT — for next session):**
1. `scripts/download_textbooks.sh` — wget the legal PDFs from official author URLs.
2. `scripts/extract_textbook_figures.py` — `pymupdf` (`fitz`) renders each page → gpt-4o-mini vision identifies figures, returns (bbox, description, suggested user prompt). Crops figures, saves locally.
3. Augment `generate_reference_corpus.py` to accept local file paths (not just Commons URLs).

Plus: `scripts/discover_commons_figures.py` to walk Commons categories (Mathematical_diagrams, Geometric_proofs, Trigonometry, Linear_algebra, etc.) via the API and auto-build a 500+ entry list with auto-generated prompts.

Final v4 corpus target: ~3000 synthetic + ~1000 real-data references = **4000 examples, ~$8 total**.

### Resume next session
1. Check the three jobs (b7x7qqtux training, bcwk73uyz synth top-up; bcpvgz4m7 already done).
2. If v3 trained successfully, kick off `scripts/judge_lora_variants.py` against base Qwen.
3. If v3 looks good, build the textbook-figure-extraction scripts described above, run them on ESLII + Strang + ISLR + OpenStax to harvest ~500-1000 real figures.
4. Generate v4 corpus (parametric expansion of seed pool + gpt-4o-mini-generated prompts + extracted textbook figures = ~4000 examples).
5. Train v4 with hyperparameters r=16, alpha=32, lr=2e-4, 3 epochs.
6. Judge v4 vs v3 vs base Qwen.
7. If v4 wins by ≥1.0 points → it becomes the new incumbent; write `winning_lora.json` to `s3://sevimstack-lorabucket-…/winning_lora.json`.
8. (Future) deploy a vLLM serve job (g4dn.xlarge or local 5090 via tunnel) loading v4 LoRA, flip `SEVIM_VLLM_URL` Secrets Manager value to point at it. OpenAI cost drops from ~$15k/mo to ~$1.5k/mo at 10k turns/day.

## Cost projections (verified math, kept for reference)

At 100 users × 100 questions/day = **10,000 turns/day**:

| Stack | Monthly | Per user |
|---|---|---|
| Current (gpt-4o + tts-1-hd) | $14,540 | $145 |
| gpt-4o-mini + tts-1-hd | $7,850 | $79 |
| Gemini 2.5 Flash + tts-1-hd | $8,840 | $88 |
| gpt-4o-mini + tts-1 | $4,250 | $43 |
| **Self-host Qwen + Google TTS Standard** | **$1,600** | **$16** |
| Self-host on user's 5090 + Cloudflare Tunnel + Google TTS Std | ~$165 | $1.65 |
| Self-host on 5090 + local TTS (piper/coqui) | ~$165 (just AWS) | $1.65 |

GPU cost is FLAT (always-on EC2 g4dn.xlarge ~$400/mo); rest scales with traffic. TTS char count dominates variable cost. Self-hosting becomes cheaper than API only above ~50,000 turns/day.

## Lessons / gotchas worth remembering

- **`--frozen` uv sync silently ignores deps not in `uv.lock`.** Run `uv lock` after every `pyproject.toml` change.
- **CDK `DockerImageAsset` cache reuse** when a previous deploy is killed mid-flight: it can publish a partial image to ECR, then the next `cdk deploy` reuses that wrong image because the asset hash matched. Cache-bust by appending `# build-rev: $(date +%s)` to any source file.
- **OpenAI TTS WAV bogus header**: `nframes=2147483647` (INT32_MAX placeholder). Use chunked `readframes(65536)` loop, not `readframes(getnframes())`.
- **ECS `Secret.from_secrets_manager` injects at task BOOT, not on each request.** After updating a secret, MUST `force-new-deployment` to surface the new value.
- **AWS Route 53 domain registration for UAE registrants** must NOT include `State` or `ZipCode` fields.
- **SES `Source` parameter**: chokes on shell-mangled display-name format. Use plain `email@domain` if Secrets-Manager-stored value goes through any quoted CLI step.
- **CDK Node 18** prints `Found errors` to stderr from a deprecation notice even when synth/deploy succeed; trust the exit code, not the text.
- **`asyncio.CancelledError` and bare exceptions have empty `str()`.** Never log just `str(exc)` in error paths; always use `repr(exc)` and a traceback.
- **Wikimedia rejects requests without a meaningful User-Agent**; use `Khayyam-Math-Distillation/1.0 (https://khayyammath.com; arash_kermani@yahoo.com) httpx` or similar.
- **Wikimedia thumb URLs aren't predictable** (MD5-derived subdirectories); use the API to resolve `File:NAME` → live thumb URL instead of hand-rolling URLs.
- **OpenAI reference-mode trick**: when image fetch fails, fall back to text-only mode with the citation — the model still produces a citation-anchored figure from internal knowledge.
- **Don't use `wait_until="networkidle"` on canvas viewer** — SSE EventSource keeps the network busy forever, networkidle never resolves. Use `domcontentloaded`.
