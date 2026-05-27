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

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse, HTMLResponse, RedirectResponse, Response,
)
from sse_starlette.sse import EventSourceResponse  # noqa: E402

from sevim.s3_map import _RELATION_PATTERN  # noqa: E402

from .canvas import REGISTRY  # noqa: E402
from studio.auth import require_user  # noqa: E402

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
    title="Khayyam Math",
    description="Khayyam Math.",
    version="0.3.0",
    # No public API schema or Swagger/ReDoc UI.  The OpenAPI document
    # would hand an attacker the entire endpoint surface; disabled in
    # every environment.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# ---------------------------------------------------------------------------
# Security headers — added by a PURE ASGI middleware.
#
# This must NOT use Starlette's BaseHTTPMiddleware (the @app.middleware("http")
# decorator): BaseHTTPMiddleware buffers the entire response body before
# passing it on, which breaks streaming responses — the figure-generation
# Server-Sent Events stream and the /canvas/*/events stream would be held
# back until the whole 1-2 minute generation finished, so the load balancer
# saw no bytes and returned 504.  A pure ASGI middleware only rewrites the
# `http.response.start` message and lets every body chunk pass straight
# through, so streaming is untouched.
# ---------------------------------------------------------------------------

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' data: https://cdn.jsdelivr.net; "
    "img-src 'self' data: blob:; "
    "media-src 'self' https://*.amazonaws.com; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; base-uri 'self'; form-action 'self'; "
    "object-src 'none'"
)

_SECURITY_HEADERS = [
    (b"content-security-policy", _CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"permissions-policy", b"geolocation=(), microphone=(self), camera=()"),
    (b"server", b"Khayyam Math"),
]
_MANAGED_HEADER_NAMES = {name for name, _ in _SECURITY_HEADERS}


