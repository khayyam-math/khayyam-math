"""LayoutDM-style graph-conditioned discrete diffusion denoiser.

Reuses the edge-aware multi-head attention from `gnn_baseline` but
swaps the input from continuous bbox features to per-dimension
position-token embeddings + step-embedding conditioning, and the
output from continuous deltas to per-dimension token-classifier
logits.

Architecture:

    type_emb + non-position numeric (10 dims: text-len, font, stroke,
      3 flags, viewbox 4) +
    per-dimension position-token embeddings (4 × token_emb_dim) +
    broadcast step-embedding (after FiLM) ────► hidden

    × N graph-transformer blocks (edge-aware MHA + FFN)

    output_head: Linear(hidden) → 4 × N_BINS logits per node

About 6-12M params depending on hidden/layers.
"""
from __future__ import annotations

import torch
import torch.nn as nn

from ..data import NUM_EDGE_RELS, NUM_NODE_TYPES
from ..diffusion import N_TOKENS, sinusoidal_step_embedding, N_BINS
from .gnn_baseline import EdgeAwareMHALayer, FFNBlock


# Non-position numeric features the LayoutDM model sees (everything
# EXCEPT the 4 bbox dims, which become discrete tokens).
NUM_NONPOS_FEATS = 10  # text_len, font, stroke, 3 flags, vb_x, vb_y, vb_w, vb_h


class FiLM(nn.Module):
    """Feature-wise linear modulation from a step embedding."""

    def __init__(self, step_dim: int, hidden: int):
        super().__init__()
        self.proj = nn.Linear(step_dim, hidden * 2)

    def forward(self, h: torch.Tensor, step_emb_per_node: torch.Tensor) -> torch.Tensor:
        gamma_beta = self.proj(step_emb_per_node)
        gamma, beta = gamma_beta.chunk(2, dim=-1)
        return h * (1.0 + gamma) + beta


class LayoutDMDenoiser(nn.Module):
    def __init__(
        self,
        *,
        hidden: int = 256,
        n_layers: int = 8,
        heads: int = 8,
        type_emb_dim: int = 48,
        pos_token_emb_dim: int = 48,
        step_emb_dim: int = 96,
    ) -> None:
        super().__init__()
        self.hidden = hidden
        self.type_emb = nn.Embedding(NUM_NODE_TYPES, type_emb_dim)
        # 4 separate embeddings for (x, y, w, h) — same vocab size
        # (N_BINS + 1 to include MASK)
        self.pos_emb = nn.ModuleList([
            nn.Embedding(N_TOKENS, pos_token_emb_dim)
            for _ in range(4)
        ])
        input_dim = (
            type_emb_dim
            + NUM_NONPOS_FEATS
            + 4 * pos_token_emb_dim
        )
        self.input_proj = nn.Linear(input_dim, hidden)
        self.step_emb_dim = step_emb_dim
        self.layers = nn.ModuleList()
        for _ in range(n_layers):
            self.layers.append(nn.ModuleDict({
                "attn": EdgeAwareMHALayer(hidden, NUM_EDGE_RELS, heads),
                "ffn": FFNBlock(hidden),
                "film": FiLM(step_emb_dim, hidden),
            }))
        self.out_norm = nn.LayerNorm(hidden)
        # Per-dim output head: 4 × N_BINS classifier
        self.out_heads = nn.ModuleList([
            nn.Linear(hidden, N_BINS) for _ in range(4)
        ])

    def forward(
        self,
        *,
        node_types: torch.Tensor,        # [N]
        position_tokens: torch.Tensor,   # [N, 4] long
        text_feats: torch.Tensor,        # [N, NUM_NONPOS_FEATS]
        edge_index: torch.Tensor,        # [2, E]
        edge_rel: torch.Tensor,          # [E]
        t: torch.Tensor,                 # [B] long
        batch_idx: torch.Tensor,         # [N]
    ) -> torch.Tensor:                   # [N, 4, N_BINS]
        type_e = self.type_emb(node_types)
        pos_e_list = [self.pos_emb[k](position_tokens[:, k])
                      for k in range(4)]
        h = torch.cat([type_e, text_feats] + pos_e_list, dim=-1)
        h = self.input_proj(h)
        # Step embedding -> per-node by gather along batch_idx
        step_emb = sinusoidal_step_embedding(t, self.step_emb_dim)
        step_emb_per_node = step_emb[batch_idx]
        for block in self.layers:
            h = block["attn"](h, edge_index, edge_rel)
            h = block["ffn"](h)
            h = block["film"](h, step_emb_per_node)
        h = self.out_norm(h)
        outs = torch.stack([head(h) for head in self.out_heads], dim=1)
        return outs  # [N, 4, N_BINS]

    def num_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


def build_default() -> LayoutDMDenoiser:
    """LayoutDM default ≈ 8M params."""
    return LayoutDMDenoiser(
        hidden=256, n_layers=8, heads=8,
        type_emb_dim=48, pos_token_emb_dim=48, step_emb_dim=96,
    )


def build_small() -> LayoutDMDenoiser:
    """LayoutDM small ≈ 2-3M params, for fast iteration."""
    return LayoutDMDenoiser(
        hidden=128, n_layers=6, heads=4,
        type_emb_dim=32, pos_token_emb_dim=32, step_emb_dim=64,
    )
