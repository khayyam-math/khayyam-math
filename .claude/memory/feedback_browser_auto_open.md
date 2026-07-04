---
name: Sevim should auto-open the browser, not return a click-me URL
description: User expects the canvas viewer to open automatically when sevim_open is called.
type: feedback
originSessionId: b750514c-b82c-4fee-9ab5-0f4710523a32
---
When ``sevim_open`` creates a canvas, the user's default browser should
be spawned onto the view_url automatically.  Don't ask the user to click
a URL.

**Why:** User explicitly requested this — quote: *"I want the browser to
automatically open, not to receive a url on which I have to click
myself."*  The Sevim MCP server runs locally on the user's machine and
can call ``webbrowser.open`` directly; making the user click is friction.

**How to apply:** Default ``auto_open=True`` on ``sevim_open``.  Call
``webbrowser.open(view_url, new=2)`` once per canvas (track via
``Canvas.browser_opened`` to prevent repeat-launches).  Provide
``SEVIM_NO_BROWSER=1`` env var as an escape hatch for headless setups,
and an ``auto_open=False`` per-call override.  When prompting the host
LLM via FastMCP instructions / CLAUDE.md, tell it explicitly NOT to
instruct the user to click the URL — the browser is already opening.
