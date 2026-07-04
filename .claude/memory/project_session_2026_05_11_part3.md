---
name: 2026-05-11 night — PR25 progressive SVG streaming + UAE IP form_to_backend_connections.pdf
description: PR25 shipped — SVG streams chunk-by-chunk over a new svg_chunk SSE event so figures begin painting in ~0.5s. Also added a new UAE IP doc form_to_backend_connections.pdf for the "Explain how to connect with these services" field (was wrong file before — connection_to_external_services.pdf is for external services, this new one is form-to-backend wiring).
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---

## PR25 — progressive SVG streaming (commit 6bcb1de)

The express path no longer waits for the full LLM response before
painting the figure.  An OpenAI streaming chat-completions request is
issued (`stream=True`) and a small JSON-aware parser
(`_StreamingSvgExtractor`) pulls the value of the top-level `svg`
field out of token deltas as they arrive.  The schema declares
fields in property order `[svg, narration, title]` so the SVG comes
first; once its closing unescaped `"` is seen, the extractor flips to
the AFTER state and stops scanning.

### Plumbing

* `studio/express.py` — adds `_StreamingSvgExtractor`,
  `_stream_chat_completion`, and the `on_svg_chunk` parameter to
  `express_figure`.  Streaming only fires on the first attempt of a
  turn; retries fall back to the original `client.post(...)` path so
  a mid-correction wipe of a finished figure can't happen.
* `studio/app.py` — `_execute_tool` accepts `on_svg_chunk`; the chat
  loop creates an `asyncio.Queue`, launches the tool as a task,
  drains chunks while the task runs, and emits each as an SSE
  `svg_chunk` event with `{"svg": "<partial markup>"}`.
* `studio/static/studio.html` — adds the `svg_chunk` SSE branch which
  posts `{type: 'sevim_partial_svg', svg}` to the canvas iframe.
* `service/static/canvas.html` — listens for `sevim_partial_svg`,
  pauses any prior-figure narration (highlight IDs would mismatch),
  clears highlights, and sets `stage.innerHTML = svg` directly.
  Partial markup paints fine; the browser ignores unclosed elements.

### Tests

`tests/test_express_streaming.py` — 11 new tests:
* 9 unit tests on `_StreamingSvgExtractor` covering: opening match,
  mid-value split, JSON-escape decoding, backslash spanning chunk
  boundary, `"svg"` key split mid-name, post-close idempotency,
  unicode `\uXXXX` placeholder, value not-yet-quoted state.
* 2 integration tests on `_stream_chat_completion` using
  `httpx.MockTransport` with a canned SSE body.

Full suite still passes — 110/110 (matches pre-PR baseline).

### Deploy

Triggered via `cd infra && SEVIM_DOMAIN=khayyammath.com npx aws-cdk
deploy -c enable_qwen=1 --require-approval never` — same flags as
the PR24 deploy.  Note: there is no system-wide `cdk` binary; must
use `npx aws-cdk`.

### Expected behaviour for the learner

* Time-to-first-visible-figure on a second-or-later turn drops from
  ~5 s → ~0.5 s.
* First-turn (no iframe loaded yet) silently drops chunks — full SVG
  arrives via the existing `tool_result` event a moment later, no
  regression.
* Total wall-clock for a single express call unchanged (review still
  needs the full SVG).

## UAE IP filing — form_to_backend_connections.pdf

User caught a mismatch on "Explain how to connect with these
services*".  That field is asking how the FORMS of the app connect
to the BACKEND endpoints (frontend→server wiring), NOT how the app
connects to external services like OpenAI / AWS.  Built a new
6-page PDF `form_to_backend_connections.pdf` mapping each user-
facing form documented in `application_forms_and_screens.pdf` to
its FastAPI route, request payload, server-side handling,
persistent state, and any external service involved.  Uploaded as
http://tmpfiles.org/37594448/.

Existing `connection_to_external_services.pdf` stays in the package
but only for the earlier "Is the system connected to systems with
external databases using web services?" → yes question.

Also done in this batch:
* Replaced the Screen-3 wireframe in
  `application_forms_and_screens.pdf` with a real production
  screenshot (vertex-cover "show it with bigger graph" turn).
* Fixed the diamond/box overlap in `source_code_explanation.pdf`
  by pushing step 5's diamond down 6 mm; also added missing
  `\usepackage{amsmath}` (latent `\text` undefined-cs bug had been
  truncating that PDF to 2 pages).

Applicant Comment field — recommended single-line text:
"I confirm sole authorship of the work and sole ownership of the
copyright. The software is currently deployed and operational at
https://khayyammath.com; I am available to provide a live
demonstration or additional materials at the Ministry's request."

## Open items (lower priority)

* Roll on-instance vLLM pins (transformers==4.46.3,
  xgrammar==0.1.11, --chat-template flag, --max-model-len 16384)
  into CDK user-data so a fresh g6.xlarge boot doesn't replay the
  SSM-driven dance.
* AWQ-quantize Qwen for ~2.5× speedup; until then admin keeps active
  model = gpt-4o-mini (Qwen on L4 is 16 t/s).
* Verify after PR25 deploy completes: open
  https://khayyammath.com/studio, run two consecutive prompts, watch
  the second figure paint progressively in the canvas iframe.
