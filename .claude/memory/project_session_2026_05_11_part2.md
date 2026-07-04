---
name: 2026-05-11 evening — Qwen on AWS + admin page + reviewer pipeline + next-step SVG streaming
description: Continuation of 2026-05-11; v4 LoRA shipped to AWS g6.xlarge; admin page deployed; reviewer pipeline rebuilt to be model-agnostic. Immediate next task: progressive SVG streaming so figures appear incrementally instead of all-at-once.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Full afternoon/evening picking up from the morning v4 fine-tune.  Site
state and the next concrete task captured here for the post-compaction
session.

## What's live in production right now (khayyammath.com)

**EC2 + Qwen vLLM** (cdk deploy -c enable_qwen=1 succeeded):
* Instance: `i-00da897e2d2b3f468`, g6.xlarge, **on-demand** (~$0.80/hr,
  ~$576/mo always-on).  Spot was blocked by CFN's
  `AWS::EC2::Instance` not accepting `InstanceMarketOptions`.
* Private IP: `10.0.2.235`; vLLM listening on `:8000`.
* Fargate env: `SEVIM_QWEN_VLLM_URL=http://10.0.2.235:8000/v1`.
* Models served: `Qwen/Qwen2.5-7B-Instruct` (base) +
  `qwen_lora_v4` (in-house LoRA from
  `s3://sevimstack-lorabuckete14b3a5d-yhibapco1six/qwen_lora_v4/`).
