---
name: Sevim pivot to MCP plugin (May 2026)
description: Sevim was repurposed in May 2026 from a Lyceum-integrated diagram backend into a standalone MCP plugin that connects to host LLMs (Claude, ChatGPT, Gemini), with geometric layout, captions, fade-in animation, and voice narration.
type: project
originSessionId: b750514c-b82c-4fee-9ab5-0f4710523a32
---
In May 2026 Sevim's direction changed: it was decoupled from Lyceum (its
former real-time diagram consumer) and rearchitected as an MCP plugin so
the host LLM (Claude Desktop, Claude Code, ChatGPT Apps SDK, Gemini CLI,
Cursor, Zed, etc.) calls Sevim directly to visualise its own reasoning —
the original use case being math-question explanations.

Concrete state as of 2026-05-08:

  - **MCP plugin layer**: ``mcp_server/`` (FastMCP server, 12 tools) +
    ``service/`` (FastAPI viewer, SSE live updates).  Stdio transport
    works in Claude Code; ``--transport http`` works for claude.ai web
    via Custom Connector + cloudflared/ngrok tunnel.
  - **Geometric layout**: ``sevim/s4_geo_layout.py`` honours math
    coordinates (x/y, cx/cy/r) when canvas opened with math_mode=True,
    bypassing Sugiyama.  Required because Sugiyama places nodes by
    edge topology, not by their geometric meaning.
  - **Closed-loop visual feedback**: L1 algorithmic critic
    (``sevim/geo_check.py``) catches lies_on violations etc; L2
    ``sevim_review`` tool returns a PNG so vision-capable LLMs can
    self-correct before showing the user.
  - **Captions in margins** with dashed leader lines back to anchor
    points — captions never cover the figure (user preference).
  - **Animate=True**: SMIL fade-in injection via regex post-processor
    in ``service/canvas.py:_apply_fade_in``.
  - **Voice narration**: piper-tts (en_US-lessac-medium voice at
    ``~/.local/share/sevim/voices/``).  Per-phrase synthesis →
    phrase-accurate timing without estimation.
  - **Browser auto-open**: sevim_open spawns the user's default
    browser onto the view_url on first creation.

**Why:** The host LLM is smart enough to pick relations and primitives
itself, so Sevim's S2 extractor (regex + dep-parse cascade) becomes
optional.  Structured construction via add_node/add_edge bypasses S2
entirely; NL pass-through (sevim_describe) remains as an "easy mode".

**How to apply:** Treat the MCP plugin layer (mcp_server/ + service/)
as the primary product surface.  The legacy /render API and the S2b
Haiku improvement step are kept for backward compatibility with the
existing eval harness but are no longer the strategic focus.  Local
GitHub remote was disconnected in May 2026 in preparation for a new
repo for the new direction.
