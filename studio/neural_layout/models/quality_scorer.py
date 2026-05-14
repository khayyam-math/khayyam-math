"""Layout-quality scorer: graph → scalar pass/fail probability.

Reuses the edge-aware MHA backbone from `gnn_baseline`. After N
layers of message passing we mean-pool the node embeddings to one
graph-level vector, then a small MLP head outputs a scalar logit.

About 2-4M params at the default config — small enough to call
many times during inference (re-ranking K candidates), and fast
enough to retrain in under 5 minutes on the 5090.

Output convention: ``forward(...)`` returns RAW LOGITS (one per
graph in the batch). Apply `torch.sigmoid` for probabilities.
"""
from __future__ import annotations

import torch
import torch.nn as nn

from ..data import NUM_EDGE_RELS, NUM_NODE_TYPES, NUM_NUMERIC_FEATS
from .gnn_baseline import EdgeAwareMHALayer, FFNBlock


class QualityScorer(nn.Module):
    def __init__(
        self,
        *,
        hidden: int = 192,
        n_layers: int = 4,
        heads: int = 4,
        type_emb_dim: int = 32,
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
        self.head = nn.Sequential(
            nn.Linear(hidden, hidden),
            nn.GELU(),
            nn.Linear(hidden, 1),
        )

    def forward(
        self,
        node_types: torch.Tensor,     # [N]
        numeric_feats: torch.Tensor,  # [N, 14]
        edge_index: torch.Tensor,
        edge_rel: torch.Tensor,
        batch_idx: torch.Tensor,      # [N] which graph each node belongs to
    ) -> torch.Tensor:                # [B] scalar logits
        h = torch.cat([
            self.type_emb(node_types),
            numeric_feats,
        ], dim=-1)
        h = self.input_proj(h)
        for block in self.layers:
            h = block["attn"](h, edge_index, edge_rel)
            h = block["ffn"](h)
        h = self.out_norm(h)
        # Mean-pool over nodes per graph: scatter-add then divide.
        B = int(batch_idx.max().item()) + 1
        pooled = torch.zeros(
            B, h.shape[-1], device=h.device, dtype=h.dtype,
        )
        pooled.scatter_add_(
            0, batch_idx.unsqueeze(-1).expand_as(h), h,
        )
        counts = torch.zeros(B, device=h.device, dtype=h.dtype)
        counts.scatter_add_(
            0, batch_idx, torch.ones_like(batch_idx, dtype=h.dtype),
        )
        pooled = pooled / counts.unsqueeze(-1).clamp_min(1.0)
        return self.head(pooled).squeeze(-1)

    def num_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


def build_default() -> QualityScorer:
    """Default: ~2.5M params."""
    return QualityScorer(
        hidden=192, n_layers=4, heads=4, type_emb_dim=32,
    )
