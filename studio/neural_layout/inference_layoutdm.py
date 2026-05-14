"""LayoutDM-style repair inference.

Given a broken SceneGraph, run iterative denoising to produce a
corrected SceneGraph. Differs from `inference.py` (GNN baseline) by
treating layout repair as a denoising problem rather than a
delta-regression: instead of asking "predict the deltas", we ask
"given these noisy positions, what positions would form a layout
similar to those in the training distribution?".

Key design choice: we initialise the denoising with the broken
positions (not from scratch) and mask only a fraction. The model
fills in the masked positions consistent with the unmasked context.
This way we get the benefit of the broken layout as prior +
local correction power.
"""
from __future__ import annotations

from pathlib import Path
import random

import torch

from .data import _node_type_idx, _edge_rel_idx, NUM_NUMERIC_FEATS
from .data import collate, pair_to_example
from .diffusion import (
    MASK_ID, N_BINS, NoiseSchedule, dequantise_tokens, denoise,
    quantise_bbox_norm,
)
from .models.layoutdm import build_default, build_small, LayoutDMDenoiser
from .schema import NodeFeatures, SceneGraph, TrainingPair
from .svg_to_graph import parse_svg


def load_model_layoutdm(
    checkpoint_path: str | Path, *, device: str = "cpu",
) -> LayoutDMDenoiser:
    ckpt = torch.load(checkpoint_path, map_location=device,
                      weights_only=False)
    args = ckpt.get("args") or {}
    if args.get("model_size") == "small":
        model = build_small()
    else:
        model = build_default()
    model.load_state_dict(ckpt["model"])
    model.to(device)
    model.eval()
    return model


@torch.no_grad()
def correct_scene_graph(
    model: LayoutDMDenoiser,
    source: SceneGraph,
    *,
    mask_frac: float = 0.5,
    n_steps: int = 16,
    T: int = 100,
    temperature: float = 0.7,
    device: torch.device | str = "cuda",
    seed: int = 0,
) -> SceneGraph:
    """Run iterative denoising to repair a broken layout.

    `mask_frac` controls how much of the source layout is treated as
    "noise" and re-predicted. 0.0 → keep all source positions (no
    change). 1.0 → fully regenerate from scratch.

    Protected nodes (titles, narration anchors) are never masked.
    """
    if not source.nodes:
        return source
    fake = TrainingPair(
        pair_id="infer", prompt="", source=source, target=source,
        viewport_kind="desktop", math_bucket="other",
    )
    ex = pair_to_example(fake)
    if ex is None:
        return source
    batch = collate([ex])
    device = torch.device(device)
    batch = batch.to(device)

    # Build position tokens from source bbox
    source_bbox = batch.numeric_feats[:, :4].clamp(0, 1.0 - 1e-7)
    src_tokens = quantise_bbox_norm(source_bbox)  # [N, 4]
    # Random mask: pick `mask_frac` of (node, dim) positions to mask,
    # but never mask protected nodes' positions.
    rng = torch.Generator(device=device).manual_seed(seed)
    rand = torch.rand(src_tokens.shape, generator=rng, device=device)
    mask = rand < mask_frac
    protected_node_mask = batch.is_protected.unsqueeze(-1).expand_as(mask)
    mask = mask & (~protected_node_mask)

    non_pos = batch.numeric_feats[:, 4:]  # [N, 10]
    final_tokens = denoise(
        model,
        node_types=batch.node_types,
        text_feats=non_pos,
        edge_index=batch.edge_index,
        edge_rel=batch.edge_rel,
        batch_idx=batch.batch_idx,
        init_tokens=src_tokens,
        init_mask=mask,
        n_steps=n_steps,
        schedule=NoiseSchedule(T=T),
        temperature=temperature,
        device=device,
    )
    new_bbox_norm = dequantise_tokens(final_tokens)  # [N, 4] in [0, 1]
    cw, ch = source.canvas_w, source.canvas_h
    new_nodes: list[NodeFeatures] = []
    # pair_to_example preserves node ORDER, so source.nodes[i] aligns.
    for i, n in enumerate(source.nodes):
        if i >= new_bbox_norm.shape[0]:
            new_nodes.append(n)
            continue
        if n.is_protected:
            new_nodes.append(n)
            continue
        x_n, y_n, w_n, h_n = new_bbox_norm[i].tolist()
        new_bbox = (
            x_n * cw, y_n * ch,
            max(1.0, w_n * cw), max(1.0, h_n * ch),
        )
        new_nodes.append(NodeFeatures(
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
    model: LayoutDMDenoiser, svg_text: str, **kwargs,
) -> tuple[SceneGraph, SceneGraph]:
    res = parse_svg(svg_text)
    if not res.graph.nodes:
        return res.graph, res.graph
    return res.graph, correct_scene_graph(model, res.graph, **kwargs)
