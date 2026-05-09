"""Algorithmic critic for Sevim canvases — Level 1 of the closed-loop
visual-feedback system.

Runs after every mutation and surfaces structured warnings the host LLM
can react to *without* needing vision capabilities.  Catches the most
common LLM mistakes when constructing geometric figures: points that
claim to lie on a circle but don't, duplicate coordinates, math-mode
canvases with no actual coordinates, etc.

Each warning is a JSON-friendly ``dict`` so it round-trips cleanly through
MCP tool results:

    {
        "code": "lies_on_violation",
        "message": "Point 'P' is marked as lying on 'Unit Circle' but its
                    actual distance to the centre is 0.866, expected
                    1.000 (diff 0.134).",
        "refs": {"point": "n_p", "curve": "n_unit_circle"},
    }

The host LLM is expected to read the warnings list, decide whether each
issue is intentional or a mistake, and emit corrections via further tool
calls.  Sevim does NOT auto-correct — false positives shouldn't silently
mutate the canvas.
"""
from __future__ import annotations

import math

from .ir import SceneGraph

# Tolerance for "point lies on a unit-radius curve" — math units.
_LIES_ON_EPS = 0.05

# Coordinate-precision used to detect duplicates.  Two points that round
# to the same key at this precision are flagged.
_COORD_PRECISION = 6


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check(g: SceneGraph, math_mode: bool) -> list[dict]:
    """Return a list of warnings for the current scene graph.

    Args:
        g: SceneGraph after the latest mutation.
        math_mode: True iff the canvas was opened in geometric layout
            mode.  Some checks (e.g. "no coords") only fire in math mode.

    Returns:
        Possibly-empty list of warning dicts.  Order is deterministic so
        a stable graph yields a stable warning list.
    """
    warnings: list[dict] = []

    nodes_by_id = {n.id: n for n in g.nodes}

    warnings.extend(_check_lies_on(g, nodes_by_id))
    warnings.extend(_check_duplicate_coords(g))
    warnings.extend(_check_math_mode_coverage(g, math_mode))
    warnings.extend(_check_dangling_edges(g, nodes_by_id))
    warnings.extend(_check_circle_radius(g))

    return warnings


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_lies_on(g: SceneGraph, nodes_by_id: dict) -> list[dict]:
    """Flag points marked `lies_on` a circle that don't actually lie on it.

    Tolerance ``_LIES_ON_EPS`` is in math units, so 0.05 means "within 5%
    of a unit-radius circle's perimeter."
    """
    out: list[dict] = []
    for e in g.edges:
        if e.relation != "lies_on":
            continue
        a = nodes_by_id.get(e.from_id)
        b = nodes_by_id.get(e.to_id)
        if a is None or b is None:
            continue
        # Try both orderings: (point, curve) and (curve, point).
        pair = _resolve_point_curve(a, b)
        if pair is None:
            continue
        point, curve = pair
        px = float(point.meta["x"])
        py = float(point.meta["y"])
        cx = float(curve.meta["cx"])
        cy = float(curve.meta["cy"])
        r = float(curve.meta.get("r", 0.0))
        d = math.hypot(px - cx, py - cy)
        diff = abs(d - r)
        if diff > _LIES_ON_EPS:
            out.append({
                "code": "lies_on_violation",
                "message": (
                    f"Point {point.label!r} is marked as lying on "
                    f"{curve.label!r} but its actual distance to the centre "
                    f"is {d:.3f}, expected {r:.3f} (off by {diff:.3f})."
                ),
                "refs": {
                    "point": point.id,
                    "curve": curve.id,
                    "actual_distance": d,
                    "expected_radius": r,
                },
            })
    return out


