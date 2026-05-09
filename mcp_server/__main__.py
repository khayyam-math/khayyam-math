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
    """Return ``preferred`` if free, else an OS-assigned free port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind((host, 0))
    port = s2.getsockname()[1]
    s2.close()
    return port


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
