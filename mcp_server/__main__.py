"""Sevim MCP server entry point.

Two transports, picked at startup:

  python -m mcp_server                       # stdio (default — Claude Code,
                                             # Claude Desktop, Cursor, Zed)

  python -m mcp_server --transport http      # streamable-http MCP on
                                             # SEVIM_MCP_PORT (default 8765),
                                             # plus the viewer on
                                             # SEVIM_HTTP_PORT (default 7777)

In **stdio mode**: the host LLM (Claude Code etc.) launches us as a
subprocess and speaks JSON-RPC over stdin/stdout.  uvicorn runs on a
background thread so the live viewer is reachable at
``http://127.0.0.1:7777/canvas/<id>/view``.

In **http mode**: there's no host-launched subprocess — the user runs us
once as a daemon, exposes the MCP port via a tunnel (cloudflared / ngrok)
or hosted URL, and registers that public URL as a Custom Connector in
claude.ai or as an App in ChatGPT.  The viewer stays bound to localhost
because only the user's own browser needs to reach it.

Environment variables
---------------------
  SEVIM_HTTP_HOST   default 127.0.0.1
  SEVIM_HTTP_PORT   default 7777   (live viewer)
  SEVIM_MCP_PORT    default 8765   (MCP streamable-http endpoint, http mode)
  SEVIM_MCP_HOST    default 127.0.0.1 (override to 0.0.0.0 if your tunnel
                                       can't reach 127.0.0.1)
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time

import uvicorn

from .server import configure, get_default_host, get_default_port, mcp, run_stdio


def _pick_port(host: str, preferred: int) -> int:
    """Return ``preferred`` if free; refuse with an actionable message otherwise.

    Why we don't fall back to a random port any more:
    Chrome's autoplay permission is per-origin.  ``127.0.0.1:7777`` and
    ``127.0.0.1:33629`` are *different* origins as far as the browser
    is concerned, so a fresh random port forces the user to click the
    "Play narration" overlay every session.  Pinning the port lets the
    browser remember the permission once.

    Override the preferred port via ``SEVIM_HTTP_PORT`` if you really
    need a different one — but use a stable value, not 0.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
    raise SystemExit(
        f"[sevim-mcp] viewer port {preferred} on {host} is already bound.\n"
        f"  Another Sevim MCP subprocess is probably still running.  "
        f"Kill it with:  pkill -f 'python -m mcp_server'  "
        f"or set SEVIM_HTTP_PORT to a different stable port and restart."
    )


def _preheat_heavy_modules() -> None:
    """Import + warm up modules that otherwise stall the first tool call.

    Background-thread work, so the MCP ``initialize`` handshake responds
    immediately while these load in parallel.  Each block is wrapped in a
    broad try/except: missing-package or model-not-found situations
    fall back to the same lazy-load paths the rest of Sevim already
    handles, just at first-call instead of at startup.

    Why each one matters
    --------------------
    * spaCy ``en_core_web_sm`` (~50–150 ms): used by ``_extract_dep`` in
      sevim/s2_extract.py.  Loaded the first time the LLM calls
      ``sevim_describe``.
    * piper voice (~200–400 ms): mmaps the ONNX file in
      sevim/narrate.py.  Loaded the first time the LLM calls
      ``sevim_narrate``.
    * cairosvg (~150 ms): SVG → PNG for ``sevim_review`` vision feedback.
      Imported lazily inside mcp_server/server.py.
    """
    try:
        from sevim.s2_extract import _get_nlp
        _get_nlp()
    except Exception:  # noqa: BLE001 — keep going on partial environments
        pass
    try:
        from sevim.narrate import voice_available, _load_voice
        if voice_available():
            _load_voice()
    except Exception:  # noqa: BLE001
        pass
    try:
        import cairosvg  # noqa: F401 — import side-effect only
    except Exception:  # noqa: BLE001
        pass


def _open_studio_when_ready(host: str, port: int) -> None:
    """Wait for uvicorn to accept connections, then open the Studio URL."""
    import socket
    import time
    import webbrowser

    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.1)
    webbrowser.open(f"http://{host}:{port}/studio", new=2)


def _start_uvicorn(host: str, port: int) -> threading.Thread:
    """Start uvicorn (the canvas viewer) in a daemon thread."""
    config = uvicorn.Config(
        "service.app:app",
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, name="sevim-uvicorn-viewer", daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return t


def _run_streamable_http(host: str, port: int) -> None:
    """Run FastMCP's streamable-http transport on the foreground."""
    mcp.settings.host = host
    mcp.settings.port = port
    # Exposes the MCP endpoint at <host>:<port>/mcp by default.
    mcp.run("streamable-http")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sevim-mcp")
    p.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport.  stdio for desktop/CLI hosts (Claude Code, "
             "Claude Desktop, Cursor); http for remote hosts (claude.ai web "
             "Custom Connector, ChatGPT Apps SDK).",
    )
    return p.parse_args()


def main() -> None:
    # All logging to stderr; in stdio mode stdout is reserved for JSON-RPC.
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="[sevim-mcp %(levelname)s] %(message)s",
    )

    args = _parse_args()

    viewer_host = get_default_host()
    viewer_port = _pick_port(viewer_host, get_default_port())
    configure(host=viewer_host, port=viewer_port)

    # Always start the live viewer (localhost-only) in a background thread.
    _start_uvicorn(viewer_host, viewer_port)
    print(
        f"[sevim-mcp] live viewer at http://{viewer_host}:{viewer_port}/canvas/<id>/view",
        file=sys.stderr,
    )

    # Optional: auto-open the Sevim Studio tutor surface in the user's
    # browser the moment the MCP subprocess is up.  Off by default so
    # Claude-Code workflows aren't disrupted; opt in with
    # SEVIM_AUTO_STUDIO=1 (and SEVIM_NO_BROWSER=1 still suppresses it).
    if (os.environ.get("SEVIM_AUTO_STUDIO") == "1"
            and os.environ.get("SEVIM_NO_BROWSER") != "1"):
        threading.Thread(
            target=_open_studio_when_ready,
            args=(viewer_host, viewer_port),
            name="sevim-studio-open",
            daemon=True,
        ).start()
        print(
            f"[sevim-mcp] auto-opening Studio at "
            f"http://{viewer_host}:{viewer_port}/studio",
            file=sys.stderr,
        )

    # Warm heavy modules in a parallel daemon thread so the first
    # sevim_describe / sevim_narrate / sevim_review doesn't pay an
    # import + model-load cost.  The MCP ``initialize`` handshake runs
    # below this line and is unblocked.
    threading.Thread(
        target=_preheat_heavy_modules,
        name="sevim-preheat",
        daemon=True,
    ).start()

    if args.transport == "stdio":
        # Block on the MCP stdio transport until the host disconnects.
        run_stdio()
        return

    # http mode — bind the streamable-http MCP endpoint on the foreground.
    mcp_host = os.environ.get("SEVIM_MCP_HOST", "127.0.0.1")
    mcp_port = _pick_port(mcp_host, int(os.environ.get("SEVIM_MCP_PORT", "8765")))
    print(
        f"[sevim-mcp] MCP streamable-http transport at "
        f"http://{mcp_host}:{mcp_port}/mcp\n"
        f"[sevim-mcp] expose this URL via a tunnel (e.g. "
        f"`cloudflared tunnel --url http://{mcp_host}:{mcp_port}`) and\n"
        f"[sevim-mcp] register the public https://… URL as a Custom Connector "
        f"in claude.ai → Settings → Connectors.",
        file=sys.stderr,
    )
    _run_streamable_http(mcp_host, mcp_port)


if __name__ == "__main__":
    main()
