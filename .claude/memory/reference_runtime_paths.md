---
name: Sevim runtime paths and config
description: Where Sevim stores voice models, canvas data, and how its MCP server is registered with Claude Code on this machine.
type: reference
originSessionId: b750514c-b82c-4fee-9ab5-0f4710523a32
---
Persistent runtime locations on the user's machine:

  - **Voice models** (piper-tts ONNX + companion JSON):
    ``~/.local/share/sevim/voices/``.  Default voice file:
    ``en_US-lessac-medium.onnx`` (61 MB, downloaded from
    ``huggingface.co/rhasspy/piper-voices``).  Override path with
    ``SEVIM_VOICE_MODEL`` env var.

  - **Per-canvas narration WAVs**:
    ``~/.local/share/sevim/canvases/<canvas_id>/narration.wav``.
    Override base with ``SEVIM_DATA_DIR``.

  - **MCP registration** (Claude Code, user scope):
    Registered as ``sevim`` via
    ``claude mcp add sevim --scope user -- /home/ara/.local/bin/uv run
    --directory /home/ara/Documents/Programming/sevim_plugin python -m
    mcp_server``.  The config lives in ``~/.claude.json``.  Verify
    health with ``claude mcp list``.

  - **User-level CLAUDE.md** at ``~/.claude/CLAUDE.md`` contains the
    sevim usage guidance (visualisation preference, animate/narrate
    workflow).

Environment knobs:

  - ``SEVIM_HTTP_HOST`` / ``SEVIM_HTTP_PORT`` — viewer bind (default
    127.0.0.1:7777).
  - ``SEVIM_MCP_HOST`` / ``SEVIM_MCP_PORT`` — http-transport MCP bind
    (default 127.0.0.1:8765).
  - ``SEVIM_NO_BROWSER=1`` — disable auto-open (headless / CI / SSH).
  - ``SEVIM_VOICE_MODEL`` — override piper model path.
  - ``SEVIM_DATA_DIR`` — override per-canvas data root.
