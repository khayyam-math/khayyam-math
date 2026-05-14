"""Loss functions for layout-correction training.

The model predicts per-node deltas (Δx, Δy, Δw, Δh) in *normalised*
coords (divided by canvas_w, canvas_h). To compute structural
penalties (overlap, OOB) we have to convert back to a common
0-1 box space.

Total loss = w_d * delta_mse + w_o * overlap + w_b * oob + w_p * protected
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F


@dataclass
class LossWeights:
    delta: float = 1.0
    overlap: float = 0.5
    oob: float = 0.3
    protected: float = 2.0  # pin protected nodes hard


def delta_mse(
    pred_delta: torch.Tensor,    # [N, 4]
    target_delta: torch.Tensor,  # [N, 4]
) -> torch.Tensor:
    return F.smooth_l1_loss(pred_delta, target_delta, beta=0.02)


def protected_penalty(
    pred_delta: torch.Tensor,
    is_protected: torch.Tensor,  # [N] bool
) -> torch.Tensor:
    """Protected nodes (titles, axis labels) should never move."""
    if not is_protected.any():
        return pred_delta.new_zeros(())
    mask = is_protected.float().unsqueeze(-1)
    return (pred_delta.pow(2) * mask).sum() / (mask.sum() + 1e-9)


def _predicted_boxes(
    source_bbox_norm: torch.Tensor,  # [N, 4] in normalised coords
    pred_delta: torch.Tensor,        # [N, 4]
) -> torch.Tensor:
    """source_bbox_norm + pred_delta → predicted box, clamped.

    Built without in-place slice writes so the autograd graph stays
    intact when this is called inside a loss.
    """
    pred = source_bbox_norm + pred_delta
    xy = pred[:, :2]
    wh = pred[:, 2:].clamp_min(1e-4)
    return torch.cat([xy, wh], dim=-1)


def overlap_penalty(
    pred_boxes: torch.Tensor,   # [N, 4]   (x, y, w, h) all in [0, 1]
    batch_idx: torch.Tensor,    # [N]
) -> torch.Tensor:
    """Soft pairwise overlap penalty, computed per-graph.

    For each pair of nodes (i, j) in the same graph, the overlap area
    is max(0, x_overlap) * max(0, y_overlap). We sum and average over
    the number of pairs per graph.

    This is O(N²) per graph, but our graphs are <100 nodes so it's
    fine. For batched processing we mask out cross-graph pairs.
    """
    N = pred_boxes.shape[0]
    if N < 2:
        return pred_boxes.new_zeros(())
    x = pred_boxes[:, 0]
    y = pred_boxes[:, 1]
    w = pred_boxes[:, 2]
    h = pred_boxes[:, 3]
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
    # Mask: same graph, i != j, upper triangle only.
    same_graph = batch_idx.unsqueeze(0) == batch_idx.unsqueeze(1)
    diag = torch.eye(N, dtype=torch.bool, device=pred_boxes.device)
    mask = same_graph & (~diag)
    overlap = overlap * mask.float()
    # avg over pairs to keep loss scale ~constant w.r.t. N
    n_pairs = mask.sum().clamp_min(1)
    return overlap.sum() / n_pairs


def oob_penalty(
    pred_boxes: torch.Tensor,   # [N, 4] normalised
) -> torch.Tensor:
    """Pull boxes back inside [0, 1]² by squared-distance penalty."""
    x, y, w, h = pred_boxes[:, 0], pred_boxes[:, 1], pred_boxes[:, 2], pred_boxes[:, 3]
    left = torch.clamp(-x, min=0).pow(2)
    top = torch.clamp(-y, min=0).pow(2)
    right = torch.clamp(x + w - 1.0, min=0).pow(2)
    bottom = torch.clamp(y + h - 1.0, min=0).pow(2)
    return (left + top + right + bottom).mean()


def compute_loss(
    pred_delta: torch.Tensor,
    *,
    target_delta: torch.Tensor,
    source_bbox_norm: torch.Tensor,
    is_protected: torch.Tensor,
    batch_idx: torch.Tensor,
    weights: LossWeights = LossWeights(),
) -> tuple[torch.Tensor, dict[str, float]]:
    """Returns (total, components_for_logging)."""
    delta_l = delta_mse(pred_delta, target_delta)
    proto_l = protected_penalty(pred_delta, is_protected)
    pred_boxes = _predicted_boxes(source_bbox_norm, pred_delta)
    overlap_l = overlap_penalty(pred_boxes, batch_idx)
    oob_l = oob_penalty(pred_boxes)
    total = (
        weights.delta * delta_l
        + weights.overlap * overlap_l
        + weights.oob * oob_l
        + weights.protected * proto_l
    )
    return total, {
        "loss/total": total.item(),
        "loss/delta": delta_l.item(),
        "loss/overlap": overlap_l.item(),
        "loss/oob": oob_l.item(),
        "loss/protected": proto_l.item(),
    }
