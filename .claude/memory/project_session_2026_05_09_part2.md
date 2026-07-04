---
name: 2026-05-09 evening session — figure-runtime pivot + Studio express path
description: Major architectural pivot from structured-tool pipeline to a single SVG-direct sevim_express tool with vision-audit retry, OpenAI gpt-4o backend, conversational context, and backend-side preference router. About to tag Success2.
type: project
originSessionId: ddab3e35-4da7-437e-965d-3a536788200b
---
## Summary

Continuation of the morning's session.  Pivoted Sevim from the
structured-tool pipeline (sevim_open + sevim_plan + sevim_apply +
sevim_narrate + animation) to a **single-tool SVG-direct pipeline**
because the structured path kept producing bad figures and the user
preferred LLM-emitted SVG quality.

## Final architecture (one pipeline, end of session)

```
USER  →  POST /studio/chat
  │
  ▼
parse_preference (regex pre-router)
  │  matches "use red for highlight" / "speak slower" / "louder" / etc.
  │  if match: apply server-side, return early (NO LLM call)
  │  else: fall through
  ▼
_stream_vllm_chat  (forces tool_choice = sevim_express)
  │
  ▼
_execute_tool("sevim_express", {prompt, context_canvas_ids})
  │
  ▼
express_figure (studio/express.py)
  • Builds multi-modal user message:
    - For each prior canvas: SVG XML + PNG snapshot + original prompt + narration
    - Refinement-mode header demanding byte-for-byte preservation
    - The new request as trailing text
  • Calls gpt-4o with json_schema {svg, narration, title}
  • Renders SVG → PNG, calls gpt-4o vision review with REVIEW_SCHEMA
  • Reviewer returns {verdict, summary, fixes[{action,what,where,details}]}
  • If FAIL: format fixes as numbered checklist, retry (max_retries=1)
  • Returns {svg, narration, title, retries_used, review_history}
  │
  ▼
Canvas.set_raw_svg(svg) + Canvas.narrate(script)
  │
  ▼
Hard-stop after sevim_express in _stream_vllm_chat
  │
  ▼
Frontend swaps iframe.src → /canvas/<express_id>/view
```

## Key files (post-pivot)

* `studio/express.py` — sevim_express loop + vision review + multi-modal context builder
* `studio/preferences.py` — regex pre-router for backend-only requests
  (highlight color, audio speed/volume)
* `studio/app.py` — Studio router; only `sevim_express` in TOOLS;
  forced tool_choice; 120 s hard timeout on outer LLM stream
* `service/canvas.py` — Canvas.set_raw_svg() injects LLM SVG, bypasses S3→S5;
  `is_raw_svg`, `raw_svg_ids`, `genesis_prompt` fields
* `service/static/canvas.html` — viewer; highlight selectors include
  `[id="..."]` so LLM-emitted SVG IDs work; reads /studio/preferences

## Operational state at end of session

* Studio: PID 52813, port 7781, backend vllm pointing at OpenAI gpt-4o
* OPENAI_API_KEY pulled from `/home/ara/Documents/Programming/agentic_systems/Interactive Video Lecture Creator/.env`
* Health check: `curl -s http://127.0.0.1:7781/studio/health`
* Tail log: `tail -F /tmp/sevim_studio_7781.log`
* Restart command:
  ```
  OPENAI_API_KEY=$(grep -E "^OPENAI_API_KEY=" "/home/ara/Documents/Programming/agentic_systems/Interactive Video Lecture Creator/.env" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
  SEVIM_STUDIO_BACKEND=vllm SEVIM_VLLM_URL=https://api.openai.com/v1 SEVIM_VLLM_MODEL=gpt-4o OPENAI_API_KEY="$OPENAI_API_KEY" SEVIM_HTTP_PORT=7781 SEVIM_NO_BROWSER=1 nohup /home/ara/.local/bin/uv run --directory /home/ara/Documents/Programming/sevim_plugin python -m studio > /tmp/sevim_studio_7781.log 2>&1 &
  disown
  ```

## Critical bugs fixed (in order)

1. **SSE parser used LF split** — sse-starlette emits CRLF.  Changed to `\r?\n\r?\n` regex.
2. **Mid-narration audio race** — onNarrationReady checked `intro.paused` which read stale; replaced with `introPlayed` flag + tryPlayChain `if intro.src && !introPlayed return`.
3. **Caption leader-line orphans** — added vision-audit critic that catches them.
4. **add_node label slug collision** — added `node_id` parameter (since-removed with structured tools, but lesson kept).
5. **OpenAI finish_reason='stop' with forced tool_choice** — my exit condition was `finish_reason != "tool_calls"`, which skipped execution even when tool_calls was populated.  Fixed to `if not tool_calls`.
6. **Vision auditor too harsh** — was default-FAIL with 8-item perfection checklist.  Loosened to default-PASS, only FAIL on objective brokenness (orphan leaders, wrong topology, missing main content).
7. **Express hung on chat-only responses** — model could choose tool_choice='auto' to reply in text instead of calling sevim_express, freezing iframe.  Forced `tool_choice = {function: sevim_express}`.
8. **Hangs from infinite outer-LLM streams** — added inline `time.monotonic()` 120s deadline check.

## Tag plan

* `success_1` — already exists from morning session (NLP pipeline + animation working)
* `Success2` — about to tag (single-tool SVG-direct pipeline, OpenAI backend, vision audit, conversational context, preference router)

## Open issues (resume points)

* Vision auditor sometimes PASSes incomplete figures (e.g. matrix mult shown as 3 boxes labelled A B C with no contents).  Lenient defaults err on this side; sharper rubric needed if quality slips.
* Refinement mode: the model still occasionally regenerates from scratch rather than diff-editing, despite the explicit "preserve byte-for-byte" instructions and SVG XML in context.  Could add a SVG-diff verification step.
* Word-level narration sync (Stage 2 of the original plan) was never built — current narration is phrase-level, single highlight per phrase (now extended to LIST of highlights per phrase).
* MCP server (`mcp_server/`) still has the old structured tools registered for Claude Code use.  Studio bypasses it entirely.  If we want to also rip the structured tools from MCP, that's a separate cleanup.

## Repo

* `git@github.com:arashkermaniprojects/sevim-plugin.git`
* main branch
