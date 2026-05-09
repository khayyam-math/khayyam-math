"""Figure-planning helper for math-mode Sevim canvases.

The MCP-side problem this solves: tool-using LLMs are weak at picking
coordinates for graph figures.  They cram clustered nodes onto a single
row, place all clauses at the origin, or skip the spatial structure
that makes a reduction visually legible.

``plan_layout`` removes coordinate-picking from the model's job: the
model passes a structural description (nodes with optional cluster
labels, edges) and gets back math-coordinate placements that respect
the cluster topology.  The model then threads those (x, y) values
into ``add_node(meta_extras={"x":..., "y":...})`` calls.

Layout strategies
-----------------
* ``columns`` — clusters become vertical columns left-to-right; members
  stacked top-to-bottom within each column.  This is the canonical 3SAT
  → CLIQUE / 3SAT → 3-COLOR layout (Sipser §7.5).  Use whenever the
  reduction is conventionally drawn as N parallel groups.

* ``constraint_clusters`` — outer ring of cluster centres, inner ring
  of members per cluster.  Use when the reduction has rotational
  symmetry (e.g. K_n with grouped subsets) but NOT for 3SAT→clique.

* ``radial`` — single ring of all nodes around the origin.  Best for
  flat structures (K_n, cycle graphs, "place these N concepts").

* ``auto`` — picks ``columns`` when nodes carry a ``cluster`` field
  (since that's the textbook layout for almost every clause-based
  reduction); falls back to ``radial`` when no clusters exist.

Returned coordinates are in math units centred near the origin.  Callers
in math_mode pass them through ``add_node(meta_extras={"x":..., "y":...})``;
the geometric_layout pass scales them to canvas pixels.
"""
from __future__ import annotations

import math
from typing import Any


def plan_layout(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    layout: str = "auto",
    canvas_w: float = 900,
    canvas_h: float = 620,
) -> dict[str, Any]:
    """Compute math-coordinate positions for the given graph.

    Args:
        nodes: List of ``{"node_id": str, "label": str, "cluster": str?,
            "kind": str?}``.  Only ``node_id`` is required.
        edges: Optional list of ``{"src": str, "dst": str, "relation": str?}``.
            Currently unused in the layout but echoed in warnings when the
            graph density suggests the chosen algorithm may produce edge
            clutter.
        layout: ``"auto"`` (default), ``"constraint_clusters"``, ``"radial"``.
        canvas_w / canvas_h: Hints used to scale the math-coord range.

    Returns:
        ``{
            "layout_chosen": str,
            "node_positions": {node_id: {"x": float, "y": float}},
            "warnings": [str],
            "math_bounds": {"xmin", "xmax", "ymin", "ymax"},
        }``
    """
    if not nodes:
        return {
            "layout_chosen": layout,
            "node_positions": {},
            "warnings": ["plan called with zero nodes"],
            "math_bounds": {"xmin": -1, "xmax": 1, "ymin": -1, "ymax": 1},
        }

    edges = edges or []
    has_clusters = any(n.get("cluster") for n in nodes)

    if layout == "auto":
        # Columns are the textbook layout for almost every clause-based
        # reduction (3SAT→clique, 3SAT→3-color, ...).  Falls back to
        # radial when there's nothing to group.
        layout = "columns" if has_clusters else "radial"

    warnings: list[str] = []
    if layout in ("columns", "constraint_clusters") and not has_clusters:
        warnings.append(
            f"{layout} requested but no node carries a 'cluster' "
            f"field — falling back to radial"
        )
        layout = "radial"

    if layout == "columns":
        positions = _columns_layout(nodes)
    elif layout == "constraint_clusters":
        positions = _cluster_layout(nodes)
    elif layout == "radial":
        positions = _radial_layout(nodes)
    else:
        warnings.append(f"unknown layout {layout!r} — using radial")
        layout = "radial"
        positions = _radial_layout(nodes)

    # Suggested caption anchors per cluster — the model can drop these
    # straight into add_caption ops so leader lines target real nodes
    # instead of made-up coordinates.  One anchor per cluster, placed at
    # the cluster centroid.
    cluster_anchors = _cluster_anchors(nodes, positions)

    # Density warning: dense graphs draw better with a force-directed
    # layout than with a radial one.  Mention but don't override.
    n = len(nodes)
    if layout == "radial" and len(edges) >= n * 2:
        warnings.append(
            f"radial layout with {n} nodes and {len(edges)} edges will "
            f"produce many crossings; consider grouping nodes via 'cluster'"
        )

    xs = [p["x"] for p in positions.values()]
    ys = [p["y"] for p in positions.values()]
    bounds = {
        "xmin": min(xs), "xmax": max(xs),
        "ymin": min(ys), "ymax": max(ys),
    }

    return {
        "layout_chosen": layout,
        "node_positions": positions,
        "cluster_anchors": cluster_anchors,
        "warnings": warnings,
        "math_bounds": bounds,
    }