class _SecurityHeadersMiddleware:
    """Append hardening headers without buffering the response body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for (k, v) in message.get("headers", [])
                    if k.lower() not in _MANAGED_HEADER_NAMES
                ]
                headers.extend(_SECURITY_HEADERS)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(_SecurityHeadersMiddleware)


if _STUDIO_AVAILABLE:
    app.include_router(_studio_router)

# Public marketing-side routes: contact form (with captcha + SES send)
# and the terms page.  Both serve unauthenticated.
from service.contact import router as _contact_router  # noqa: E402
app.include_router(_contact_router)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------------------
# Client-asset hardening: strip the explanatory comments out of the HTML we
# serve.  The source files keep their comments (they document non-obvious
# rendering decisions for maintainers) but the bytes that reach the browser
# carry no architectural narration for someone reading "view source".
# ---------------------------------------------------------------------------

import re as _re  # noqa: E402

_HTML_COMMENT = _re.compile(r"<!--.*?-->", _re.DOTALL)
_BLOCK_COMMENT = _re.compile(r"/\*.*?\*/", _re.DOTALL)
_LINE_COMMENT = _re.compile(r"^[ \t]*//.*$", _re.MULTILINE)
_BLANK_LINES = _re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")


def _strip_client_comments(html: str) -> str:
    """Remove HTML/CSS/JS comments from a page before it is served.

    Conservative on purpose: only ``<!-- -->`` blocks, ``/* */`` blocks,
    and lines that are *entirely* a ``//`` comment are removed — an
    inline trailing ``//`` is left alone so a URL or a regex literal is
    never corrupted.
    """
    html = _HTML_COMMENT.sub("", html)
    html = _BLOCK_COMMENT.sub("", html)
    html = _LINE_COMMENT.sub("", html)
    html = _BLANK_LINES.sub("\n", html)
    return html


def _require_canvas(cid: str, request: Request):
    """Fetch a canvas, enforcing sign-in + ownership.

    Raises 401 when the caller is not signed in (production), and 404
    for both a missing canvas AND one owned by another user — a 404
    rather than 403 so the response cannot be used to confirm that a
    canvas id exists.
    """
    user = require_user(request)
    try:
        c = REGISTRY.get(cid)
    except KeyError:
        raise HTTPException(404, "not found")
    if c.owner is not None and c.owner != user:
        raise HTTPException(404, "not found")
    return c


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


@app.get("/screenshots/{name}", include_in_schema=False)
def screenshot(name: str):
    """Serve landing-page gallery screenshots from service/static/screenshots/.

    Whitelisted by filename pattern to prevent path traversal.
    """
    import re
    if not re.fullmatch(r"[A-Za-z0-9_\-]+\.png", name):
        raise HTTPException(404)
    path = _STATIC_DIR / "screenshots" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(
        path, media_type="image/png",
        headers={"cache-control": "public, max-age=86400, immutable"},
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


@app.get("/health", include_in_schema=False)
async def health():
    # Load-balancer health check only.  Deliberately exposes no
    # internal state (canvas counts, model, backend).  Explicitly
    # async so it never goes through uvicorn's thread pool — a
    # CPU-busy express request taking thread-pool capacity must
    # not delay ALB's 5s health check, or the container gets
    # killed mid-figure (the "no figure appeared" failure mode).
    return {"status": "ok"}


@app.get("/ontology", include_in_schema=False)
def ontology(_user: str = Depends(require_user)):
    # Internal vocabulary — sign-in required so it isn't a free
    # reference for someone reverse-engineering the figure schema.
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

@app.get("/canvases", include_in_schema=False)
def list_canvases(request: Request, user: str = Depends(require_user)):
    # Only the caller's own canvases — never enumerate everyone's.
    out = []
    for info in REGISTRY.list():
        cid = info.get("canvas_id")
        if not cid:
            continue
        try:
            cv = REGISTRY.get(cid)
        except KeyError:
            continue
        if cv.owner is None or cv.owner == user:
            out.append(info)
    return {"canvases": out}


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


@app.get("/canvas/{cid}/view", response_class=HTMLResponse,
         include_in_schema=False)
def canvas_view(cid: str, request: Request):
    try:
        _require_canvas(cid, request)
    except HTTPException as exc:
        if exc.status_code == 401:
            # Not signed in — send them to the login page rather than
            # rendering a raw JSON 401 in the browser tab.
            return RedirectResponse("/studio/auth/login", status_code=302)
        # Friendly HTML 404 — without this the iframe would render the
        # raw FastAPI JSON, which is a broken UX after a deploy
        # invalidated a stored canvas id.
        return HTMLResponse(_CANVAS_404_HTML, status_code=404)
    html_path = _STATIC_DIR / "canvas.html"
    if not html_path.exists():
        raise HTTPException(500, "canvas template missing")
    html = _strip_client_comments(html_path.read_text(encoding="utf-8"))
    # inject the canvas id into the body so the script can pick it up
    html = html.replace("<body>", f'<body data-cid="{cid}">', 1)
    return HTMLResponse(html)


@app.get("/canvas/{cid}/svg", include_in_schema=False)
def canvas_svg(cid: str, request: Request):
    c = _require_canvas(cid, request)
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


@app.get("/canvas/{cid}/narration.wav", include_in_schema=False)
def canvas_narration_wav(cid: str, request: Request):
    c = _require_canvas(cid, request)
    with c.lock:
        wav_path = c.narration_wav
    return _serve_canvas_audio(cid, "narration", wav_path)


@app.get("/canvas/{cid}/intro.wav", include_in_schema=False)
def canvas_intro_wav(cid: str, request: Request):
    c = _require_canvas(cid, request)
    with c.lock:
        wav_path = c.intro_wav
    return _serve_canvas_audio(cid, "intro", wav_path)


@app.get("/canvas/{cid}/narration.json", include_in_schema=False)
def canvas_narration_manifest(cid: str, request: Request):
    c = _require_canvas(cid, request)
    with c.lock:
        manifest = c.narration_manifest
    if not manifest:
        return {"duration_s": 0.0, "phrases": []}
    return manifest


@app.get("/canvas/{cid}/state", include_in_schema=False)
def canvas_state(cid: str, request: Request):
    c = _require_canvas(cid, request)
    with c.lock:
        return {
            "canvas_id": c.canvas_id,
            "revision": c.revision,
            "node_count": (c.raw_node_count if c.raw_node_count is not None
                           else len(c.graph.nodes)),
            "edge_count": (c.raw_edge_count if c.raw_edge_count is not None
                           else len(c.graph.edges)),
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


@app.get("/canvas/{cid}/events", include_in_schema=False)
async def canvas_events(cid: str, request: Request):
    """Server-Sent Events stream — one `render` event per revision bump."""
    c = _require_canvas(cid, request)

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
                        "node_count": (
                            c.raw_node_count if c.raw_node_count is not None
                            else len(c.graph.nodes)
                        ),
                        "edge_count": (
                            c.raw_edge_count if c.raw_edge_count is not None
                            else len(c.graph.edges)
                        ),
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
