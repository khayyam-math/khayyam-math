"""SeVim — figure runtime: structured spec → live canvas + audio narration.

What this package is now (v0.3+):
--------------------------------
A deterministic rendering + narration runtime for math/concept figures.
Drive it via the MCP tools in ``mcp_server`` or the Studio web app in
``studio`` — both speak a structured tool API (``sevim_open``,
``sevim_plan``, ``sevim_apply``, ``sevim_narrate``) and let any modern
LLM (Claude, GPT-4o, Gemini, local Qwen via vLLM) build figures by
calling tools rather than emitting SVG directly.

What this package is NOT anymore:
---------------------------------
v0.1/v0.2 included a natural-language → diagram pipeline (S1 parse,
S2 relation extraction, S2b LLM improvement, ``run_pipeline``
orchestration, sentence-transformers embeddings).  That layer was made
obsolete by tool-using LLMs, which produce structural specs directly
with much higher quality than the in-house extractor.  The legacy NLP
modules were removed in v0.3.

Modules kept (the figure runtime):
  * ir              — IR dataclasses (SceneGraph, VisualGraph, PlacedGraph)
  * s3_map          — assigns visual primitives to scene nodes
  * s4_layout       — Sugiyama layout for concept diagrams
  * s4_geo_layout   — coordinate-honoring layout for math figures + caption
                      placement (constraint-based, overlap-aware)
  * s5_render       — deterministic SVG serialisation with overlap critic
  * plan            — sevim_plan: cluster / radial coordinate planner
  * narrate         — piper TTS + phrase-timed narration manifest
  * overlap         — algorithmic overlap critic (shape + label bleed)
  * geo_check       — math_mode coordinate validator
  * strict_layout   — opt-in strict no-overlap layout pass
"""
__version__ = "0.3.0"

from .ir import (
    SceneGraph, SceneNode, SceneEdge,
    SpanRef, SpanToken, TraceEvent,
)

__all__ = [
    "__version__",
    "SceneGraph", "SceneNode", "SceneEdge",
    "SpanRef", "SpanToken", "TraceEvent",
]