* **Pins applied LIVE on the instance via SSM, NOT YET in CDK
  user-data** (will be wiped if the instance is replaced; see
  next-session todo):
    - `transformers==4.46.3` (was pulling 4.50+ which removed the
      `all_special_tokens_extended` attribute vllm reads)
    - `xgrammar==0.1.11` (was pulling 0.2.0 which removed
      `TokenizerInfo.from_huggingface`)
    - `--chat-template /opt/loras/qwen_lora_v4/chat_template.jinja`
      (transformers 4.46 no longer auto-loads Qwen's template)
    - `--max-model-len 16384` (was 6144, which rejected the express
      pipeline's `max_tokens=8192` requests)

**Admin page** at `/studio/admin`:
* E-mail whitelist via `SEVIM_ADMIN_EMAILS` env var
  (`arash.kolankeh@cud.ac.ae`, `arash_kermani@yahoo.com`).
* Non-admin requests get a plain 404 (URL undiscoverable).
* Backed by a new `settings` table in RDS Postgres.
* Currently flipped to `active_model=gpt-4o-mini` via the admin API
  (Qwen on the L4 is ~16 t/s; ~90 s per generation; users were
  giving up and refreshing).  Audit row says
  `updated_by='arash.kolankeh@cud.ac.ae'`.

**Model catalog** (in order; `default` = the marked default
fallback when admin setting is missing/unavailable):
1. `qwen_lora_v4` — in-house, marked default, **available** but
   **slow on L4** (~16 t/s, ~90 s per turn).
2. `gpt-4o` — OpenAI, fast.
3. `gpt-4o-mini` — OpenAI, ~15× cheaper than gpt-4o, **currently the
   admin-selected active model**.
4. `qwen_base` — same instance, no LoRA.

`get_active_model()` resolution chain:
1. Admin setting if available
2. `default=True` entry if available
3. First `available=True` entry
4. The marked default regardless (for deterministic logging)

A 30-second cached reachability probe (`_qwen_lora_vllm_reachable()`)
gates Qwen's `available` flag so a bootstrap / spot-reclaim / crash
makes the catalog flip Qwen to unavailable and traffic falls through
to OpenAI automatically.

**Per-model telemetry**: `turns`, `canvases`, `repairs` all carry a
`model_id` column with `DEFAULT 'gpt-4o'`.  ALTER TABLE ADD COLUMN
IF NOT EXISTS migrates production RDS on first task boot of the new
image.

**Reviewer pipeline rebuilt**: `_vision_review()` now consults
`_review_config()` exclusively (ignoring the generator's URL/model).
Four env vars steer it:
* `SEVIM_REVIEW_MODE` — `text` (default), `vision`, or `off`
* `SEVIM_REVIEW_MODEL` — default `gpt-4o-mini`
* `SEVIM_REVIEW_URL` — default `SEVIM_VLLM_URL` (OpenAI)
* `SEVIM_REVIEW_KEY_ENV` — default `OPENAI_API_KEY`

Default: gpt-4o-mini reviews **SVG-as-text** (no PNG rasterisation,
no image_url block, ~15× cheaper than the old gpt-4o-vision setup,
works for any generator backend).  Switch to PNG-vision review by
setting `SEVIM_REVIEW_MODE=vision` and `SEVIM_REVIEW_MODEL=gpt-4o`.

**Deterministic structural critic** runs BEFORE the LLM review and
catches three failures the vision LLM cannot reliably see from a
rendered PNG:
* `narration_highlight_id_missing` — phrase highlight ids that
  don't appear in the SVG
* `all_highlights_empty` — every phrase has `highlight=[]`
* `vertex_labels_missing` — graph with id'd circles but < text count

11 new tests in `tests/test_structural_review.py`.

**Cookie banner removed entirely** — was getting stuck on the "Got
it" click due to a `hidden` vs inline `display:flex` CSS conflict,
plus blocking chat input on short screens.  We set only one
strictly-necessary cookie (`sevim_auth`) so no consent banner is
required under UAE PDPL / EU GDPR.  Disclosure stays on `/terms`.

**Chat-UI placeholder**: the `→ sevim_express()` trace was replaced
with **`Thinking…`** which disappears as soon as the canvas updates.
Failures show a clean `"Couldn't generate that figure — <reason>"`
line instead of the raw exception class.

**Cookie banner CSS for highlights** — viewer now matches both
wrapped (`<g id="v1"><circle/></g>`) and flat (`<circle id="v1"/>`)
patterns; flat was the GPT-4o default and got only a drop-shadow
halo before.

**Text-only generator handling**: when the active model is
`qwen_lora_v4`, `qwen_base`, or `Qwen/Qwen2.5-7B-Instruct`, the
express path strips image_url blocks from the messages (Qwen 2.5-7B-
Instruct is text-only and rejected refinement-turn PNGs with
"Unknown image model type: qwen2").  Retry path inlines the prior
SVG as text instead of as a rendered PNG.

## Commits today (sevim-plugin repo)

* `352e356` — reviewer rebuild (text-mode + gpt-4o-mini default)
* `0868158` — text-only Qwen handling (refinement + retry + review routing)
* `dc254b6` — CSS highlight fix + all_highlights_empty critic
* `675ded0` — structural critic for highlight-id + vertex labels
* `32604ba` — "Thinking…" UI placeholder
* `2bb07e4` — on-demand Qwen CDK (dropped spot)
* `9ccf51a` — Qwen reachability probe with TTL cache
* `acfe80e` — cookie banner JS fix for /studio/auth/login
* `d341dd2` — remove cookie banner entirely + responsive breakpoint 820→640
* `94202d1` — admin page + per-model telemetry + Qwen CDK scaffold

## Files left in /tmp from earlier UAE filing work

Still on disk for tmpfiles re-upload if needed:
* `/tmp/khayyam_math_application_forms_and_screens.pdf` (8 pages)
* `/tmp/khayyam_math_uae_ip_package.zip` (5.97 MB)
* `/tmp/khayyam_math_connection_to_external_services.pdf` (5 pages)

The earlier tmpfiles.org links (37564891, 37564901, 37565579) may
have expired by now (1 h TTL); re-upload with curl if needed.

## NEXT IMMEDIATE TASK: progressive SVG streaming

User explicitly asked for this and approved "save state, compact,
then do it."  Expected outcome:
* **Time-to-first-visible-figure** drops from ~5 s → ~0.5 s.
* Total wall clock unchanged (review still needs the full SVG).
* Educational fit: figures grow on the canvas as the model emits
  them, matching the "see math come alive" promise.

**Implementation plan** (estimated effort: 4–6 hours):

1. **Server side, `studio/express.py`**:
   * Add `stream=True` to the OpenAI chat-completions POST in
     `express_figure()`.
   * Replace the single `await client.post(...)` with an SSE-style
     iteration over the streaming response body.
   * Buffer the streamed token deltas while running a *lightweight
     streaming JSON parser* (or a regex hack on the partial text)
     to extract the `svg` field's value as soon as it begins
     emitting.  The schema declares fields in the order
     `[svg, narration, title]` so SVG comes first.
   * Yield partial-SVG callbacks to the caller.  Suggested
     signature: pass an `on_svg_chunk: Callable[[str], None] | None`
     parameter to `express_figure()`; default `None` keeps the
     existing call path working.
   * Continue accumulating until the full JSON arrives, then run
     the existing structural review + LLM review + retry loop.

2. **Studio chat surface, `studio/app.py`**:
   * In `_stream_vllm_chat` / the tool-execution path that runs
     `express_figure`, supply an `on_svg_chunk` callback that emits
     a new SSE event named `svg_chunk` with the partial SVG
     contents (or an incremental delta — the simpler option is a
     full-snapshot replace each time, since SVGs aren't huge).
   * Existing `tool_call` / `tool_result` events continue to fire
     at the start and end.

3. **Browser chat handler, `studio/static/studio.html`**:
   * Add an `else if (evt === 'svg_chunk')` branch in the stream
     reader that forwards the partial SVG to the canvas iframe via
     `postMessage({type:'partial_svg', svg: ...})`.

4. **Canvas iframe, `service/static/canvas.html`**:
   * Add a `window.addEventListener('message', ...)` handler for
     `partial_svg` that updates `#stage.innerHTML = svg` directly.
   * The browser will render partial SVG gracefully — unclosed
     elements just don't paint, and they update on the next chunk.
   * Disable narration auto-play during partial streaming; only
     start audio once the full canvas arrives via the existing
     final `tool_result` event.

5. **Parser**: the simplest viable approach for extracting the SVG
   field from a streaming structured-JSON response is a regex
   buffer:
   ```python
   import re
   _SVG_FIELD_RE = re.compile(r'"svg"\s*:\s*"((?:[^"\\]|\\.)*)$')
   _SVG_END_RE   = re.compile(r'^(.*?[^\\])"\s*,')
   ```
   Track whether we're "inside" the svg field; on each new chunk
   append to the partial buffer, then if a non-escaped closing
   quote appears, snip the buffer and stop streaming.  Decode the
   JSON-escaped string (`\"`, `\\`, `\n` → `"`, `\`, `\n`) before
   emitting to the client.

6. **Tests**:
   * Add `tests/test_express_streaming.py` covering the parser:
     feed it a stream of mock OpenAI SSE lines containing a JSON
     payload split mid-SVG; assert it emits the correct partial
     SVG strings and the correct final state.
   * Make sure the non-streaming code path (when
     `on_svg_chunk=None`) still works identically.

7. **Deploy**:
   * cdk deploy from infra/.  Image rebuild + Fargate rolling
     update, ~5 min.  No changes to Qwen-side.

## Other immediate to-dos (lower priority than streaming)

* Roll the on-instance vLLM pins (`transformers==4.46.3`,
  `xgrammar==0.1.11`, chat-template flag, max-model-len=16384) into
  the CDK user-data so a fresh EC2 boot doesn't replay the dance.
* If GPU cost is annoying you: `cdk deploy` without `enable_qwen=1`
  to tear down the instance — admin page still works, Qwen
  options just show "not configured."
* AWQ-quantize the Qwen weights for ~2.5× speedup (separate
  v4-AWQ adapter directory in the same S3 bucket; CDK user-data
  swaps the `--model` argument).

## Open items still deferred

* `project_uae_ip_deferred.md` — UAE IP package polish (verbatim
  code excerpt, source-code-explanation truncation, real
  screenshots for chat + canvas viewer, mcp_server stale-env
  warning).  Low priority while the streaming work is in flight.
