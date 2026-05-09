"""Sevim Studio — standalone tutor surface.

Bypasses Claude Code / MCP / JSON-RPC and talks directly to the
Anthropic Messages API with sevim tools wired up as function-call
definitions.  Frontend captures user voice via the Web Speech API,
streams Claude's response token-by-token into the browser's TTS,
and renders the canvas inline.

Goal: collapse the host-LLM-as-middleman so time-to-first-audio
drops from ~100 s (TTFT of the giant Claude Code system prompt
through MCP) to ~1 s (direct streaming API).

Two layers:

  studio/app.py        — FastAPI router mounted at /studio/* into
                         the existing service/app.py uvicorn server
  studio/static/...    — single-page voice-and-canvas frontend
"""
