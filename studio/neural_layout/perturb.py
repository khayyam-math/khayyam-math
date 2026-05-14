"""Synthetic perturbation of `SceneGraph` to generate additional
(broken, fixed) training pairs from already-accepted layouts.

The user's instruction: real prompts are the spine; synthetic
perturbation is augmentation on top. So we never perturb the
`source` (broken) field of an existing pair — we perturb the
`target` (fixed) and use the result as a NEW source paired with
the same target.

All perturbations operate on the structured `SceneGraph` directly,
not on raw SVG. The parser already gave us canvas-space bboxes for
every node, so a perturbation is just a deterministic transform of
those bboxes. The narration-anchored / protected nodes are exempt:
the model needs to learn that those positions are pinned.

Each perturbation function takes a `SceneGraph` + an `rng` and
returns a NEW `SceneGraph` (no mutation of the input).
"""
from __future__ import annotations

import copy
import random
from typing import Callable

from .schema import NodeFeatures, SceneGraph

Perturbation = Callable[[SceneGraph, random.Random], SceneGraph]


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------

def _shift_bbox(
    bbox: tuple[float, float, float, float], dx: float, dy: float,
) -> tuple[float, float, float, float]:
    x, y, w, h = bbox
    return (x + dx, y + dy, w, h)


def _scale_bbox(
    bbox: tuple[float, float, float, float], factor: float,
) -> tuple[float, float, float, float]:
    x, y, w, h = bbox
    cx, cy = x + w / 2, y + h / 2
    new_w, new_h = w * factor, h * factor
    return (cx - new_w / 2, cy - new_h / 2, new_w, new_h)


def _top_level_group_ids(graph: SceneGraph) -> list[str]:
    """Distinct top-level group identifiers in the graph."""
    seen: dict[str, None] = {}
    for n in graph.nodes:
        gid = n.top_level_group_id or n.id
        seen.setdefault(gid, None)
    return list(seen.keys())


def _nodes_in_group(
    graph: SceneGraph, gid: str,
) -> list[NodeFeatures]:
    return [
        n for n in graph.nodes
        if (n.top_level_group_id or n.id) == gid
    ]


def _clone_with_node_updates(
    graph: SceneGraph,
    new_bbox_by_id: dict[str, tuple[float, float, float, float]],
) -> SceneGraph:
    """Return a shallow-copied graph where nodes whose id appears in
    `new_bbox_by_id` have their bbox replaced."""
    new_nodes: list[NodeFeatures] = []
    for n in graph.nodes:
        if n.id in new_bbox_by_id:
            replacement = copy.copy(n)
            replacement.bbox = new_bbox_by_id[n.id]
            new_nodes.append(replacement)
        else:
            new_nodes.append(n)
    return SceneGraph(
        nodes=new_nodes,
        edges=list(graph.edges),
        viewbox=graph.viewbox,
        canvas_w=graph.canvas_w,
        canvas_h=graph.canvas_h,
    )


# --------------------------------------------------------------------
# perturbations
# --------------------------------------------------------------------

def jitter_groups(
    graph: SceneGraph, rng: random.Random, *,
    max_delta_px: float = 30.0,
) -> SceneGraph:
    """Translate every top-level group by U(-d, d) in x and y.

    Protected nodes (titles, axis labels) are exempt — their positions
    are pinned and the model must learn to leave them alone.
    """
    updates: dict[str, tuple[float, float, float, float]] = {}
    for gid in _top_level_group_ids(graph):
        members = _nodes_in_group(graph, gid)
        if any(m.is_protected for m in members):
            continue
        dx = rng.uniform(-max_delta_px, max_delta_px)
        dy = rng.uniform(-max_delta_px, max_delta_px)
        for n in members:
            updates[n.id] = _shift_bbox(n.bbox, dx, dy)
    return _clone_with_node_updates(graph, updates)


def displace_one_group(
    graph: SceneGraph, rng: random.Random, *,
    magnitude_px: tuple[float, float] = (60.0, 120.0),
) -> SceneGraph:
    """Pick one top-level group at random and shove it sideways /
    up / down by a larger delta — the kind of displacement that
    blows past the canvas edge."""
    gids = [
        gid for gid in _top_level_group_ids(graph)
        if not any(
            m.is_protected for m in _nodes_in_group(graph, gid)
        )
    ]
    if not gids:
        return graph
    gid = rng.choice(gids)
    mag = rng.uniform(*magnitude_px)
    angle = rng.uniform(0, 6.283185)  # 2π
    import math
    dx = mag * math.cos(angle)
    dy = mag * math.sin(angle)
    members = _nodes_in_group(graph, gid)
    updates = {n.id: _shift_bbox(n.bbox, dx, dy) for n in members}
    return _clone_with_node_updates(graph, updates)


