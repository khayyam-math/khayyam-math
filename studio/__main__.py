"""Launch Sevim Studio with browser auto-open.

Two scenarios this handles:

1. **Claude Code is already running.**  The MCP subprocess has already
   bound port 7777 and serves /studio.  We just open the browser tab.

2. **Standalone Studio.**  No Claude Code, no MCP subprocess.  We
   start uvicorn ourselves on 7777 and then open the browser.

Run with:

    python -m studio              # honour SEVIM_HTTP_PORT (default 7777)
    sevim-studio                  # console-script entry point

Env knobs:
    SEVIM_HTTP_PORT     viewer + studio bind port (default 7777)
    SEVIM_NO_BROWSER    set to skip the auto-launch (CI / SSH)
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from service.secrets import bootstrap as _bootstrap_secrets

_bootstrap_secrets()

from service.app import app  # noqa: E402 — import after env is populated


def _port() -> int:
    return int(os.environ.get("SEVIM_HTTP_PORT", "7777"))


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _url() -> str:
    return f"http://127.0.0.1:{_port()}/studio"


def _open_when_ready() -> None:
    """Wait for the server to accept connections, then open the browser."""
    for _ in range(50):
        if _port_in_use(_port()):
            break
        time.sleep(0.1)
    if os.environ.get("SEVIM_NO_BROWSER") == "1":
        return
    webbrowser.open(_url(), new=2)


def main() -> None:
    port = _port()
    url = _url()

    if _port_in_use(port):
        print(f"[sevim-studio] :{port} already serving Sevim — opening browser")
        if os.environ.get("SEVIM_NO_BROWSER") != "1":
            webbrowser.open(url, new=2)
        return

    print(f"[sevim-studio] starting standalone uvicorn on :{port}")
    print(f"[sevim-studio] opening {url}")
    threading.Thread(target=_open_when_ready, name="sevim-studio-open",
                     daemon=True).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
