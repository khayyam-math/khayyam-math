---
name: After pipeline deploys, test a live generation AND measure streaming
description: A real /studio/chat round-trip must be exercised after any chat/figure-pipeline deploy — and the test must measure time-to-first-byte, because a buffered SSE stream still "succeeds" for a patient client.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
After any change touching the Studio chat path or figure pipeline
(service/app.py, studio/app.py, express.py, middleware, telemetry),
verify with a REAL figure generation — `/health`, endpoint status
codes, the canvas viewer, and `pytest` all miss chat-path bugs.

**The test must measure time-to-first-byte, not just "did it
return."** `curl` waits patiently for a buffered response, so a
broken SSE stream still looks like a pass. Stream the response and
assert the first event arrives within ~1-2s.

**Two incidents on 2026-05-19, both production-down, both missed:**
1. `_execute_tool(owner=user)` added inside `_stream_vllm_chat`
   where `user` wasn't in scope → `NameError` crashed every figure
   request. 266 tests passed (suite never drives a live tool call).
2. The security-headers middleware used `@app.middleware("http")`
   (Starlette `BaseHTTPMiddleware`), which **buffers the whole
   response body** → the figure-generation SSE stream was held back
   for the entire ~1-2 min generation → load balancer returned 504.
   A curl smoke test "passed" because curl waited out the buffering.

**How to apply:**
- NEVER use `BaseHTTPMiddleware` / `@app.middleware("http")` on this
  app — it breaks SSE (`/studio/chat`, `/canvas/*/events`). Use a
  pure ASGI middleware that only rewrites `http.response.start`.
- Smoke test: start `service.app:app` with
  `SEVIM_VLLM_URL=https://api.openai.com/v1`, `SEVIM_VLLM_MODEL=gpt-4o`,
  `OPENAI_API_KEY` from `.env`; stream `POST /studio/chat` and assert
  first SSE line < ~2s AND final `stop_reason: express_complete`.
