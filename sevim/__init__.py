"""SeVim — deterministic semantic-to-visual mapping pipeline.

Public entry points
-------------------
``run_pipeline(text, utterance_id="u0", graph=None) -> PipelineResult``
    Convert a natural-language string to a deterministic SVG diagram.
    Identical (text, frozen-parameter) pairs always produce byte-identical
    output. Pass ``graph=<previous_result.graph>`` to extend a diagram
    incrementally across clauses or turns.

``PipelineResult``
    Bundle of ``(svg, graph, placed, trace)`` where ``svg`` is the
    canonical serialised SVG string, ``graph`` is the post-extraction
    SceneGraph, ``placed`` is the laid-out PlacedGraph, and ``trace`` is
    a list of per-stage TraceEvents for debugging.

The five stages are S1 Parse → S2 Extract → S3 Map → S4 Layout →
S5 Render. An optional S2b LLM-graph-improvement step (Claude Haiku) and
an optional S4.6 strict-non-overlap layout pass are gated behind
environment variables (``SEVIM_IMPROVE``, ``SEVIM_STRICT_LAYOUT``).

Citation
--------
If you use this package in your research, please cite the
SeVim paper.  See ``CITATION.cff`` and ``NOTICE`` at the
repository root.
"""
__version__ = "0.1.0"

from .ir import (
    PipelineResult, SceneGraph, SceneNode, SceneEdge,
    SpanRef, SpanToken, TraceEvent,
)
from .pipeline import run_pipeline

__all__ = [
    "__version__",
    "run_pipeline",
    "PipelineResult", "SceneGraph", "SceneNode", "SceneEdge",
    "SpanRef", "SpanToken", "TraceEvent",
]
