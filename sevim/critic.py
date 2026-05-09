"""Post-render quality audit for Sevim canvases.

Run after every batch of tool calls.  Returns a list of human-readable
issues the LLM can act on.  Empty list → canvas passes; non-empty list
is fed back to the model for self-correction.

Initial check (the one the user keeps hitting): caption leader endpoints
that don't terminate near any actual node.  Symptom: a dashed line goes
from the caption box to empty canvas.
"""
from __future__ import annotations

import math
from typing import Any


# Pixel distance from a caption's leader-line endpoint to the nearest
# node centre, above which we flag the caption as "pointing to nowhere."
# 60 px is generous — even thick point markers (~12 px diameter) leave
# plenty of room before the leader visually misses.
_LEADER_TOLERANCE_PX = 60.0


def audit_canvas(canvas) -> list[str]:
    """Return a list of issues with the canvas's current placed graph.

    Args:
        canvas: ``service.canvas.Canvas`` instance with a populated
            ``placed`` graph (post-render).

    Returns:
        Human-readable issue strings.  Empty list means the figure
        passes the audit.
    """
    issues: list[str] = []
    placed = getattr(canvas, "placed", None)
    if placed is None or not placed.shapes:
        return issues

    # Index points (and circles, set_blob centres) by their canvas-px
    # centre.  Captions' leader endpoints should land near one of these.
    anchor_centres: list[tuple[str, float, float]] = []
    for ps in placed.shapes:
        prim = ps.shape.primitive
        if prim in ("point", "circle", "set_blob", "rect", "ellipse"):
            cx = ps.x + ps.shape.width / 2
            cy = ps.y + ps.shape.height / 2
            anchor_centres.append((ps.shape.nid, cx, cy))

    if not anchor_centres:
        return issues

    for ps in placed.shapes:
        if ps.shape.primitive != "caption":
            continue
        endpoint = ps.shape.meta.get("leader_to_canvas")
        if not (isinstance(endpoint, (tuple, list)) and len(endpoint) == 2):
            # No leader → nothing to audit (caption is purely informational).
            continue
        ex, ey = float(endpoint[0]), float(endpoint[1])
        # Distance to nearest node centre.
        nearest_id, min_dist = _nearest(anchor_centres, ex, ey)
        if min_dist > _LEADER_TOLERANCE_PX:
            issues.append(
                f"caption {ps.shape.nid!r} has its leader-line target "
                f"({ex:.0f}, {ey:.0f}) at {min_dist:.0f}px from the "
                f"nearest node ({nearest_id!r}).  The leader line is "
                f"pointing to empty canvas.  Re-issue this add_caption "
                f"with x/y from sevim_plan's node_positions or "
                f"cluster_anchors output."
            )

    return issues


def _nearest(
    centres: list[tuple[str, float, float]],
    ex: float, ey: float,
) -> tuple[str, float]:
    best_id, best_d = centres[0][0], float("inf")
    for nid, cx, cy in centres:
        d = math.hypot(ex - cx, ey - cy)
        if d < best_d:
            best_id, best_d = nid, d
    return best_id, best_d
