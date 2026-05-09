# Sevim public deployment + data-driven improvement plan

Status: **planning + local foundation** (2026-05-09).
Target: phased rollout — local hardening → AWS staged → public.

## 1. Goals

1. Make Sevim accessible to outside users (initially friends + colleagues; later wider).
2. Capture every user prompt + the figure Sevim produced so we can:
   * Improve the system prompt (find what gpt-4o struggles with).
   * Improve the backend (latency hot-spots, audit pass rate by figure type).
   * Build a fine-tuning corpus for a smaller model (Qwen2.5-7B/14B) we can self-host.
3. Stay safe on a public surface: rate-limit, cost-cap, content-filter.
4. Don't lock in to a vendor — keep the data path portable so we can move from gpt-4o to a fine-tuned local model without changing the schema.

## 2. What "data" means here

For every user turn, we want to capture the full pipeline:

| Field | Why |
|---|---|
| session_id | Group turns into a conversation |
| ip_hash | Abuse / rate-limit key without storing PII |
| user_agent | Frontend bug correlation |
| timestamp | Ordering + sessionisation |
| user_prompt | The actual input — primary fine-tune signal |
| canvas_id | Link to the produced figure |
| prior_canvas_ids | Refinement chain — high-value training signal |
| svg | The figure produced (for vision-augmented fine-tuning, also rendered to PNG) |
| narration_json | Phrase-level highlight schedule (for narration fine-tuning) |
| n_phrases, retries_used | Audit difficulty |
| review_history | Vision-review verdicts; failure modes worth analysing |
| duration_s | Per-stage latency |
| accepted | Did the user move on without immediately re-prompting? |
| refined_within_seconds | Time to next user message — short means dissatisfaction |
| total_cost_usd_estimate | LLM bill |

The "accepted" + "refined_within_seconds" pair is the core quality signal. A figure the user **didn't refine within 60 s** is a positive training example. A figure followed by *"no, fix X"* is the negative — and the user's correction prompt is the gold corrective signal.

## 3. Safety surface (local + public)

Three layers:

1. **Rate limiting** — token-bucket per session_id, default 30 req/hr + 100 req/day. Lower for unauthenticated public.
2. **Cost guard** — track estimated dollar cost per session_id; hard-cap at $1.00/day for public users. Reject with friendly explanation when exceeded. Keeps a single user from running our gpt-4o bill into the hundreds.
3. **Content filter** — regex/keyword denylist for the obvious red flags (explicit content, attempts to extract system prompt, injection attempts). For production: layer OpenAI's moderation endpoint on top.

All three are off by default in dev (env vars `SEVIM_RATE_LIMIT=1`, `SEVIM_COST_GUARD=1`, `SEVIM_CONTENT_FILTER=1`). Public deployment turns them on.

## 4. Storage choices

**Local now:**
* SQLite in `~/.local/share/sevim/telemetry.db` — one file, queryable with `sqlite3` CLI, easy to back up.
* Canvas SVGs in the existing `~/.local/share/sevim/canvases/` dir.
* Audio WAVs same.

**AWS later:**
* RDS Postgres (or DynamoDB) for telemetry — same schema as SQLite, one mechanical migration.
* S3 for canvas SVGs + WAVs (the current per-canvas dir maps to an S3 prefix).
* CloudWatch for application logs.

The point of starting on SQLite is that the schema and access patterns translate to RDS without a rewrite.

## 5. Fine-tuning data pipeline

The telemetry DB is the source of truth.  An exporter walks it and emits a JSONL file in Qwen2.5's chat-format:

```json
{"messages": [
  {"role": "system", "content": "<the EXPRESS_SYSTEM prompt>"},
  {"role": "user", "content": "<user_prompt>"},
  {"role": "assistant", "content": "<json: {svg, narration, title}>"}
]}
```

**Filtering rules** for the fine-tune set:
* Only turns where the figure was `accepted` (no follow-up refinement within 60 s).
* Only turns where `retries_used == 0` (model nailed it first try — these are pristine training pairs).
* Or, more aggressively: only turns where the user *praised* explicitly ("perfect", "nice", "yes").

