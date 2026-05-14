"""Hybrid layout correction: LayoutDM suggests, CP-SAT enforces.

Pipeline:
    broken SVG
        → parse → SceneGraph
        → LayoutDM denoise (with mask_frac controllable) → suggested graph
        → rewrite SVG top-level group transforms + text x/y to match
        → `studio.layout_planner.plan_layout` → guaranteed-valid SVG

The model never produces a final layout on its own. Its role is to
shift the per-element "anchor" away from the broken position toward
the in-distribution position. CP-SAT then picks one of 25 candidate
positions around that new anchor, optimising for no-overlap, in-
bounds, and group cohesion. Pinned (narration-anchored / protected)
elements bypass the model entirely.

This is the layered approach from PLAN.md "Layer 2", and the
recommended deployment shape: the model provides a learned prior,
the deterministic solver provides hard correctness.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

import torch

from studio.layout_planner import plan_layout
from studio.neural_layout.inference_layoutdm import (
    correct_scene_graph, load_model_layoutdm,
)
from studio.neural_layout.schema import NodeFeatures, SceneGraph
from studio.neural_layout.svg_to_graph import parse_svg

_SVG_NS = "{http://www.w3.org/2000/svg}"
_TRANSLATE_RE = re.compile(
    r"translate\s*\(\s*([-+]?\d*\.?\d+)\s*[, ]\s*"
    r"([-+]?\d*\.?\d+)\s*\)"
)


def _node_lookup(graph: SceneGraph) -> dict[str, NodeFeatures]:
    return {n.id: n for n in graph.nodes}


def _replace_translate(transform: str, new_tx: float, new_ty: float) -> str:
    """Replace the FIRST translate(...) in `transform` with new values.

    If no translate exists, prepend one. Preserves the rest of the
    transform (matrix, scale, etc.) so children stay correct.
    """
    if not transform:
        return f"translate({new_tx:.2f},{new_ty:.2f})"
    if _TRANSLATE_RE.search(transform):
        return _TRANSLATE_RE.sub(
            f"translate({new_tx:.2f},{new_ty:.2f})", transform, count=1,
        )
    return f"translate({new_tx:.2f},{new_ty:.2f}) " + transform


def _rewrite_positions_from_graph(
    svg_text: str, source: SceneGraph, suggested: SceneGraph,
) -> str:
    """Update top-level <g> transforms + top-level <text> x/y so the
    elements move from their source positions to the model-suggested
    positions. Children inherit their parent group's transform.

    We only touch TOP-LEVEL elements (direct children of <svg>) —
    inner-group elements move with their parent group automatically.
    Group transforms are absolute translate(...) replacements; text
    elements get their x/y attributes replaced.
    """
    src_by_id = _node_lookup(source)
    sug_by_id = _node_lookup(suggested)

    # Parse the SVG and walk its top-level children.
    try:
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return svg_text
    if not root.tag.endswith("svg"):
        return svg_text

    for child in list(root):
        elem_id = child.get("id")
        if not elem_id or elem_id not in src_by_id or elem_id not in sug_by_id:
            continue
        src_node = src_by_id[elem_id]
        sug_node = sug_by_id[elem_id]
        if src_node.is_protected or sug_node.is_protected:
            continue
        sx, sy, _, _ = src_node.bbox
        ux, uy, _, _ = sug_node.bbox
        dx = ux - sx
        dy = uy - sy
        # Skip if the model basically didn't move it (avoid noise jitter).
        if abs(dx) < 1.0 and abs(dy) < 1.0:
            continue
        tag = child.tag.replace(_SVG_NS, "")
        if tag == "g":
            t_in = child.get("transform") or ""
            tx = ty = 0.0
            m = _TRANSLATE_RE.search(t_in)
            if m:
                tx, ty = float(m.group(1)), float(m.group(2))
            new_tx = tx + dx
            new_ty = ty + dy
            child.set("transform", _replace_translate(t_in, new_tx, new_ty))
        elif tag in ("text", "rect", "circle", "ellipse", "line",
                    "image", "use"):
            # Shift the element's anchor by (dx, dy). Only handle
            # x/y here; CP-SAT may further adjust afterward.
            if tag in ("text", "rect", "image", "use"):
                old_x = float(child.get("x") or 0)
                old_y = float(child.get("y") or 0)
                child.set("x", f"{old_x + dx:.2f}")
                child.set("y", f"{old_y + dy:.2f}")
            elif tag in ("circle", "ellipse"):
                old_cx = float(child.get("cx") or 0)
                old_cy = float(child.get("cy") or 0)
                child.set("cx", f"{old_cx + dx:.2f}")
                child.set("cy", f"{old_cy + dy:.2f}")
            elif tag == "line":
                old_x1 = float(child.get("x1") or 0)
                old_y1 = float(child.get("y1") or 0)
                old_x2 = float(child.get("x2") or 0)
                old_y2 = float(child.get("y2") or 0)
                child.set("x1", f"{old_x1 + dx:.2f}")
                child.set("y1", f"{old_y1 + dy:.2f}")
                child.set("x2", f"{old_x2 + dx:.2f}")
                child.set("y2", f"{old_y2 + dy:.2f}")
        # other element types (path, polygon, …) we leave alone —
        # rewriting their geometry is fragile and CP-SAT can move
        # them via group enclosure if needed.
    return ET.tostring(root, encoding="unicode")


def correct_layout_hybrid(
    svg_text: str,
    model,
    *,
    mask_frac: float = 0.5,
    n_steps: int = 12,
    temperature: float = 0.5,
    protected_ids: set[str] | None = None,
    plan_time_limit_s: float = 2.0,
    seed: int = 0,
    device: torch.device | str = "cpu",
) -> str:
    """Run the full hybrid pipeline. Returns the corrected SVG.

    Args:
        svg_text: source (possibly broken) SVG.
        model: a loaded LayoutDMDenoiser checkpoint.
        mask_frac: how much of the layout the model should
            reconsider. 0.5 is a good default — strong enough to
            propose real movement, weak enough to keep the
            original's structure as the prior.
        n_steps: number of iterative denoising steps.
        protected_ids: ids that CP-SAT must pin (typically
            narration-highlighted ids). The model also leaves these
            alone via the `is_protected` flag from the parser.
        plan_time_limit_s: cap on the CP-SAT solve time.
    """
    parsed = parse_svg(svg_text)
    source = parsed.graph
    if not source.nodes:
        return svg_text
    suggested = correct_scene_graph(
        model, source,
        mask_frac=mask_frac, n_steps=n_steps,
        temperature=temperature, device=device, seed=seed,
    )
    nudged_svg = _rewrite_positions_from_graph(
        svg_text, source, suggested,
    )
    # CP-SAT runs on the model-nudged SVG.
    return plan_layout(
        nudged_svg, time_limit_s=plan_time_limit_s,
        protected_ids=protected_ids,
    )


def correct_layout_planner_only(
    svg_text: str,
    *,
    protected_ids: set[str] | None = None,
    plan_time_limit_s: float = 2.0,
) -> str:
    """Pass-through to the existing CP-SAT planner, no model.

    Useful for A/B comparison — the same SVG run through CP-SAT
    alone vs through hybrid (model + CP-SAT).
    """
    return plan_layout(
        svg_text, time_limit_s=plan_time_limit_s,
        protected_ids=protected_ids,
    )
