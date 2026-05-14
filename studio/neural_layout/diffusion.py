"""Discrete absorbing-state diffusion (D3PM) over quantised bbox positions.

Each per-node bbox has 4 dimensions (x, y, w, h). We quantise each
dim to N_BINS bins (default 64), giving a vocabulary of N_BINS
"position tokens" per dimension PLUS one extra MASK token (= N_BINS).

Forward process: at step t in [1..T], each token transitions to MASK
with probability β_t. Schedule is linear: β_t = t / T, so the
cumulative mask probability is t/T.

Reverse process: the model learns to predict the original (unmasked)
position from a partially-masked layout + step embedding. The
denoiser is graph-conditioned: it gets node types, edges, text
features, and the current noisy positions as input.

For REPAIR rather than pure generation, we initialise the denoising
with the broken layout's positions and only mask a fraction (e.g.
30-50%) chosen to corrupt the worst-overlap nodes; then we
iteratively unmask. This way the network can leverage the broken
positions as priors instead of starting from scratch.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch

N_BINS = 64
MASK_ID = N_BINS  # the (N_BINS+1)-th token id
N_TOKENS = N_BINS + 1


def quantise_bbox_norm(
    bbox_norm: torch.Tensor,
) -> torch.Tensor:
    """[N, 4] in [0, 1] → [N, 4] long token ids in [0, N_BINS-1]."""
    return (bbox_norm.clamp(0, 1.0 - 1e-7) * N_BINS).long()


def dequantise_tokens(
    tokens: torch.Tensor,
) -> torch.Tensor:
    """[N, 4] token ids → [N, 4] normalised coords in [0, 1].

    A token of MASK_ID is decoded as 0.0 (caller should handle
    masked tokens specially).
    """
    return ((tokens.clamp_max(N_BINS - 1).float() + 0.5) / N_BINS)


@dataclass
class NoiseSchedule:
    """Linear absorbing schedule. β_t = t / T monotonically."""
    T: int = 100

    def mask_prob(self, t: torch.Tensor) -> torch.Tensor:
        """Per-step cumulative mask probability for step t."""
        return t.float() / float(self.T)


def forward_mask(
    tokens: torch.Tensor,    # [N, 4] long, values in [0, N_BINS-1]
    t: torch.Tensor,         # [B] long, one t per graph
    batch_idx: torch.Tensor, # [N] long
    schedule: NoiseSchedule,
    *,
    generator: torch.Generator | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Apply the forward absorbing process.

    Returns (noisy_tokens, mask) where:
    - noisy_tokens[n, k] = MASK_ID if dropped else tokens[n, k]
    - mask[n, k] = True if that position was masked
    """
    p = schedule.mask_prob(t)[batch_idx]    # [N]
    p4 = p.unsqueeze(-1).expand(-1, 4)      # [N, 4]
    rand = torch.rand(
        tokens.shape, device=tokens.device, generator=generator,
    )
    mask = rand < p4
    noisy = torch.where(
        mask, torch.full_like(tokens, MASK_ID), tokens,
    )
    return noisy, mask


def sinusoidal_step_embedding(
    t: torch.Tensor, dim: int,
) -> torch.Tensor:
    """[B] long → [B, dim] sinusoidal embedding."""
    device = t.device
    half = dim // 2
    freq = torch.exp(
        -math.log(10000.0) * torch.arange(half, device=device).float() / max(half - 1, 1)
    )
    angles = t.float().unsqueeze(-1) * freq.unsqueeze(0)
    emb = torch.cat([torch.sin(angles), torch.cos(angles)], dim=-1)
    if emb.shape[-1] < dim:
        pad = torch.zeros(t.shape[0], dim - emb.shape[-1], device=device)
        emb = torch.cat([emb, pad], dim=-1)
    return emb


@torch.no_grad()
def denoise(
    model,
    *,
    node_types: torch.Tensor,
    text_feats: torch.Tensor,       # [N, F] non-position features
    edge_index: torch.Tensor,
    edge_rel: torch.Tensor,
    batch_idx: torch.Tensor,
    init_tokens: torch.Tensor | None = None,  # [N, 4] or None
    init_mask: torch.Tensor | None = None,    # [N, 4] bool — which to denoise
    n_steps: int = 16,
    schedule: NoiseSchedule = NoiseSchedule(),
    temperature: float = 0.7,
    device: torch.device | str = "cuda",
) -> torch.Tensor:
    """Iteratively denoise from `init_tokens` masked at `init_mask`.

    If init_tokens is None, starts with all-MASK. If init_mask is None,
    denoises every position (full generation).

    Returns final [N, 4] tokens.
    """
    N = node_types.shape[0]
    device = torch.device(device)
    if init_tokens is None:
        cur = torch.full((N, 4), MASK_ID, dtype=torch.long, device=device)
        mask = torch.ones((N, 4), dtype=torch.bool, device=device)
    else:
        cur = init_tokens.clone().to(device)
        if init_mask is None:
            mask = torch.ones((N, 4), dtype=torch.bool, device=device)
        else:
            mask = init_mask.clone().to(device)
        cur = torch.where(mask, torch.full_like(cur, MASK_ID), cur)

    # iteratively unmask
    for step_i in range(n_steps):
        # The notional "diffusion time" we're inverting.
        t_value = schedule.T - int(step_i * schedule.T / n_steps)
        t = torch.full(
            (int(batch_idx.max().item()) + 1,),
            t_value, dtype=torch.long, device=device,
        )
        logits = model(
            node_types=node_types,
            position_tokens=cur,
            text_feats=text_feats,
            edge_index=edge_index,
            edge_rel=edge_rel,
            t=t,
            batch_idx=batch_idx,
        )  # [N, 4, N_BINS]
        logits = logits / max(temperature, 1e-3)
        probs = torch.softmax(logits, dim=-1)
        # sample where currently masked
        flat_probs = probs.reshape(-1, N_BINS)  # [N*4, N_BINS]
        flat_sample = torch.multinomial(flat_probs, num_samples=1).squeeze(-1)
        sampled = flat_sample.reshape(N, 4)
        # confidence per (node, dim): the prob of the sampled class
        flat_conf = flat_probs.gather(1, flat_sample.unsqueeze(1)).squeeze(1)
        conf = flat_conf.reshape(N, 4)
        # decide which masked positions to commit this round
        n_masked = mask.sum().item()
        if n_masked == 0:
            break
        # commit the top-k most-confident currently-masked positions
        keep_frac = (step_i + 1) / n_steps
        n_keep = max(1, int(keep_frac * n_masked))
        masked_flat_conf = torch.where(
            mask.reshape(-1), conf.reshape(-1),
            torch.full_like(conf.reshape(-1), -1.0),
        )
        topk = torch.topk(masked_flat_conf, k=n_keep).indices
        # build new mask: clear masked-bit at the chosen positions
        new_mask = mask.clone().reshape(-1)
        new_mask[topk] = False
        new_mask = new_mask.reshape(N, 4)
        # update cur where we just committed
        commit = mask & (~new_mask)
        cur = torch.where(commit, sampled, cur)
        mask = new_mask
    # any still-masked positions get filled with the argmax prediction
    if mask.any():
        logits = model(
            node_types=node_types,
            position_tokens=cur,
            text_feats=text_feats,
            edge_index=edge_index,
            edge_rel=edge_rel,
            t=torch.zeros_like(t),
            batch_idx=batch_idx,
        )
        cur = torch.where(mask, logits.argmax(-1), cur)
    return cur