For the **negative-example** set (used to train a critic, or for DPO):
* Pairs of `(user_prompt, bad_svg, refinement_prompt, good_svg)` extracted from refinement chains.
* Direct preference signal.

A separate exporter format for **vision fine-tuning** (image → SVG):
* `{"image": <base64-png>, "label": <user_prompt>}` for the inverse task.
* `{"image": <prior-canvas-png>, "instruction": <refinement_prompt>, "output": <new-svg>}` for image-conditioned editing.

## 6. AWS architecture (target)

```
                        Route53
                           │
                           ▼
                       CloudFront         (static assets)
                           │
                           ▼
                          ALB
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          ECS task     ECS task     ECS task
          (uvicorn,    (uvicorn,    (uvicorn,
           studio)      studio)      studio)
              │            │            │
              ├────────────┴────────────┤
              ▼                         ▼
            RDS                       S3
          Postgres                 (canvases/
        (telemetry +              audio WAVs)
         sessions)
              │
              ▼
        Secrets Manager
       (OPENAI_API_KEY)

         CloudWatch (logs from all ECS tasks)
```

**ECS Fargate** for the runtime so we don't manage EC2 instances. Spec per task: 1 vCPU, 2 GB RAM (Studio is mostly I/O bound). Start with 2 tasks behind ALB; scale on CPU > 60% or request count.

**RDS Postgres** db.t4g.small for telemetry. ~$25/month. Easily upsizable.

**S3** standard for canvas dir. ~$0.023/GB/month + bandwidth. A canvas dir is ~100 KB; 10K canvases = ~1 GB = $0.02/month. Negligible.

**Secrets Manager** for OPENAI_API_KEY. ECS tasks pull at boot.

**Route53 + ALB + CloudFront** for the public URL (sevim.example.com). CloudFront caches /studio.html and /canvas/*/view (cache-busted via revision query string) so the LLM-emitted SVGs propagate to viewers cheaply.

**Estimated cost** at 100 active users / day, 5 prompts each:
* ECS Fargate (2 tasks × 24×30): ~$30/month
* RDS Postgres: ~$25/month
* ALB + CloudFront + S3 + Route53: ~$25/month
* OpenAI gpt-4o (500 prompts/day × $0.05 each × 30 days): ~$750/month
* **Total ~$830/month** dominated by gpt-4o cost.

The whole motivation for the fine-tuning pipeline is to replace gpt-4o with a self-hosted Qwen once we have enough data. At that point the OpenAI spend goes near-zero and total infra cost drops to ~$100-200/month plus a GPU node.

## 7. Migration path

1. **Local hardening (this commit)**: telemetry + sessions + rate limit + safety + fine-tune export, all toggleable, all tested.
2. **Local public test**: expose Studio on the local LAN with rate limits on; collect 100-500 turns from friends.
3. **First fine-tune attempt**: export the captured turns, run Qwen2.5-7B fine-tune on the user's existing GPU. Eval against gpt-4o on a held-out set.
4. **AWS staging**: deploy the same code stack to a single ECS task + RDS small + S3. Smoke test end-to-end.
5. **AWS production**: scale to 2+ ECS tasks, CloudFront, real domain, monitoring + alerts.
6. **Switch to fine-tuned model**: change `SEVIM_VLLM_URL` to a self-hosted vLLM endpoint (could be on a g5.xlarge EC2 with the fine-tuned Qwen).

## 8. Open questions / decisions deferred

* **Authentication**: simplest first launch is anonymous + IP-based rate-limit. Real fix is a magic-link email auth. Punted to Phase 2.
* **DSAR / GDPR**: if we go EU-public, need a way to delete a user's data on request. SQLite lookup by `ip_hash` makes this easy; needs a deletion endpoint.
* **Voice / piper TTS in production**: piper synthesis runs in the studio process today. For AWS we'd want to either bundle piper into the ECS image (works, ~200 MB) or call a managed TTS (cheaper compute, harder to tune voice). Lean toward bundling for v1.
* **Whisper transcription** for voice input: nice-to-have, not Phase 1.
