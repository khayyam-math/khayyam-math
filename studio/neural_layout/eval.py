"""Layout-quality metrics for validation + final reporting.

All metrics are computed on the predicted boxes after applying the
model's delta to the source bbox. They use *normalised* (0-1) coords
unless suffixed with `_px` (then in canvas pixels).
"""
from __future__ import annotations

from dataclasses import dataclass

import torch


@dataclass
class GraphMetrics:
    overlap_count: float        # # overlapping pairs per graph
    overlap_area_norm: float    # total overlap area / 1
    oob_count: float            # # nodes with any out-of-bounds edge
    oob_area_norm: float        # avg OOB-area per node
    mean_displacement_norm: float
    n_anchors: int
    anchor_displacement_norm: float
    n_protected: int
    protected_displacement_norm: float


def predicted_boxes_from(
    source_bbox_norm: torch.Tensor,
    pred_delta: torch.Tensor,
) -> torch.Tensor:
    pred = source_bbox_norm + pred_delta
    xy = pred[:, :2]
    wh = pred[:, 2:].clamp_min(1e-4)
    return torch.cat([xy, wh], dim=-1)


def overlap_pair_count(
    boxes: torch.Tensor, batch_idx: torch.Tensor,
    threshold: float = 1e-5,
) -> torch.Tensor:
    """How many (i<j, same-graph) pairs overlap by > threshold area."""
    N = boxes.shape[0]
    if N < 2:
        return boxes.new_zeros(())
    x, y, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    x1 = x.unsqueeze(0).expand(N, N)
    x2 = x.unsqueeze(1).expand(N, N)
    w1 = w.unsqueeze(0).expand(N, N)
    w2 = w.unsqueeze(1).expand(N, N)
    y1 = y.unsqueeze(0).expand(N, N)
    y2 = y.unsqueeze(1).expand(N, N)
    h1 = h.unsqueeze(0).expand(N, N)
    h2 = h.unsqueeze(1).expand(N, N)
    x_overlap = torch.clamp(
        torch.min(x1 + w1, x2 + w2) - torch.max(x1, x2), min=0,
    )
    y_overlap = torch.clamp(
        torch.min(y1 + h1, y2 + h2) - torch.max(y1, y2), min=0,
    )
    overlap = x_overlap * y_overlap
    same = batch_idx.unsqueeze(0) == batch_idx.unsqueeze(1)
    upper = torch.triu(
        torch.ones(N, N, dtype=torch.bool, device=boxes.device),
        diagonal=1,
    )
    mask = same & upper
    return ((overlap > threshold) & mask).sum().float()


def oob_node_count(boxes: torch.Tensor) -> torch.Tensor:
    """How many nodes have ANY corner outside [0, 1]²."""
    x, y, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    outside = (
        (x < -1e-4) | (y < -1e-4)
        | (x + w > 1.0 + 1e-4) | (y + h > 1.0 + 1e-4)
    )
    return outside.sum().float()


def per_batch_metrics(
    source_bbox_norm: torch.Tensor,
    pred_delta: torch.Tensor,
    target_delta: torch.Tensor,
    is_protected: torch.Tensor,
    batch_idx: torch.Tensor,
    is_anchor: torch.Tensor | None = None,
) -> dict[str, float]:
    """Compute aggregate metrics for one batch."""
    pred_boxes = predicted_boxes_from(source_bbox_norm, pred_delta)
    target_boxes = predicted_boxes_from(source_bbox_norm, target_delta)
    n_graphs = int(batch_idx.max().item()) + 1 if batch_idx.numel() else 0
    pred_overlap = overlap_pair_count(pred_boxes, batch_idx)
    tgt_overlap = overlap_pair_count(target_boxes, batch_idx)
    pred_oob = oob_node_count(pred_boxes)
    tgt_oob = oob_node_count(target_boxes)
    disp = (pred_delta - target_delta).pow(2).sum(-1).sqrt()
    out = {
        "metric/overlap_pairs_per_graph_pred": (
            pred_overlap.item() / max(1, n_graphs)
        ),
        "metric/overlap_pairs_per_graph_target": (
            tgt_overlap.item() / max(1, n_graphs)
        ),
        "metric/oob_nodes_per_graph_pred": (
            pred_oob.item() / max(1, n_graphs)
        ),
        "metric/oob_nodes_per_graph_target": (
            tgt_oob.item() / max(1, n_graphs)
        ),
        "metric/delta_rmse": disp.mean().item(),
        "metric/n_graphs": n_graphs,
    }
    if is_protected.any():
        prot_disp = (pred_delta[is_protected]).pow(2).sum(-1).sqrt()
        out["metric/protected_displacement"] = prot_disp.mean().item()
    if is_anchor is not None and is_anchor.any():
        anc_disp = (pred_delta[is_anchor]).pow(2).sum(-1).sqrt()
        out["metric/anchor_displacement"] = anc_disp.mean().item()
    return out
