"""Re-ranker: generate K layout candidates per input, score each via
the trained quality model, return the highest-scoring one.

Candidate generators used:

1. **no_op**         — pass through the source SVG unchanged.
2. **planner**       — `studio.layout_planner.plan_layout`.
3. **planner_seeds** — CP-SAT with different random seeds (gives
   multiple in-distribution candidates).
4. **hybrid_seeds**  — LayoutDM denoise + planner, with different
   mask_frac / temperature / seed combinations.

The scorer is run on each candidate's SceneGraph (parsed from the
SVG). The highest-scoring candidate wins.

Public API:
    rerank(svg, *, scorer, layoutdm_model=None, k=8) -> svg
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import torch

from studio.layout_planner import plan_layout
from studio.neural_layout.hybrid import (
    correct_layout_hybrid, correct_layout_planner_only,
)
from studio.neural_layout.models.quality_scorer import (
    QualityScorer, build_default as build_scorer,
)
from studio.neural_layout.svg_to_graph import parse_svg
from studio.neural_layout.train_scorer import _collate, _graph_to_example


@dataclass
class Candidate:
    name: str
    svg: str
    score: float = 0.0


def load_scorer(
    checkpoint_path: str | Path, *, device: str = "cpu",
) -> QualityScorer:
    ckpt = torch.load(checkpoint_path, map_location=device,
                      weights_only=False)
    model = build_scorer()
    model.load_state_dict(ckpt["model"])
    model.to(device)
    model.eval()
    return model


@torch.no_grad()
def score_svg(scorer: QualityScorer, svg: str, device: str = "cpu") -> float:
    """Score one SVG by parsing → graph → scorer head."""
    graph = parse_svg(svg).graph
    if len(graph.nodes) < 2:
        return 0.5  # neutral if we can't parse
    ex = _graph_to_example(graph, label=0, bucket_idx=0)
    if ex is None:
        return 0.5
    batch = _collate([ex]).to(torch.device(device))
    logits = scorer(
        batch.node_types, batch.numeric_feats,
        batch.edge_index, batch.edge_rel, batch.batch_idx,
    )
    return torch.sigmoid(logits).item()


def generate_candidates(
    svg: str,
    *,
    layoutdm_model=None,
    n_planner_seeds: int = 4,
    n_hybrid_seeds: int = 4,
    protected_ids: set[str] | None = None,
) -> list[Candidate]:
    """Produce a diverse pool of corrected-SVG candidates."""
    out: list[Candidate] = []
    # 1) no_op
    out.append(Candidate("no_op", svg))
    # 2) plain planner
    try:
        out.append(Candidate(
            "planner", plan_layout(
                svg, time_limit_s=2.0, protected_ids=protected_ids,
            ),
        ))
    except Exception:
        pass
    # 3) planner with different time-budgets (proxy for different
    #    search depths; OR-tools CP-SAT is deterministic per seed
    #    but plan_layout caches the seed at 42, so we vary
    #    time-limit instead for budget diversity).
    for tl in (0.5, 1.0, 3.0)[:n_planner_seeds]:
        try:
            out.append(Candidate(
                f"planner_t{tl}", plan_layout(
                    svg, time_limit_s=tl, protected_ids=protected_ids,
                ),
            ))
        except Exception:
            pass
    # 4) hybrid with different mask_frac / seed combos
    if layoutdm_model is not None:
        combos = [
            (0.3, 0), (0.5, 1), (0.7, 2), (0.5, 3),
        ][:n_hybrid_seeds]
        for mf, sd in combos:
            try:
                out.append(Candidate(
                    f"hybrid_mf{mf}_s{sd}",
                    correct_layout_hybrid(
                        svg, layoutdm_model,
                        mask_frac=mf, n_steps=8, seed=sd,
                        device="cpu",
                        protected_ids=protected_ids,
                    ),
                ))
            except Exception:
                pass
    return out


def rerank(
    svg: str,
    *,
    scorer: QualityScorer,
    layoutdm_model=None,
    n_planner_seeds: int = 4,
    n_hybrid_seeds: int = 4,
    protected_ids: set[str] | None = None,
    device: str = "cpu",
) -> tuple[Candidate, list[Candidate]]:
    """Generate candidates, score each, return (best, all-sorted-desc)."""
    cands = generate_candidates(
        svg, layoutdm_model=layoutdm_model,
        n_planner_seeds=n_planner_seeds,
        n_hybrid_seeds=n_hybrid_seeds,
        protected_ids=protected_ids,
    )
    for c in cands:
        c.score = score_svg(scorer, c.svg, device=device)
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands[0], cands
