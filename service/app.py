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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Populate env from Secrets Manager (AWS) or .env (local) BEFORE any
# downstream module reads OPENAI_API_KEY / SEVIM_VLLM_* / SEVIM_TELEMETRY_DB.
# Idempotent and safe across all entry points (uvicorn direct, python -m
# studio, python -m mcp_server, container CMD).
from service.secrets import bootstrap as _bootstrap_secrets  # noqa: E402
_bootstrap_secrets()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, Response  # noqa: E402
from sse_starlette.sse import EventSourceResponse  # noqa: E402

from sevim.s3_map import _RELATION_PATTERN  # noqa: E402

from .canvas import REGISTRY  # noqa: E402

# Studio router: direct-to-LLM voice tutor surface (Anthropic / OpenAI /
# local vLLM).  Imported lazily after REGISTRY so the studio module sees
# the same singleton as the MCP path.
try:
    from studio.app import router as _studio_router
    _STUDIO_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001 — studio is optional
    _studio_router = None
    _STUDIO_AVAILABLE = False
    _STUDIO_IMPORT_ERROR = str(_exc)


app = FastAPI(
    title="SeVim",
    description="Figure runtime: structured spec → live canvas + audio "
                "narration.  Drive via the MCP tools or the Studio chat "
                "surface; this app exposes the /canvas/* viewer endpoints.",
    version="0.3.0",
)

if _STUDIO_AVAILABLE:
    app.include_router(_studio_router)

# Public marketing-side routes: contact form (with captcha + SES send)
# and the terms page.  Both serve unauthenticated.
from service.contact import router as _contact_router  # noqa: E402
app.include_router(_contact_router)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def root():
    """Public landing page — explains what Sevim is, gives a CTA to
    sign in, and contains the SEO meta + structured data crawlers need
    to surface us in search results.  Authenticated users still get
    Studio one click away via the Sign-in button."""
    landing = _STATIC_DIR / "landing.html"
    if not landing.exists():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/studio", status_code=302)
    return FileResponse(
        landing, media_type="text/html",
        headers={"cache-control": "public, max-age=300"},
    )


@app.get("/terms", include_in_schema=False)
def terms_page():
    """Public terms-and-conditions page.  Static HTML, served from disk
    so we can edit copy without touching Python."""
    terms = _STATIC_DIR / "terms.html"
    if not terms.exists():
        raise HTTPException(500, "terms.html missing")
    return FileResponse(
        terms, media_type="text/html",
        headers={"cache-control": "public, max-age=3600"},
    )


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    """Search-engine policy: index the landing, the FAQ anchors are
    fine, but stay out of the Studio app, the canvas viewers, and
    every internal API surface (those need auth or are user-data)."""
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /contact\n"
        "Allow: /terms\n"
        "Disallow: /studio\n"
        "Disallow: /studio/\n"
        "Disallow: /canvas/\n"
        "Disallow: /canvases\n"
        "Disallow: /ontology\n"
        "Disallow: /health\n"
        "\n"
        "Sitemap: https://khayyammath.com/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain",
                    headers={"cache-control": "public, max-age=86400"})


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml():
    """One URL today: the landing.  We'll add more as we grow public
    content (a /pricing page, a /docs page, etc.)."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url>\n'
        '    <loc>https://khayyammath.com/</loc>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>1.0</priority>\n'
        '  </url>\n'
        '  <url>\n'
        '    <loc>https://khayyammath.com/contact</loc>\n'
        '    <changefreq>monthly</changefreq>\n'
        '    <priority>0.5</priority>\n'
        '  </url>\n'
        '  <url>\n'
        '    <loc>https://khayyammath.com/terms</loc>\n'
        '    <changefreq>yearly</changefreq>\n'
        '    <priority>0.3</priority>\n'
        '  </url>\n'
        '</urlset>\n'
    )
    return Response(content=body, media_type="application/xml",
                    headers={"cache-control": "public, max-age=86400"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """1x1 transparent PNG — silences browsers' /favicon.ico requests
    so they don't show 404s in the tab and don't pollute the auth
    handler's logs.  Replace with a real icon when we have a brand."""
    from fastapi.responses import Response
    # 67-byte 1×1 transparent PNG
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
        b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc"
        b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return Response(content=png, media_type="image/png",
                    headers={"cache-control": "public, max-age=86400"})


@app.get("/health")
def health():
    return {"status": "ok", "canvases": len(REGISTRY.list())}


@app.get("/ontology")
def ontology():
    return {
        "version": "v2",
        "relations": [
            {"relation": rel, "visual_pattern": pat}
            for rel, pat in _RELATION_PATTERN.items()
        ],
    }


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


_CANVAS_404_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Canvas not found</title>
<style>
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif; background: #fff; color: #444;
    display: flex; align-items: center; justify-content: center;
    height: 100dvh; padding: 1em; text-align: center; }
  .card { max-width: 360px; }
  h1 { font-size: 1.05em; color: #222; margin: 0 0 0.4em 0; }
  p  { font-size: 0.9em; line-height: 1.4; }
</style></head><body><div class="card">
  <h1>This figure is no longer available.</h1>
  <p>The server was restarted since this canvas was created.
  Ask a new question in chat and a fresh figure will appear here.</p>
</div></body></html>"""


@app.get("/canvas/{cid}/view", response_class=HTMLResponse)
def canvas_view(cid: str):
    try:
        REGISTRY.get(cid)
    except KeyError:
        # Friendly HTML 404 — without this the iframe would render
        # the raw FastAPI JSON ({"detail": "canvas ... not found"})
        # which the user reported as a broken UX after a deploy
        # invalidated their stored canvas id.
        return HTMLResponse(_CANVAS_404_HTML, status_code=404)
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


def _serve_canvas_audio(cid: str, kind: str, local_path: str | None):
    """Shared serve-or-redirect logic for narration / intro WAVs.

    Local FileResponse when the file is on disk; else a 302 redirect to a
    presigned S3 URL when the storage backend is remote and an upload
    succeeded earlier (Fargate task replacement scenario); else 404.
    """
    if local_path and Path(local_path).is_file():
        return FileResponse(local_path, media_type="audio/wav")
    from service.storage import get_storage
    from fastapi.responses import RedirectResponse
    storage = get_storage()
    if storage.is_remote():
        url = storage.presigned_get_url(f"{cid}/{kind}.wav")
        if url:
            return RedirectResponse(url, status_code=302)
    raise HTTPException(404, f"no {kind} generated yet")


@app.get("/canvas/{cid}/narration.wav")
def canvas_narration_wav(cid: str):
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")
    with c.lock:
        wav_path = c.narration_wav
    return _serve_canvas_audio(cid, "narration", wav_path)


@app.get("/canvas/{cid}/intro.wav")
def canvas_intro_wav(cid: str):
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, f"canvas {cid!r} not found")
    with c.lock:
        wav_path = c.intro_wav
    return _serve_canvas_audio(cid, "intro", wav_path)


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
            "transition_text": c.transition_text,
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
                        "transition_text": c.transition_text,
                    }
            if payload is not None:
                last_rev = rev
                yield {"event": "render", "data": _to_json(payload)}
            await asyncio.sleep(0.1)

    return EventSourceResponse(event_gen())


def _to_json(obj) -> str:
    import json
    return json.dumps(obj, separators=(",", ":"))
