"""Sevim Studio backend — direct-to-Anthropic chat with sevim tools.

Mounted into the main FastAPI app at ``/studio/*``.  Endpoints:

  GET  /studio                serve the SPA
  POST /studio/chat           one-shot chat turn (streaming SSE response)
  POST /studio/canvas/new     spawn a fresh studio canvas
  GET  /studio/health         API key configured? piper available?

The chat endpoint loops Claude's tool-use turns server-side: the
client sends one user message, the server pumps the multi-step
tool-use conversation with Anthropic until Claude returns a stop
reason, streaming text deltas back to the client as SSE events.
Tool calls execute against the same CanvasRegistry singleton the
MCP path uses, so the canvas in Studio is the *same* canvas the
MCP server's viewer would show.

Required env var: ``ANTHROPIC_API_KEY``.
Optional env var: ``SEVIM_STUDIO_MODEL`` (default ``claude-opus-4-7``).
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from service.canvas import REGISTRY


_STATIC = Path(__file__).resolve().parent / "static"

# OpenAI-compatible Chat Completions endpoint Sevim talks to.  Defaults
# to the user's local vLLM but the env vars below let the same code
# point at api.openai.com (or any other compatible service).
_DEFAULT_VLLM_URL = "http://127.0.0.1:8000/v1"
_DEFAULT_VLLM_MODEL = "Qwen/Qwen2.5-14B-Instruct-AWQ"


def _backend() -> str:
    """Selected studio backend.  Always ``"vllm"`` in v0.4 (the OpenAI-
    compatible Chat Completions API path).  The Anthropic-direct backend
    was removed when we collapsed to the single sevim_express pipeline."""
    return "vllm"


def _vllm_url() -> str:
    return (os.environ.get("SEVIM_VLLM_URL") or _DEFAULT_VLLM_URL).rstrip("/")


def _vllm_model() -> str:
    return os.environ.get("SEVIM_VLLM_MODEL") or _DEFAULT_VLLM_MODEL

router = APIRouter(prefix="/studio")


# ---------------------------------------------------------------------------
# Tool schemas — wire sevim ops into Anthropic tool-use definitions.
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "sevim_express",
        "description": (
            "Build a complete teaching figure for the user's request.  "
            "ONE call, no follow-ups: Sevim asks gpt-4o to emit a "
            "self-contained SVG + phrase-timed narration in a single "
            "structured response, runs a vision-review loop (≤3 "
            "retries) against a strict math-teacher rubric, synthesises "
            "piper TTS audio, and ships the canvas to the user.  Works "
            "for any concept that can be drawn statically: matrix "
            "multiplication, 3SAT reductions, set diagrams, function "
            "plots, geometry, complexity-class diagrams, derivative "
            "visualisations, etc.  This is the ONLY canvas-building "
            "tool you have — there is no separate sevim_open / "
            "sevim_plan / sevim_apply / sevim_narrate.  Call this with "
            "the user's request, then end your turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The user's request, verbatim or lightly clarified.",
                },
                "context_canvas_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional canvas IDs (express_xxxxx) of prior "
                        "figures the user is refining or combining.  "
                        "When set, Sevim attaches each prior figure's "
                        "PNG snapshot + the prompt that made it to the "
                        "LLM call so gpt-4o sees the real pixels.  "
                        "Studio auto-supplies the most-recent canvas "
                        "and any pinned canvases when the user asks "
                        "for a refinement; you typically don't need to "
                        "pass this manually."
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
]


async def _execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run the single Studio tool: ``sevim_express``.

    Loads any prior canvases the user is refining out of REGISTRY,
    delegates to ``express_figure`` for SVG generation + vision audit,
    then injects the resulting SVG and synthesises narration.
    """
    if name != "sevim_express":
        raise ValueError(f"unknown tool {name!r} — only sevim_express is exposed")
    from studio.express import express_figure
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("sevim_express requires a non-empty 'prompt'")
    # Conversational context: pull prior canvases out of REGISTRY so
    # gpt-4o sees the actual figure(s) the user is refining (SVG XML +
    # PNG snapshot + original prompt + narration script).
    context_ids = list(args.get("context_canvas_ids") or [])
    context_canvases: list[dict[str, Any]] = []
    for prior_id in context_ids[:3]:  # cap at 3 prior figures (cost)
        try:
            pc = REGISTRY.get(prior_id)
        except KeyError:
            continue
        if not getattr(pc, "svg", None):
            continue
        context_canvases.append({
            "id": pc.canvas_id,
            "svg": pc.svg,
            "prompt": pc.genesis_prompt or "",
            "narration": (pc.narration_manifest or {}).get("phrases") or [],
        })
    result = await express_figure(
        user_prompt=prompt,
        base_url=_vllm_url(),
        model=_vllm_model(),
        api_key=os.environ.get("OPENAI_API_KEY"),
        context_canvases=context_canvases,
    )
    cid = "express_" + secrets.token_hex(3)
    c = REGISTRY.open(canvas_id=cid, math_mode=True, animate=False,
                      width=900, height=620)
    c.set_raw_svg(result["svg"])
    c.genesis_prompt = prompt
    narration = result.get("narration") or []
    narrate_out = c.narrate(narration) if narration else {}
    return {
        "canvas_id": c.canvas_id,
        "view_url": f"/canvas/{c.canvas_id}/view",
        "title": result.get("title", ""),
        "n_phrases": len(narration),
        "retries_used": result.get("retries_used", 0),
        "review_history": result.get("review_history", []),
        "context_used": [pc["id"] for pc in context_canvases],
        "narration": narrate_out,
    }


