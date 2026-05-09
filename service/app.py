"""FastAPI service for Sevim — both legacy `/render` endpoints and the new
`/canvas/*` surface that backs the MCP plugin's live viewer.

Launch standalone:
    uvicorn service.app:app --host 127.0.0.1 --port 8003

The MCP server (``mcp_server/__main__.py``) launches this in a background
thread so the same process serves both the MCP tool surface (over stdio)
and the live HTML viewer (over HTTP).

Endpoints
---------
Legacy (stateless / per-session):
    GET  /health
    GET  /ontology
    POST /render
    POST /render/session/{sid}
    DELETE /session/{sid}

New (canvas-backed, shared with the MCP server):
    GET  /canvas/{cid}/view     HTML viewer that auto-updates via SSE
    GET  /canvas/{cid}/svg      raw current SVG
    GET  /canvas/{cid}/state    JSON snapshot (revision, counts, svg)
    GET  /canvas/{cid}/events   Server-Sent Events stream
    GET  /canvases              list all open canvases

Bind to 127.0.0.1 — this is intended for same-machine use.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import asdict
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sevim.ir import SceneGraph  # noqa: E402
from sevim.pipeline import run_pipeline  # noqa: E402
from sevim.s3_map import _RELATION_PATTERN  # noqa: E402

from .canvas import REGISTRY  # noqa: E402


app = FastAPI(
    title="SeVim",
    description="Deterministic semantic-to-visual mapping. "
                "Hosts both the legacy /render API and the /canvas/* "
                "surface used by the MCP plugin's live viewer.",
    version="0.2.0",
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# ---------------------------------------------------------------------------
# Legacy session-based render API (preserved for backward compatibility).
# ---------------------------------------------------------------------------

_sessions: dict[str, SceneGraph] = {}
_sessions_lock = Lock()


class RenderReq(BaseModel):
    text: str
    utterance_id: str = "u0"


class RenderResp(BaseModel):
    svg: str
    ir: dict
    trace: list[dict]


def _serialize(result) -> RenderResp:
    return RenderResp(
        svg=result.svg,
        ir={
            "revision": result.graph.revision,
            "nodes": [asdict(n) for n in result.graph.nodes],
            "edges": [asdict(e) for e in result.graph.edges],
        },
        trace=[
            {"stage": t.stage, "message": t.message, "refs": t.refs}
            for t in result.trace
        ],
    )


@app.get("/health")
def health():
    return {"status": "ok", "sessions": len(_sessions), "canvases": len(REGISTRY.list())}


@app.get("/ontology")
def ontology():
    return {
        "version": "v2",
        "relations": [
            {"relation": rel, "visual_pattern": pat}
            for rel, pat in _RELATION_PATTERN.items()
        ],
    }


@app.post("/render", response_model=RenderResp)
def render(req: RenderReq):
    if not req.text.strip():
        raise HTTPException(400, "text must be non-empty")
    result = run_pipeline(req.text, utterance_id=req.utterance_id)
    return _serialize(result)


@app.post("/render/session/{sid}", response_model=RenderResp)
def render_session(sid: str, req: RenderReq):
    if not req.text.strip():
        raise HTTPException(400, "text must be non-empty")
    with _sessions_lock:
        prev = _sessions.get(sid)
    result = run_pipeline(req.text, utterance_id=sid, graph=prev)
    with _sessions_lock:
        _sessions[sid] = result.graph
    return _serialize(result)


@app.delete("/session/{sid}")
def clear_session(sid: str):
    with _sessions_lock:
        existed = _sessions.pop(sid, None) is not None
    return {"cleared": existed, "sid": sid}


# ---------------------------------------------------------------------------
# Canvas viewer API — read-only over HTTP.
#
# Mutations are made through the MCP tool surface, which calls into the same
# CanvasRegistry singleton.  Tools intentionally do NOT have an HTTP entry
# point: only the host LLM should be writing.
# ---------------------------------------------------------------------------

@app.get("/canvases")
def list_canvases():
    return {"canvases": REGISTRY.list()}


@app.get("/canvas/{cid}/view", response_class=HTMLResponse)
def canvas_view(cid: str):
    try:
        REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")
    html_path = _STATIC_DIR / "canvas.html"
    if not html_path.exists():
        raise HTTPException(500, "canvas.html template missing")
    html = html_path.read_text(encoding="utf-8")
    # inject the canvas id into the body so the script can pick it up
    html = html.replace("<body>", f'<body data-cid="{cid}">', 1)
    return HTMLResponse(html)


@app.get("/canvas/{cid}/svg")
def canvas_svg(cid: str):
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")
    with c.lock:
        svg = c.svg
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/canvas/{cid}/narration.wav")
def canvas_narration_wav(cid: str):
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")
    with c.lock:
        wav_path = c.narration_wav
    if not wav_path or not Path(wav_path).is_file():
        raise HTTPException(404, "no narration generated yet")
    return FileResponse(wav_path, media_type="audio/wav")


@app.get("/canvas/{cid}/intro.wav")
def canvas_intro_wav(cid: str):
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")
    with c.lock:
        wav_path = c.intro_wav
    if not wav_path or not Path(wav_path).is_file():
        raise HTTPException(404, "no intro generated yet")
    return FileResponse(wav_path, media_type="audio/wav")


@app.get("/canvas/{cid}/narration.json")
def canvas_narration_manifest(cid: str):
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")
    with c.lock:
        manifest = c.narration_manifest
    if not manifest:
        return {"duration_s": 0.0, "phrases": []}
    return manifest


@app.get("/canvas/{cid}/state")
def canvas_state(cid: str):
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")
    with c.lock:
        return {
            "canvas_id": c.canvas_id,
            "revision": c.revision,
            "node_count": len(c.graph.nodes),
            "edge_count": len(c.graph.edges),
            "math_mode": c.math_mode,
            "width": c.width,
            "height": c.height,
            "updated_at": c.updated_at,
            "svg": c.svg,
            "narration": c.narration_manifest,
            "has_narration": c.narration_wav is not None,
            "has_intro": c.intro_wav is not None,
            "intro_duration_s": c.intro_duration_s,
            "intro_text": c.intro_text,
        }


@app.get("/canvas/{cid}/events")
async def canvas_events(cid: str, request: Request):
    """Server-Sent Events stream — one `render` event per revision bump."""
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")

    async def event_gen():
        last_rev = -1
        while True:
            if await request.is_disconnected():
                break
            with c.lock:
                rev = c.revision
                payload = None
                if rev != last_rev:
                    payload = {
                        "canvas_id": c.canvas_id,
                        "revision": rev,
                        "node_count": len(c.graph.nodes),
                        "edge_count": len(c.graph.edges),
                        "updated_at": c.updated_at,
                        "svg": c.svg,
                        "narration": c.narration_manifest,
                        "has_narration": c.narration_wav is not None,
                        "has_intro": c.intro_wav is not None,
                        "intro_duration_s": c.intro_duration_s,
                        "intro_text": c.intro_text,
                    }
            if payload is not None:
                last_rev = rev
                yield {"event": "render", "data": _to_json(payload)}
            await asyncio.sleep(0.1)

    return EventSourceResponse(event_gen())


def _to_json(obj) -> str:
    import json
    return json.dumps(obj, separators=(",", ":"))