def _check_duplicate_coords(g: SceneGraph) -> list[dict]:
    """Two distinct *non-caption* nodes pinned to the same (x, y) — almost
    always a labelling mistake.

    Captions are excluded: they share their leader-anchor coords with the
    figure piece they explain by design, and that's the point.
    """
    coord_groups: dict[tuple[float, float], list[str]] = {}
    for n in g.nodes:
        if n.meta.get("kind") == "caption":
            continue
        x = n.meta.get("x")
        y = n.meta.get("y")
        if x is None or y is None:
            continue
        key = (round(float(x), _COORD_PRECISION),
               round(float(y), _COORD_PRECISION))
        coord_groups.setdefault(key, []).append(n.id)

    out: list[dict] = []
    for (x, y), ids in coord_groups.items():
        if len(ids) <= 1:
            continue
        out.append({
            "code": "duplicate_coords",
            "message": (
                f"{len(ids)} nodes share coordinates ({x:g}, {y:g}): "
                f"{ids}.  This usually means a labelling mistake — "
                f"two points cannot occupy the same location."
            ),
            "refs": {"nodes": ids, "x": x, "y": y},
        })
    return out


def _check_math_mode_coverage(g: SceneGraph, math_mode: bool) -> list[dict]:
    """If the canvas opened in math_mode, complain when no node has
    coordinates — the figure will sit in the label strip and look like a
    bug to the user."""
    if not math_mode:
        return []
    if not g.nodes:
        return []
    geo_count = 0
    for n in g.nodes:
        if "x" in n.meta or "cx" in n.meta:
            geo_count += 1
            continue
        if n.meta.get("kind") == "axes":
            geo_count += 1
    if geo_count == 0:
        return [{
            "code": "math_mode_no_coords",
            "message": (
                "Canvas is in math_mode but no node carries coordinates "
                "(x/y or cx/cy/r).  Either pass coordinate metadata via "
                "sevim_add_node(meta_extras={'x':..., 'y':...}) for points "
                "and {'cx':..., 'cy':..., 'r':...} for circles, or open "
                "the canvas without math_mode for concept-diagram layout."
            ),
            "refs": {},
        }]
    return []


def _check_dangling_edges(g: SceneGraph, nodes_by_id: dict) -> list[dict]:
    """Edges referencing nodes that don't exist — shouldn't happen given
    Canvas.add_edge validates ids, but worth catching if the SceneGraph
    is built another way (e.g. via run_pipeline)."""
    out: list[dict] = []
    for e in g.edges:
        if e.from_id not in nodes_by_id:
            out.append({
                "code": "dangling_edge",
                "message": f"Edge {e.id!r} references missing source node {e.from_id!r}.",
                "refs": {"edge": e.id, "missing": e.from_id},
            })
        if e.to_id not in nodes_by_id:
            out.append({
                "code": "dangling_edge",
                "message": f"Edge {e.id!r} references missing target node {e.to_id!r}.",
                "refs": {"edge": e.id, "missing": e.to_id},
            })
    return out


def _check_circle_radius(g: SceneGraph) -> list[dict]:
    """A circle node with cx/cy but no r is invalid in math_mode."""
    out: list[dict] = []
    for n in g.nodes:
        if n.meta.get("kind") != "circle":
            continue
        if "cx" not in n.meta or "cy" not in n.meta:
            continue
        if "r" not in n.meta:
            out.append({
                "code": "circle_missing_radius",
                "message": (
                    f"Circle {n.label!r} has cx/cy but no r — Sevim will "
                    f"draw it at default size, not the geometric radius "
                    f"you intended.  Add 'r' to meta_extras."
                ),
                "refs": {"node": n.id},
            })
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_point_curve(a, b) -> tuple | None:
    """Identify which of (a, b) is the point and which is the circle/curve.

    Returns ``(point, curve)`` if exactly one has x/y and the other has
    cx/cy/r; otherwise None (we can't reason about lies_on without both).
    """
    a_is_point = "x" in a.meta and "y" in a.meta
    b_is_point = "x" in b.meta and "y" in b.meta
    a_is_curve = "cx" in a.meta and "cy" in a.meta and "r" in a.meta
    b_is_curve = "cx" in b.meta and "cy" in b.meta and "r" in b.meta
    if a_is_point and b_is_curve:
        return a, b
    if b_is_point and a_is_curve:
        return b, a
    return None