# ---------------------------------------------------------------------------
# Tool-format translation (Anthropic-style internal schema → OpenAI
# function-calling format the OpenAI/vLLM Chat Completions API expects).
# ---------------------------------------------------------------------------

def _tools_openai_format() -> list[dict[str, Any]]:
    """Translate the internal TOOLS list to OpenAI function-calling format."""
    out: list[dict[str, Any]] = []
    for t in TOOLS:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        })
    return out


# ---------------------------------------------------------------------------
# Chat endpoint — streams Claude's response back to the browser.
# ---------------------------------------------------------------------------

class ChatTurn(BaseModel):
    role: str          # "user" | "assistant"
    content: str       # plain text


class ChatReq(BaseModel):
    history: list[ChatTurn] = []
    user: str
    # Most-recent canvas the iframe is showing.  Studio attaches this
    # to the express call so the LLM sees the figure the user is
    # currently looking at and can refine it instead of starting fresh.
    canvas_id: str | None = None
    # Additional canvases the user has explicitly pinned ("📌") in the
    # chat panel.  Used for combining figures across turns.  Capped at
    # ~3 by the frontend to bound LLM cost.
    prior_canvas_ids: list[str] = []


SYSTEM_PROMPT = (
    "You are a real-time visual TUTOR.  Every user message is a request "
    "for a diagram-with-narration.  You have exactly ONE tool: "
    "``sevim_express(prompt)``.  Call it once with the user's request "
    "(verbatim or lightly clarified to be self-contained), then end "
    "your turn with at most one short acknowledgement sentence in "
    "chat.  All audio + visuals come from the canvas — do NOT type "
    "the lesson into chat.\n"
    "\n"
    "How sevim_express works (so you know what's happening):\n"
    "  • gpt-4o emits a complete SVG figure + phrase-timed narration "
    "in one structured response.\n"
    "  • Sevim renders the SVG to PNG and asks gpt-4o (vision) to "
    "audit it against a strict math-teacher rubric.  Up to 3 retries.\n"
    "  • piper TTS synthesises the narration; the canvas viewer plays "
    "the audio and highlights elements as their phrase is spoken.\n"
    "  • Result is shipped to the user's browser iframe.\n"
    "\n"
    "If the user follows up to refine ('add the conclusion', 'use "
    "different numbers', 'show the dot product step', 'combine this "
    "with the matrix mult one'), call sevim_express again with a "
    "prompt that bundles the original request plus the refinement.  "
    "Studio AUTOMATICALLY attaches the most-recent canvas (the one "
    "the user is currently looking at) AND any canvases the user has "
    "pinned in the chat (📌 button) — gpt-4o sees their actual PNG "
    "snapshots and the prompts that produced them, so you can refer "
    "to them naturally in your new prompt without quoting canvas IDs.\n"
    "\n"
    "If the user asks something that is not a figure request "
    "(general question, ambiguous), reply in chat asking what they'd "
    "like to see drawn — don't call sevim_express on a vague prompt."
)


