"""Baseline GNN delta-predictor: edge-type-aware message passing.

Pure PyTorch (no PyG dependency). Architecture:

    node-type embedding ─┐
                          ├─ Linear(d_emb + 14) → hidden
    numeric features ────┘

    × N layers:
        h_dst = LN(h + MHA(h, edges))    # edge-aware multi-head
        h = LN(h + FFN(h))

    output_head: Linear(hidden) → 4   # (Δx, Δy, Δw, Δh)

Params at default size (hidden=192, layers=6, heads=4) ≈ 4.5M.
Fits comfortably on an RTX 5090 with batch size > 64.

The output is interpreted as a NORMALISED delta — multiply by
canvas_wh to recover pixel deltas, then add to source bbox.
"""
from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..data import (
    NUM_EDGE_RELS, NUM_NODE_TYPES, NUM_NUMERIC_FEATS,
)


class EdgeAwareMHALayer(nn.Module):
    """Multi-head attention over a sparse graph, with edge-type biases.

    For each directed edge (src → dst) and head h:
        score = <q_dst, k_src + e_emb> / sqrt(d_head)
    Softmax is over the IN-edges of each dst (no global softmax —
    nodes attend only to their own neighbourhood). Then
        msg_dst = Σ_edges softmax(score) * v_src
    """

    def __init__(self, dim: int, n_edge_types: int, heads: int = 4):
        super().__init__()
        assert dim % heads == 0, "dim must be divisible by heads"
        self.dim = dim
        self.heads = heads
        self.d_head = dim // heads
        self.q = nn.Linear(dim, dim, bias=False)
        self.k = nn.Linear(dim, dim, bias=False)
        self.v = nn.Linear(dim, dim, bias=False)
        self.out = nn.Linear(dim, dim)
        self.edge_emb = nn.Embedding(n_edge_types, self.d_head)
        self.norm = nn.LayerNorm(dim)
        self.scale = 1.0 / math.sqrt(self.d_head)

    def forward(
        self, h: torch.Tensor, edge_index: torch.Tensor,
        edge_rel: torch.Tensor,
    ) -> torch.Tensor:
        # h: [N, D]
        # edge_index: [2, E] (src, dst)
        # edge_rel: [E]
        N, D = h.shape
        E = edge_index.shape[1]
        if E == 0:
            return self.norm(h)
        q = self.q(h).view(N, self.heads, self.d_head)
        k = self.k(h).view(N, self.heads, self.d_head)
        v = self.v(h).view(N, self.heads, self.d_head)
        src = edge_index[0]
        dst = edge_index[1]
        e_bias = self.edge_emb(edge_rel)  # [E, d_head] — added to k
        # score per (edge, head)
        q_dst = q[dst]                    # [E, H, d_head]
        k_src = k[src] + e_bias.unsqueeze(1)  # broadcast over heads
        scores = (q_dst * k_src).sum(-1) * self.scale  # [E, H]
        # Softmax over edges grouped by dst: use scatter trick.
        # max-subtract for numerical stability
        with torch.no_grad():
            scores_max = torch.full(
                (N, self.heads), float("-inf"),
                device=h.device, dtype=scores.dtype,
            )
            scores_max.scatter_reduce_(
                0, dst.unsqueeze(1).expand_as(scores),
                scores, reduce="amax", include_self=False,
            )
        # nodes with no in-edges keep -inf; we'll filter below
        scores_max_per_edge = scores_max[dst]
        scores_max_per_edge = torch.where(
            torch.isinf(scores_max_per_edge),
            torch.zeros_like(scores_max_per_edge),
            scores_max_per_edge,
        )
        scores_stable = scores - scores_max_per_edge
        weights_unnorm = torch.exp(scores_stable)  # [E, H]
        # sum per dst per head
        denom = torch.zeros(
            N, self.heads, device=h.device, dtype=weights_unnorm.dtype,
        )
        denom.scatter_add_(
            0, dst.unsqueeze(1).expand_as(weights_unnorm),
            weights_unnorm,
        )
        denom = denom.clamp_min(1e-9)
        weights = weights_unnorm / denom[dst]  # [E, H]
        # weighted sum of v_src into dst
        v_weighted = v[src] * weights.unsqueeze(-1)
        agg = torch.zeros(
            N, self.heads, self.d_head, device=h.device, dtype=v.dtype,
        )
        agg.scatter_add_(
            0,
            dst.view(-1, 1, 1).expand_as(v_weighted),
            v_weighted,
        )
        agg = agg.reshape(N, D)
        out = self.out(agg)
        return self.norm(h + out)


class FFNBlock(nn.Module):
    def __init__(self, dim: int, expansion: int = 4):
        super().__init__()
        self.fc1 = nn.Linear(dim, dim * expansion)
        self.fc2 = nn.Linear(dim * expansion, dim)
        self.norm = nn.LayerNorm(dim)

    def forward(self, h: torch.Tensor) -> torch.Tensor:
        return self.norm(h + self.fc2(F.gelu(self.fc1(h))))


class GraphLayoutCorrector(nn.Module):
    """The Phase B baseline.

    Args:
        hidden: model dim (default 192).
        n_layers: number of attention+FFN blocks (default 6).
        heads: attention heads per block (default 4).
        type_emb_dim: dim of node-type embedding (default 32).
        max_delta_scale: tanh-scaled cap on per-node delta magnitude;
            keeps gradients well-behaved early in training.
    """

    def __init__(
        self,
        *,
        hidden: int = 192,
        n_layers: int = 6,
        heads: int = 4,
        type_emb_dim: int = 32,
        max_delta_scale: float = 1.0,
    ) -> None:
        super().__init__()
        self.hidden = hidden
        self.type_emb = nn.Embedding(NUM_NODE_TYPES, type_emb_dim)
        self.input_proj = nn.Linear(
            type_emb_dim + NUM_NUMERIC_FEATS, hidden,
        )
        self.layers = nn.ModuleList()
        for _ in range(n_layers):
            self.layers.append(nn.ModuleDict({
                "attn": EdgeAwareMHALayer(hidden, NUM_EDGE_RELS, heads),
                "ffn": FFNBlock(hidden),
            }))
        self.out_norm = nn.LayerNorm(hidden)
        self.out_head = nn.Linear(hidden, 4)
        self.max_delta_scale = max_delta_scale

    def forward(
        self,
        node_types: torch.Tensor,     # [N]
        numeric_feats: torch.Tensor,  # [N, 14]
        edge_index: torch.Tensor,     # [2, E]
        edge_rel: torch.Tensor,       # [E]
    ) -> torch.Tensor:                # [N, 4]
        h = torch.cat([
            self.type_emb(node_types),
            numeric_feats,
        ], dim=-1)
        h = self.input_proj(h)
        for block in self.layers:
            h = block["attn"](h, edge_index, edge_rel)
            h = block["ffn"](h)
        h = self.out_norm(h)
        out = self.out_head(h)
        return torch.tanh(out) * self.max_delta_scale

    def num_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


def build_default() -> GraphLayoutCorrector:
    """Default Phase B config — about 4-5M params."""
    return GraphLayoutCorrector(
        hidden=192, n_layers=6, heads=4, type_emb_dim=32,
        max_delta_scale=1.0,
    )


def build_large() -> GraphLayoutCorrector:
    """Larger Phase B config — about 11M params. v2 uses this."""
    return GraphLayoutCorrector(
        hidden=256, n_layers=8, heads=8, type_emb_dim=48,
        max_delta_scale=1.0,
    )
