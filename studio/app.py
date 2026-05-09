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
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from service.canvas import REGISTRY, Canvas
from studio.digest import digest_for_agent, text_digest


_STATIC = Path(__file__).resolve().parent / "static"
_DEFAULT_MODEL = "claude-opus-4-7"
_API_URL = "https://api.anthropic.com/v1/messages"

router = APIRouter(prefix="/studio")


# ---------------------------------------------------------------------------
# Tool schemas — wire sevim ops into Anthropic tool-use definitions.
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "sevim_open",
        "description": (
            "Open a new diagram canvas.  Set prelude=... to the FULL "
            "verbal problem-and-solution definition (50-150 words); "
            "the canvas speaks it via piper TTS the moment it opens, "
            "while you write the figure-build calls.  Set animate=True "
            "for a self-playing whiteboard.  After the prelude, the "
            "viewer auto-speaks the transition phrase 'And now please "
            "look at the diagram.' before sevim_narrate's walkthrough "
            "starts — you don't write the transition yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "math_mode": {"type": "boolean"},
                "animate": {"type": "boolean"},
                "prelude": {"type": "string",
                            "description": "Full 50-150 word problem+solution speech."},
                "transition": {"type": "string",
                               "description": "Optional transition phrase override."},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
            },
        },
    },
    {
        "name": "sevim_apply",
        "description": (
            "Batched mutation: apply many ops in one round-trip.  "
            "Each op is {op: 'add_node'|'add_edge'|'add_caption'|"
            "'remove'|'describe', ...}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "canvas_id": {"type": "string"},
                "ops": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["canvas_id", "ops"],
        },
    },
    {
        "name": "sevim_narrate",
        "description": "Generate phrase-timed voice narration for the current figure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "canvas_id": {"type": "string"},
                "script": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "speak": {"type": "string"},
                            "highlight": {"type": "string"},
                        },
                        "required": ["speak"],
                    },
                },
            },
            "required": ["canvas_id", "script"],
        },
    },
]