@router.post("/chat")
async def chat(req: ChatReq):
    # Diagnostic so /tmp/sevim_studio_openai.log shows when the
    # browser actually reaches us.
    import sys
    print(f"[studio-chat] POST received: user={req.user[:60]!r} canvas_id={req.canvas_id}",
          flush=True, file=sys.stderr)
    # PRE-ROUTER: detect "system preference" requests (highlight color,
    # audio speed, volume, autoplay) and handle them deterministically
    # without calling gpt-4o.  When the user asks for a backend-only
    # change, the LLM round-trip is unnecessary cost + latency + a
    # source of hangs.
    from studio.preferences import parse_preference
    pref = parse_preference(req.user)
    if pref is not None:
        print(f"[studio-chat] preference matched: {pref}", flush=True, file=sys.stderr)
        async def pref_stream():
            yield {"event": "tool_call", "data": json.dumps({
                "name": "set_preference",
                "args_keys": [f"{pref.get('key','?')}={pref.get('value','?')}"],
            })}
            yield {"event": "tool_result", "data": json.dumps(pref)}
            yield {"event": "text", "data": json.dumps({"text": pref.get("status", "Preference applied.")})}
            yield {"event": "done", "data": json.dumps({"stop_reason": "preference_applied"})}
        return EventSourceResponse(pref_stream())
    return EventSourceResponse(_stream_vllm_chat(req))