def _cluster_anchors(
    nodes: list[dict[str, Any]],
    positions: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """For each cluster, return a math-coord (x, y) that captions about
    that cluster should anchor to (its members' centroid).  When the
    LLM passes these straight into ``add_caption`` ops, leader lines
    end on real geometry instead of empty canvas."""
    by_cluster: dict[str, list[str]] = {}
    for n in nodes:
        key = n.get("cluster")
        if not key:
            continue
        by_cluster.setdefault(key, []).append(n["node_id"])
    out: dict[str, dict[str, float]] = {}
    for name, ids in by_cluster.items():
        xs = [positions[i]["x"] for i in ids if i in positions]
        ys = [positions[i]["y"] for i in ids if i in positions]
        if not xs:
            continue
        out[name] = {"x": sum(xs) / len(xs), "y": sum(ys) / len(ys)}
    return out


# ---------------------------------------------------------------------------
# Layout primitives
# ---------------------------------------------------------------------------

# Math-coord radius the outer ring of cluster centres sits on.  Calibrated
# against the default 900x620 canvas: the geometric_layout pass will scale
# these coords to fill the canvas, but the *aspect ratio* of node spread
# matters for legibility, hence a fixed radius rather than a canvas-derived
# one.
_OUTER_R = 6.0

# Inner-cluster radius — cluster members sit on a small circle around the
# cluster centre.  Half the typical inter-cluster gap so members are
# clearly grouped but not overlapping the next cluster.
_INNER_R = 1.6


# Math-coord half-ranges for the columns layout.  Tuned so a 3-clause /
# 3-literal-per-clause figure maps to a comfortable rectangle on the
# default 900×620 canvas with room for caption strips on both sides.
_COL_HALFRANGE = 6.0   # horizontal span of cluster centres
_ROW_HALFRANGE = 4.0   # vertical span of members within a column


def _columns_layout(nodes: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Vertical columns, one per cluster, evenly spaced left-to-right.

    Members of each cluster are stacked top-to-bottom within the column.
    This is the canonical 3SAT→CLIQUE / 3SAT→3-COLOR layout: clauses
    march left-to-right, literals stack inside their clause.

    Stable ordering: clusters sorted by name, members sorted by node_id
    so the same input produces the same layout.
    """
    by_cluster: dict[str, list[dict[str, Any]]] = {}
    for n in nodes:
        key = n.get("cluster") or "_unclustered"
        by_cluster.setdefault(key, []).append(n)

    cluster_names = sorted(by_cluster)
    n_cols = len(cluster_names)
    positions: dict[str, dict[str, float]] = {}

    for i, name in enumerate(cluster_names):
        if n_cols == 1:
            cx = 0.0
        else:
            cx = -_COL_HALFRANGE + 2 * _COL_HALFRANGE * i / (n_cols - 1)
        members = sorted(by_cluster[name], key=lambda n: n.get("node_id", ""))
        m = len(members)
        for j, member in enumerate(members):
            if m == 1:
                cy = 0.0
            else:
                # j=0 → top (+ROW_HALFRANGE), j=m-1 → bottom (-ROW_HALFRANGE)
                cy = _ROW_HALFRANGE - 2 * _ROW_HALFRANGE * j / (m - 1)
            positions[member["node_id"]] = {"x": cx, "y": cy}
    return positions


def _cluster_layout(nodes: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Outer ring of cluster centres + inner ring of members per cluster.

    Nodes without a cluster value share a synthetic '_unclustered' bin
    that gets its own slot on the outer ring.  Stable ordering: clusters
    sorted by name, members sorted by ``node_id`` so the same input
    produces the same layout.
    """
    by_cluster: dict[str, list[dict[str, Any]]] = {}
    for n in nodes:
        key = n.get("cluster") or "_unclustered"
        by_cluster.setdefault(key, []).append(n)

    cluster_names = sorted(by_cluster)
    n_clusters = len(cluster_names)

    # Outer-ring angles: evenly spaced, starting at the top and going
    # clockwise — matches how a reader scans clauses on a page.
    if n_clusters == 1:
        cluster_centres = {cluster_names[0]: (0.0, 0.0)}
    else:
        cluster_centres = {}
        for i, name in enumerate(cluster_names):
            theta = math.pi / 2 - 2 * math.pi * i / n_clusters
            cluster_centres[name] = (
                _OUTER_R * math.cos(theta),
                _OUTER_R * math.sin(theta),
            )

    positions: dict[str, dict[str, float]] = {}
    for name in cluster_names:
        members = sorted(by_cluster[name], key=lambda n: n.get("node_id", ""))
        cx, cy = cluster_centres[name]
        m = len(members)
        if m == 1:
            positions[members[0]["node_id"]] = {"x": cx, "y": cy}
            continue
        for j, member in enumerate(members):
            # Inner ring: same start-at-top + clockwise convention.
            phi = math.pi / 2 - 2 * math.pi * j / m
            positions[member["node_id"]] = {
                "x": cx + _INNER_R * math.cos(phi),
                "y": cy + _INNER_R * math.sin(phi),
            }
    return positions


def _radial_layout(nodes: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Single ring of all nodes around the origin, evenly spaced."""
    sorted_nodes = sorted(nodes, key=lambda n: n.get("node_id", ""))
    n = len(sorted_nodes)
    positions: dict[str, dict[str, float]] = {}
    if n == 1:
        positions[sorted_nodes[0]["node_id"]] = {"x": 0.0, "y": 0.0}
        return positions
    radius = _OUTER_R
    for i, node in enumerate(sorted_nodes):
        theta = math.pi / 2 - 2 * math.pi * i / n
        positions[node["node_id"]] = {
            "x": radius * math.cos(theta),
            "y": radius * math.sin(theta),
        }
    return positions