def _execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Translate an Anthropic tool_use call into a CanvasRegistry mutation."""
    if name == "sevim_open":
        c = REGISTRY.open(
            canvas_id=args.get("canvas_id"),
            math_mode=bool(args.get("math_mode", False)),
            animate=bool(args.get("animate", False)),
            width=int(args.get("width", 700)),
            height=int(args.get("height", 440)),
        )
        prelude = (args.get("prelude") or args.get("intro") or "").strip()
        transition = (args.get("transition") or "").strip()
        if prelude or transition:
            with c.lock:
                if prelude:
                    c.intro_text = prelude
                if transition:
                    c.transition_text = transition
                c.revision += 1
        if prelude:
            try:
                c.intro(prelude)
            except Exception:  # noqa: BLE001 — viewer falls back gracefully
                pass
        return {
            "canvas_id": c.canvas_id,
            "view_url": f"/canvas/{c.canvas_id}/view",
            "prelude_started": bool(prelude),
        }
    if name == "sevim_apply":
        cid = args.get("canvas_id")
        if not cid:
            raise ValueError("canvas_id required")
        c = REGISTRY.get(cid)
        results = []
        for op in args.get("ops", []):
            kind = op.get("op")
            if kind == "add_node":
                results.append(c.add_node(
                    label=op["label"],
                    kind=op.get("kind"),
                    meta_extras=op.get("meta_extras"),
                ))
            elif kind == "add_edge":
                results.append(c.add_edge(
                    src_id=op["src_id"],
                    dst_id=op["dst_id"],
                    relation=op["relation"],
                ))
            elif kind == "add_caption":
                meta = {k: op[k] for k in ("x", "y", "anchor") if k in op}
                results.append(c.add_node(
                    label=op["text"], kind="caption", meta_extras=meta,
                ))
            elif kind == "remove":
                results.append(c.remove(op["ids"]))
        return {"canvas_id": cid, "results": results}
    if name == "sevim_narrate":
        cid = args.get("canvas_id")
        if not cid:
            raise ValueError("canvas_id required")
        c = REGISTRY.get(cid)
        return c.narrate(args.get("script", []))
    raise ValueError(f"unknown tool {name!r}")


# ---------------------------------------------------------------------------
# Chat endpoint — streams Claude's response back to the browser.
# ---------------------------------------------------------------------------

class ChatTurn(BaseModel):
    role: str          # "user" | "assistant"
    content: str       # plain text


class ChatReq(BaseModel):
    history: list[ChatTurn] = []
    user: str
    canvas_id: str | None = None
    model: str = _DEFAULT_MODEL


SYSTEM_PROMPT = (
    "You are a real-time visual tutor.  The user watches the Sevim "
    "canvas in their browser; you build figures via the sevim_* tools.\n"
    "\n"
    "Your chat-side text reply is for visual reference only — it is "
    "NOT spoken aloud.  ALL audio comes from the canvas: piper TTS "
    "speaks the prelude on sevim_open, then the transition, then the "
    "phrase-timed walkthrough on sevim_narrate.  So write the actual "
    "lesson into the prelude and narration script — keep your chat "
    "reply terse (one or two acknowledgement sentences).\n"
    "\n"
    "Standard 3-call workflow:\n"
    "  1. sevim_open(math_mode=True, animate=True, prelude=\"…\") "
    "with the FULL 50-150 word problem-and-solution definition in "
    "prelude.  Canvas starts speaking immediately.\n"
    "  2. sevim_apply(canvas_id=…, ops=[…]) — ALL nodes, edges, AND "
    "captions in one batched call.  Captions go in the same list:\n"
    "       {\"op\":\"add_caption\",\"text\":…,\"x\":…,\"y\":…,\"anchor\":…}\n"
    "  3. sevim_narrate(canvas_id=…, script=[…]) — phrase-timed "
    "walk-through.  Each phrase highlights one element.  The viewer "
    "auto-prepends the transition phrase ('And now please look at "
    "the diagram.') — start the script with the first observation "
    "about the figure, not with a transition.\n"
    "\n"
    "Carry canvas_id explicitly through every tool call after the "
    "first sevim_open."
)


@router.post("/chat")
async def chat(req: ChatReq):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not set on the studio server")

    messages: list[dict[str, Any]] = []
    for turn in req.history:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": req.user})

    async def event_stream():
        # STREAMING server-side tool-use loop: forward Anthropic's
        # text + tool deltas as they arrive so the chat fills
        # incrementally, instead of going silent for the full ~60 s
        # generation per step.
        for _step in range(8):  # safety cap
            payload = {
                "model": req.model,
                "max_tokens": 16384,
                "stream": True,
                "system": SYSTEM_PROMPT,
                "tools": TOOLS,
                "messages": messages,
            }

            content: list[dict[str, Any]] = []
            stop = None
            blocks: dict[int, dict[str, Any]] = {}

            try:
                async with httpx.AsyncClient(timeout=180) as client:
                    async with client.stream(
                        "POST",
                        _API_URL,
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json=payload,
                    ) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode(errors="replace")
                            yield {"event": "error",
                                   "data": json.dumps({"detail": body[:600]})}
                            return
                        async for raw in resp.aiter_lines():
                            if not raw or not raw.startswith("data:"):
                                continue
                            data_str = raw[5:].strip()
                            if not data_str:
                                continue
                            try:
                                ev = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            etype = ev.get("type")
                            if etype == "content_block_start":
                                idx = ev.get("index", 0)
                                cb = ev.get("content_block") or {}
                                blocks[idx] = {
                                    "type": cb.get("type"),
                                    "id": cb.get("id"),
                                    "name": cb.get("name"),
                                    "text": "",
                                    "partial_json": "",
                                }
                                if cb.get("type") == "tool_use":
                                    yield {"event": "tool_call",
                                           "data": json.dumps({
                                               "name": cb.get("name"),
                                               "args_keys": [],
                                           })}
                            elif etype == "content_block_delta":
                                idx = ev.get("index", 0)
                                delta = ev.get("delta") or {}
                                blk = blocks.get(idx)
                                if blk is None:
                                    continue
                                dtype = delta.get("type")
                                if dtype == "text_delta":
                                    chunk = delta.get("text", "")
                                    blk["text"] += chunk
                                    if chunk:
                                        yield {"event": "text",
                                               "data": json.dumps({"text": chunk})}
                                elif dtype == "input_json_delta":
                                    blk["partial_json"] += delta.get("partial_json", "")
                            elif etype == "content_block_stop":
                                idx = ev.get("index", 0)
                                blk = blocks.get(idx)
                                if blk is None:
                                    continue
                                if blk["type"] == "text":
                                    content.append({"type": "text", "text": blk["text"]})
                                elif blk["type"] == "tool_use":
                                    try:
                                        inp = json.loads(blk["partial_json"] or "{}")
                                    except json.JSONDecodeError:
                                        inp = {}
                                    content.append({
                                        "type": "tool_use",
                                        "id": blk["id"],
                                        "name": blk["name"],
                                        "input": inp,
                                    })
                            elif etype == "message_delta":
                                d = ev.get("delta") or {}
                                if "stop_reason" in d:
                                    stop = d["stop_reason"]
                            elif etype == "error":
                                yield {"event": "error",
                                       "data": json.dumps(ev.get("error") or ev)}
                                return
            except Exception as exc:  # noqa: BLE001
                yield {"event": "error",
                       "data": json.dumps({"detail": f"stream failed: {exc}"})}
                return

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            messages.append({"role": "assistant", "content": content})

            # If we hit a non-tool-use stop reason, surface it.  The
            # important cases:
            #   end_turn      — natural finish
            #   max_tokens    — output budget hit; figure may be partial
            #   stop_sequence — rare
            if stop != "tool_use" or not tool_uses:
                if stop == "max_tokens":
                    yield {"event": "text", "data": json.dumps({
                        "text": (
                            "\n\n[Studio: hit max_tokens before finishing — "
                            "the figure above may be partial.  Bump "
                            "max_tokens or break the build into smaller "
                            "tool calls.]"
                        ),
                    })}
                yield {"event": "done", "data": json.dumps({"stop_reason": stop})}
                return

            # Execute tools and append the tool_result blocks.
            tool_results = []
            for tu in tool_uses:
                try:
                    out = _execute_tool(tu["name"], tu.get("input") or {})
                    body = json.dumps(out)
                except Exception as exc:  # noqa: BLE001
                    body = json.dumps({"error": str(exc)})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": body,
                })
                yield {"event": "tool_result", "data": body}
            messages.append({"role": "user", "content": tool_results})

        yield {"event": "done", "data": json.dumps({"stop_reason": "max_steps"})}

    return EventSourceResponse(event_stream())


@router.post("/canvas/new")
def new_canvas() -> dict[str, str]:
    """Spawn a fresh canvas owned by Studio (random id, math_mode, animate)."""
    cid = "studio_" + secrets.token_hex(3)
    c = REGISTRY.open(canvas_id=cid, math_mode=True, animate=True, width=820, height=520)
    return {"canvas_id": c.canvas_id, "view_url": f"/canvas/{c.canvas_id}/view"}


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "model": os.environ.get("SEVIM_STUDIO_MODEL", _DEFAULT_MODEL),
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def studio_index() -> HTMLResponse:
    html_path = _STATIC / "studio.html"
    if not html_path.exists():
        raise HTTPException(500, "studio.html missing")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tier 3 — canvas-state digest + realtime bridge stub
# ---------------------------------------------------------------------------

@router.get("/canvas/{cid}/digest")
def canvas_digest(cid: str, include_png: bool = False) -> dict[str, Any]:
    """Compact summary the realtime tutor receives every turn.

    The realtime agent sees this on every conversation turn so it
    knows what the user is currently looking at — without needing
    to re-call sevim_review.  Lightweight: ~1-3 KB of text plus an
    optional 5-30 KB PNG when vision is wanted.
    """
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")
    return digest_for_agent(c, include_png=include_png)


@router.websocket("/realtime/{cid}")
async def realtime_bridge(ws: WebSocket, cid: str):
    """Bidirectional bridge for realtime voice+canvas tutoring.

    Architecture (when wired to a real backend):

        Browser (mic + speaker)
            ⇅ WebSocket (PCM16 frames + tool deltas)
        This bridge
            ⇅ WebSocket (vendor realtime API: OpenAI / Anthropic when shipped)
        Realtime model
            ⇅ tool calls
        CanvasRegistry mutations + canvas digest on every turn

    Current status: STUB.  Logs that a client connected, sends back
    the canvas digest, and echoes pings.  Concrete realtime-vendor
    plumbing (PCM forwarding, function-call routing, audio response
    streaming) is intentionally left out so this commit lands cleanly
    without an OpenAI/Anthropic-realtime API key.

    To productionise: implement ``_run_vendor_session`` to open a
    second WebSocket to the vendor, forward audio frames in both
    directions, intercept ``tool_call`` events and execute them via
    ``_execute_tool`` (which already does the right thing for the
    HTTP /studio/chat path), and push ``canvas-digest`` to the
    vendor session whenever ``REGISTRY.get(cid).revision`` changes.
    """
    await ws.accept()
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        await ws.send_json({"type": "error", "message": f"canvas {cid!r} not found"})
        await ws.close()
        return

    last_revision = -1
    try:
        # Initial digest so the agent can introduce the figure.
        await ws.send_json({
            "type": "canvas_digest",
            "digest": digest_for_agent(c, include_png=False),
        })

        while True:
            # Push a fresh digest whenever the canvas changes.
            with c.lock:
                rev = c.revision
            if rev != last_revision:
                last_revision = rev
                await ws.send_json({
                    "type": "canvas_digest",
                    "digest": digest_for_agent(c, include_png=False),
                })

            # Echo any client message — placeholder for vendor
            # round-trips.  Real implementation routes audio frames
            # to the vendor and tool calls to _execute_tool.
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            kind = msg.get("type")
            if kind == "ping":
                await ws.send_json({"type": "pong"})
            elif kind == "user_text":
                # In the real bridge this would be vendor-streamed
                # audio.  Here we just acknowledge for development.
                await ws.send_json({
                    "type": "agent_turn",
                    "text": (
                        "[realtime stub] would stream audio reply for: "
                        + str(msg.get("text", ""))[:160]
                    ),
                })
            elif kind == "tool_call":
                try:
                    out = _execute_tool(msg["name"], msg.get("input") or {})
                    await ws.send_json({"type": "tool_result", "result": out})
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({"type": "tool_error", "error": str(exc)})
    except WebSocketDisconnect:
        return