async def _stream_vllm_chat(req: ChatReq):
    """Stream the tool-calling loop against a local vLLM server.

    Mirrors the SSE event vocabulary the Anthropic streamer emits
    (``text`` / ``tool_call`` / ``tool_result`` / ``done`` / ``error``)
    so the studio frontend works unchanged.

    vLLM serves the OpenAI Chat Completions API, so all wire formats
    (tools, assistant tool_calls, tool messages, finish_reason, SSE
    framing) follow OpenAI's spec.  Requires vLLM to be started with
    ``--enable-auto-tool-choice --tool-call-parser hermes`` for
    Qwen2.5-Instruct.
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Compact prior turns: keep just role+text, cap each turn's content
    # at ~600 chars, and only retain the most recent 6 turns.  Without
    # this, refinement turns N+ accumulate the full assistant chat-side
    # text from every prior round and inflate the LLM's input until
    # response latency degrades.
    history_window = 6
    trimmed = list(req.history)[-history_window:]
    for turn in trimmed:
        text = (turn.content or "")
        if len(text) > 600:
            text = text[:580] + " …[truncated]"
        messages.append({"role": turn.role, "content": text})
    messages.append({"role": "user", "content": req.user})

    base = _vllm_url()
    model = _vllm_model()
    tools = _tools_openai_format()

    # Track the most-recent canvas the LLM has touched so the post-stop
    # audit knows which canvas to inspect.
    latest_canvas_id: str | None = None
    import sys as _sys
    def _slog(msg: str) -> None:
        print(f"[chat-loop] {msg}", flush=True, file=_sys.stderr)

    _slog(f"start canvas_id={req.canvas_id} prior={req.prior_canvas_ids} "
          f"history_len={len(req.history)} user={req.user[:50]!r}")

    for _step in range(20):  # bumped from 8 to fit up to 3 audit retries
        _slog(f"step={_step} sending outer LLM call (messages={len(messages)})")
        # Force the model to call sevim_express on every turn.  Studio's
        # job is to build figures; if we let tool_choice='auto' the
        # model can decide to respond in chat-side text instead, leaving
        # the iframe empty and the chat panel hung waiting for a tool
        # result that never comes.  Preference requests are pre-routed
        # before this loop, so by the time we get here the user wants a
        # figure.
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": {
                "type": "function",
                "function": {"name": "sevim_express"},
            },
            "parallel_tool_calls": False,
            "stream": True,
            "max_tokens": 4096,
            "temperature": 0.3,
        }

        # Per-step accumulators.  vLLM streams text in `delta.content`
        # and tool calls in `delta.tool_calls[*]`; both arrive in many
        # tiny pieces and we have to reassemble before executing.
        text_buf = ""
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        # Bearer auth when targeting OpenAI (or any OpenAI-compatible
        # service that requires it).  Local vLLM doesn't need auth, so
        # the header is omitted unless OPENAI_API_KEY is set.
        headers = {"content-type": "application/json"}
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # HARD TOTAL CAP on this round-trip.  Even if OpenAI keeps the
        # connection alive with pings (which makes per-event read
        # timeouts useless), an inline elapsed-time check on each event
        # ensures we abort within the deadline.  The forced
        # tool_choice="sevim_express" already keeps responses small, so
        # 120s is generous.
        import time as _time
        _OUTER_TOTAL_TIMEOUT_S = 120.0
        _stream_start = _time.monotonic()
        try:
            _timeout = httpx.Timeout(connect=15.0, read=90.0,
                                     write=15.0, pool=15.0)
            async with httpx.AsyncClient(timeout=_timeout) as client:
                async with client.stream(
                    "POST",
                    f"{base}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    _slog(f"step={_step} outer LLM responded status={resp.status_code}")
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="replace")
                        _slog(f"step={_step} HTTP {resp.status_code} body: {body[:200]}")
                        yield {"event": "error",
                               "data": json.dumps({"detail": body[:600]})}
                        return
                    _line_count = 0
                    async for raw in resp.aiter_lines():
                        _line_count += 1
                        if _time.monotonic() - _stream_start > _OUTER_TOTAL_TIMEOUT_S:
                            _slog(f"step={_step} HARD TIMEOUT after {_OUTER_TOTAL_TIMEOUT_S}s lines={_line_count} — aborting")
                            raise TimeoutError(f"outer LLM stream exceeded {_OUTER_TOTAL_TIMEOUT_S}s")
                        # Log every 20th line to confirm stream is alive.
                        if _line_count % 20 == 1:
                            _slog(f"step={_step} streaming line {_line_count}: {raw[:80]!r}")
                        if not raw or not raw.startswith("data:"):
                            continue
                        data_str = raw[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            ev = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        choices = ev.get("choices") or []
                        if not choices:
                            continue
                        ch = choices[0]
                        delta = ch.get("delta") or {}
                        if delta.get("content"):
                            chunk = delta["content"]
                            text_buf += chunk
                            yield {"event": "text",
                                   "data": json.dumps({"text": chunk})}
                        for tc in (delta.get("tool_calls") or []):
                            idx = tc.get("index", 0)
                            slot = tool_calls.setdefault(idx, {
                                "id": None, "name": None, "arguments": "",
                            })
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                                yield {"event": "tool_call",
                                       "data": json.dumps({
                                           "name": fn["name"], "args_keys": [],
                                       })}
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
                        if ch.get("finish_reason"):
                            finish_reason = ch["finish_reason"]
                            _slog(f"step={_step} finish_reason={finish_reason!r} "
                                  f"text_buf_len={len(text_buf)} "
                                  f"tool_calls={list(tool_calls.keys())}")
            _slog(f"step={_step} stream consumed: lines={_line_count} "
                  f"finish_reason={finish_reason!r} tool_calls={[tc.get('name') for tc in tool_calls.values()]}")
        except Exception as exc:  # noqa: BLE001
            _slog(f"step={_step} OUTER LLM ERROR: {type(exc).__name__}: {exc}")
            yield {"event": "error",
                   "data": json.dumps({"detail": f"outer LLM stream failed: {type(exc).__name__}: {exc}"})}
            return

        # Assemble the assistant message exactly the way OpenAI expects
        # it on the next turn.  ``content`` MUST be present (can be
        # empty) when ``tool_calls`` is set; setting None upsets some
        # OpenAI-compatible servers.
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": text_buf}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.get("id") or f"call_{idx}",
                    "type": "function",
                    "function": {
                        "name": tc.get("name") or "",
                        "arguments": tc.get("arguments") or "{}",
                    },
                }
                for idx, tc in sorted(tool_calls.items())
            ]
        messages.append(assistant_msg)

        # If the model produced any tool calls, execute them — REGARDLESS
        # of finish_reason.  When tool_choice forces a specific function,
        # OpenAI returns finish_reason='stop' even though the tool_call
        # IS present in the deltas.  Only the absence of tool_calls (or
        # an explicit 'length' truncation) means there's nothing to run.
        if not tool_calls:
            if finish_reason == "length":
                yield {"event": "text", "data": json.dumps({"text": (
                    "\n\n[Studio: gpt-4o hit max_tokens before finishing.]"
                )})}
            yield {"event": "done",
                   "data": json.dumps({"stop_reason": finish_reason})}
            return

        # Execute each tool and append the OpenAI ``tool`` messages.
        for idx, tc in sorted(tool_calls.items()):
            try:
                args = json.loads(tc.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            # Auto-inject conversational context IDs into sevim_express:
            # the LLM rarely knows the canvas_id of the figure currently
            # in the iframe, so we plumb it through from the frontend.
            # User-pinned canvases (req.prior_canvas_ids) get prepended
            # before the latest canvas so the order matches "explicitly
            # pinned" → "implicitly current".
            if (tc.get("name") or "") == "sevim_express":
                supplied = list(args.get("context_canvas_ids") or [])
                ctx_ids: list[str] = []
                for pid in (req.prior_canvas_ids or []):
                    if pid and pid not in ctx_ids:
                        ctx_ids.append(pid)
                if req.canvas_id and req.canvas_id not in ctx_ids:
                    ctx_ids.append(req.canvas_id)
                for sid in supplied:
                    if sid and sid not in ctx_ids:
                        ctx_ids.append(sid)
                if ctx_ids:
                    args["context_canvas_ids"] = ctx_ids
            try:
                out = await _execute_tool(tc.get("name") or "", args)
                # Track the most-recent canvas_id touched by any tool —
                # this is what the post-stop audit will inspect.
                if isinstance(out, dict) and out.get("canvas_id"):
                    latest_canvas_id = out["canvas_id"]
                body = json.dumps(out)
            except Exception as exc:  # noqa: BLE001
                body = json.dumps({"error": str(exc)})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or f"call_{idx}",
                "content": body,
            })
            yield {"event": "tool_result", "data": body}
            # Server-side exclusivity: sevim_express does
            # open + apply + narrate atomically.  If we let the loop
            # continue, the model habitually goes on to call the
            # structured tools, and the iframe ends up swapping to
            # whatever canvas the LAST tool created — overwriting the
            # good express figure with a junk structured one.  Force a
            # clean stop here.
            if (tc.get("name") or "") == "sevim_express":
                yield {"event": "text", "data": json.dumps({
                    "text": "(figure built and narrated — see canvas →)"
                })}
                yield {"event": "done", "data": json.dumps({
                    "stop_reason": "express_complete"
                })}
                return

    yield {"event": "done", "data": json.dumps({"stop_reason": "max_steps"})}


@router.post("/canvas/new")
def new_canvas() -> dict[str, str]:
    """Spawn a fresh canvas owned by Studio (random id, math_mode, animate)."""
    cid = "studio_" + secrets.token_hex(3)
    c = REGISTRY.open(canvas_id=cid, math_mode=True, animate=True, width=820, height=520)
    return {"canvas_id": c.canvas_id, "view_url": f"/canvas/{c.canvas_id}/view"}


@router.get("/preferences")
def preferences() -> dict[str, Any]:
    """Current backend-side UI preferences (highlight color, audio speed,
    volume, autoplay).  The canvas viewer polls this and applies the
    values to its DOM/audio elements."""
    from studio.preferences import get_settings
    return get_settings()


@router.get("/health")
def health() -> dict[str, Any]:
    backend = _backend()
    info: dict[str, Any] = {"backend": backend}
    if backend == "vllm":
        info["model"] = _vllm_model()
        info["vllm_url"] = _vllm_url()
        # Distinguish local vLLM from OpenAI-style remote so the
        # frontend status pill can label it correctly.
        url = _vllm_url()
        if "openai.com" in url:
            info["remote"] = "openai"
            info["api_key_configured"] = bool(os.environ.get("OPENAI_API_KEY"))
        else:
            info["remote"] = "local"
        try:
            with httpx.Client(timeout=2.0) as c:
                probe_headers = {}
                api_key = os.environ.get("OPENAI_API_KEY")
                if api_key and "openai.com" in url:
                    probe_headers["Authorization"] = f"Bearer {api_key}"
                r = c.get(f"{url}/models", headers=probe_headers)
            info["vllm_reachable"] = r.status_code == 200
        except Exception:  # noqa: BLE001
            info["vllm_reachable"] = False
    else:
        info["api_key_configured"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
        info["model"] = os.environ.get("SEVIM_STUDIO_MODEL", _DEFAULT_MODEL)
    return info




@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def studio_index() -> HTMLResponse:
    html_path = _STATIC / "studio.html"
    if not html_path.exists():
        raise HTTPException(500, "studio.html missing")
    # The studio.html file changes frequently as we iterate.  Firefox's
    # default heuristic caches HTML aggressively even on the same TCP
    # session, which left the user staring at stale UI long after the
    # server had new code.  Force a fresh fetch on every page load.
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


