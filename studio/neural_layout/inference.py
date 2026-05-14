"""Inference utilities: apply a trained GNN to a single SVG.

End-to-end:
    svg_text → SceneGraph → tensor batch → model → predicted deltas
    → write back into the SceneGraph → render as SVG.

Designed to run on CPU (for Phase D Fargate deployment); the model
is tiny enough that CPU inference is sub-second.

Public API:
    load_model(checkpoint_path, device='cpu') -> GraphLayoutCorrector
    correct_layout(model, svg_text) -> svg_text   (best-effort)
    correct_scene_graph(model, source) -> SceneGraph
"""
from __future__ import annotations

from pathlib import Path

import torch

from .data import collate, pair_to_example
from .models.gnn_baseline import (
    build_default, build_large, GraphLayoutCorrector,
)
from .schema import SceneGraph, TrainingPair
from .svg_to_graph import parse_svg


def load_model(
    checkpoint_path: str | Path, *, device: str = "cpu",
) -> GraphLayoutCorrector:
    ckpt = torch.load(checkpoint_path, map_location=device,
                      weights_only=False)
    # Pick the right model size based on the checkpoint's saved args.
    args = ckpt.get("args") or {}
    if args.get("model_size") == "large":
        model = build_large()
    else:
        model = build_default()
    model.load_state_dict(ckpt["model"])
    model.to(device)
    model.eval()
    return model


@torch.no_grad()
def correct_scene_graph(
    model: GraphLayoutCorrector,
    source: SceneGraph,
) -> SceneGraph:
    """Apply the model to a SceneGraph; return a NEW SceneGraph with
    bbox values updated by the predicted deltas. Protected nodes are
    forced to keep their original bbox regardless of model output."""
    # Trick the data pipeline: build a dummy TrainingPair where
    # target == source, so we can reuse `pair_to_example` to get the
    # tensors. The target_delta it produces is zero (we won't use it).
    fake = TrainingPair(
        pair_id="infer", prompt="", source=source, target=source,
        viewport_kind="desktop", math_bucket="other",
    )
    ex = pair_to_example(fake)
    if ex is None:
        return source
    batch = collate([ex])
    device = next(model.parameters()).device
    batch = batch.to(device)
    pred_delta = model(
        batch.node_types, batch.numeric_feats,
        batch.edge_index, batch.edge_rel,
    )  # [N, 4] in normalised coords
    cw, ch = source.canvas_w, source.canvas_h
    new_nodes = []
    common_ids = [n.id for n in source.nodes]  # pair_to_example keeps order
    for i, n in enumerate(source.nodes):
        if i >= pred_delta.shape[0]:
            new_nodes.append(n)
            continue
        if n.is_protected:
            new_nodes.append(n)
            continue
        dx, dy, dw, dh = pred_delta[i].tolist()
        x, y, w, h = n.bbox
        new_bbox = (
            x + dx * cw, y + dy * ch,
            max(1.0, w + dw * cw), max(1.0, h + dh * ch),
        )
        new_nodes.append(type(n)(
            id=n.id, type=n.type, bbox=new_bbox,
            text=n.text, font_size=n.font_size,
            stroke_width=n.stroke_width,
            parent_id=n.parent_id,
            top_level_group_id=n.top_level_group_id,
            is_narration_anchor=n.is_narration_anchor,
            is_caption=n.is_caption,
            is_protected=n.is_protected,
            raw_attrs=n.raw_attrs,
        ))
    return SceneGraph(
        nodes=new_nodes, edges=list(source.edges),
        viewbox=source.viewbox,
        canvas_w=source.canvas_w, canvas_h=source.canvas_h,
    )


def correct_layout_svg(
    model: GraphLayoutCorrector, svg_text: str,
) -> tuple[SceneGraph, SceneGraph]:
    """Parse the SVG, apply the model, return (source, predicted).

    This does NOT re-serialise back to SVG yet — that's a Phase D
    task (turn a corrected SceneGraph back into SVG patches for the
    runtime to apply). For now we expose the predicted graph so it
    can be inspected.
    """
    res = parse_svg(svg_text)
    if not res.graph.nodes:
        return res.graph, res.graph
    corrected = correct_scene_graph(model, res.graph)
    return res.graph, corrected