def compress_viewbox(
    graph: SceneGraph, rng: random.Random, *,
    factor_range: tuple[float, float] = (0.80, 0.92),
) -> SceneGraph:
    """Shrink the viewBox by 8–20%, forcing outer content to escape."""
    factor = rng.uniform(*factor_range)
    x0, y0, w, h = graph.viewbox
    new_w, new_h = w * factor, h * factor
    new_x0 = x0 + (w - new_w) / 2
    new_y0 = y0 + (h - new_h) / 2
    return SceneGraph(
        nodes=list(graph.nodes),
        edges=list(graph.edges),
        viewbox=(new_x0, new_y0, new_w, new_h),
        canvas_w=int(graph.canvas_w * factor),
        canvas_h=int(graph.canvas_h * factor),
    )


def scale_one_group(
    graph: SceneGraph, rng: random.Random, *,
    factor_range: tuple[float, float] = (1.25, 1.6),
) -> SceneGraph:
    """Scale one group up; will force overlap with siblings."""
    gids = [
        gid for gid in _top_level_group_ids(graph)
        if not any(
            m.is_protected for m in _nodes_in_group(graph, gid)
        )
    ]
    if not gids:
        return graph
    gid = rng.choice(gids)
    factor = rng.uniform(*factor_range)
    updates: dict[str, tuple[float, float, float, float]] = {}
    members = _nodes_in_group(graph, gid)
    # Find group centre to scale around.
    xs = [m.bbox[0] + m.bbox[2] / 2 for m in members]
    ys = [m.bbox[1] + m.bbox[3] / 2 for m in members]
    if not xs:
        return graph
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    for m in members:
        x, y, w, h = m.bbox
        new_w, new_h = w * factor, h * factor
        # scale each node's centre relative to the group centre too
        mx, my = x + w / 2, y + h / 2
        new_cx = cx + (mx - cx) * factor
        new_cy = cy + (my - cy) * factor
        updates[m.id] = (
            new_cx - new_w / 2, new_cy - new_h / 2, new_w, new_h,
        )
    return _clone_with_node_updates(graph, updates)


def stack_overlap(
    graph: SceneGraph, rng: random.Random, *,
    overlap_px: float = 40.0,
) -> SceneGraph:
    """Pick two top-level groups and shove them toward each other so
    they overlap by ~overlap_px. The fixed target has them apart;
    teaching this perturbation should help the model push them back."""
    gids = [
        gid for gid in _top_level_group_ids(graph)
        if not any(
            m.is_protected for m in _nodes_in_group(graph, gid)
        )
    ]
    if len(gids) < 2:
        return graph
    g1, g2 = rng.sample(gids, 2)
    m1 = _nodes_in_group(graph, g1)
    m2 = _nodes_in_group(graph, g2)
    if not m1 or not m2:
        return graph
    # Pull g2 toward g1's centroid.
    cx1 = sum(n.bbox[0] + n.bbox[2] / 2 for n in m1) / len(m1)
    cx2 = sum(n.bbox[0] + n.bbox[2] / 2 for n in m2) / len(m2)
    dx = (cx1 - cx2) * 0.6  # shove ~60% of the way
    if abs(dx) < overlap_px:
        dx = overlap_px * (1 if dx >= 0 else -1)
    updates = {n.id: _shift_bbox(n.bbox, dx, 0) for n in m2}
    return _clone_with_node_updates(graph, updates)


PERTURBATIONS: dict[str, Perturbation] = {
    "jitter": jitter_groups,
    "displace": displace_one_group,
    "viewbox_compress": compress_viewbox,
    "scale_one": scale_one_group,
    "stack_overlap": stack_overlap,
}


def random_perturbation(
    graph: SceneGraph, rng: random.Random,
) -> tuple[str, SceneGraph]:
    """Apply ONE random perturbation. Returns (kind, perturbed_graph)."""
    kind = rng.choice(list(PERTURBATIONS.keys()))
    return kind, PERTURBATIONS[kind](graph, rng)
