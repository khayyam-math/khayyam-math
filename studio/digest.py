"""Compact canvas-state digest for realtime agent context.

A realtime voice tutor needs to *know what the user is currently
looking at* on every turn — otherwise it can't answer "why is that
point off the line?" or "make the green circle bigger".  The agent's
context window is precious in the realtime path (latency budget, not
token budget), so the digest is deliberately compact:

  * one-line summary line (canvas id, node/edge counts, mode)
  * node list: id  primitive  label  coords/size hints
  * edge list: id  src→dst  relation
  * any L1 warnings since last turn

Optionally, a low-resolution PNG snapshot for vision-capable models.

The PNG is opt-in because rasterising every turn costs ~50 ms; the
text digest alone is enough for most "discuss the figure" turns.
"""
from __future__ import annotations

import base64
import io
from typing import Any

from service.canvas import Canvas


def text_digest(c: Canvas, *, max_nodes: int = 60, max_edges: int = 80) -> str:
    """Compact human-and-LLM-readable summary of the canvas."""
    with c.lock:
        nodes = list(c.graph.nodes)
        edges = list(c.graph.edges)
        warnings = list(c.warnings)
        last_error = c.last_error
        rev = c.revision
        mode = "math" if c.math_mode else "concept"
        anim = "animated" if c.animate else "static"

    lines: list[str] = [
        f"canvas={c.canvas_id} rev={rev} mode={mode} {anim} "
        f"nodes={len(nodes)} edges={len(edges)} {c.width}x{c.height}",
    ]

    if last_error:
        lines.append(f"render_error: {last_error}")

    lines.append("nodes:")
    for n in nodes[:max_nodes]:
        coord = ""
        if "x" in n.meta and "y" in n.meta:
            coord = f"@({n.meta['x']:g},{n.meta['y']:g})"
        elif "cx" in n.meta and "cy" in n.meta:
            r = n.meta.get("r", "")
            coord = f"@({n.meta['cx']:g},{n.meta['cy']:g})r={r}"
        kind = n.meta.get("kind") or n.node_type
        label = (n.label or "").replace("\n", " ")[:40]
        lines.append(f"  {n.id}  {kind}  {label!r}{coord}")
    if len(nodes) > max_nodes:
        lines.append(f"  …+{len(nodes) - max_nodes} more")

    lines.append("edges:")
    for e in edges[:max_edges]:
        lines.append(f"  {e.id}  {e.from_id}→{e.to_id}  {e.relation}")
    if len(edges) > max_edges:
        lines.append(f"  …+{len(edges) - max_edges} more")

    if warnings:
        lines.append("warnings:")
        for w in warnings[:8]:
            lines.append(f"  {w.get('code','?')}: {w.get('message','')[:160]}")

    return "\n".join(lines)


def png_thumbnail(c: Canvas, width: int = 480) -> bytes | None:
    """Return a low-res PNG of the current SVG, or None on failure.

    Vision-capable models can ingest this directly.  Width is small
    (480 px) to keep token cost low — the agent only needs gist-level
    visual understanding, not pixel-perfect inspection.
    """
    with c.lock:
        svg = c.svg
    if not svg:
        return None
    try:
        import cairosvg
        return cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            output_width=width,
        )
    except Exception:  # noqa: BLE001 — vision degrades to text-only
        return None


def digest_for_agent(c: Canvas, *, include_png: bool = False) -> dict[str, Any]:
    """Bundle the digest into a JSON-friendly dict for transport.

    Use ``include_png=True`` when the realtime model is vision-capable
    and you want it to *see* the canvas on this turn.  Cost is ~50 ms
    of CPU + base64-encoded PNG bytes (typically 5-30 KB).
    """
    out: dict[str, Any] = {
        "canvas_id": c.canvas_id,
        "revision": c.revision,
        "text": text_digest(c),
    }
    if include_png:
        png = png_thumbnail(c)
        if png is not None:
            out["png_b64"] = base64.b64encode(png).decode("ascii")
            out["png_media_type"] = "image/png"
    return out
