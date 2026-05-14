"""Evaluate a trained GNN checkpoint, with per-bucket breakdown.

For every validation example, computes:
- source→target distance (how broken the input was)
- predicted→target distance (how close the model got)
- overlap-pair count: source vs predicted vs target
- OOB-node count: source vs predicted vs target
- per-bucket aggregates

Reports both the macro-average and the bucket-stratified average, so
we can see whether the model is doing well overall but poorly on
under-represented categories.

Usage:
    .venv/bin/python scripts/eval_gnn.py \\
        --ckpt runs/gnn_v2/best.pt \\
        --data data/neural_layout/starter_pairs.jsonl \\
        --data data/neural_layout/synthetic_aug_v2_from_clean.jsonl \\
        --seed 42 --val-frac 0.1
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import torch
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.neural_layout.data import (  # noqa: E402
    LayoutPairDataset, collate,
)
from studio.neural_layout.eval import (  # noqa: E402
    oob_node_count, overlap_pair_count, predicted_boxes_from,
)
from studio.neural_layout.inference import load_model  # noqa: E402
from studio.neural_layout.schema import MATH_BUCKETS  # noqa: E402


@torch.no_grad()
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", required=True, type=Path)
    ap.add_argument("--data", action="append", required=True)
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--device", default=None)
    args = ap.parse_args(argv)
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")

    model = load_model(args.ckpt, device=device)
    ds = LayoutPairDataset(args.data)
    n = len(ds)
    rng = random.Random(args.seed)
    idx = list(range(n))
    rng.shuffle(idx)
    n_val = max(1, int(n * args.val_frac))
    val_idx = idx[:n_val]
    print(f"checkpoint: {args.ckpt}")
    print(f"data:       {n} pairs, evaluating on {n_val} val")

    # Per-bucket buckets
    sums: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float),
    )
    counts: dict[str, int] = defaultdict(int)

    for i in val_idx:
        try:
            ex = ds[i]
        except Exception:
            continue
        batch = collate([ex]).to(torch.device(device))
        pred = model(
            batch.node_types, batch.numeric_feats,
            batch.edge_index, batch.edge_rel,
        )
        source_bbox = batch.numeric_feats[:, :4]
        pred_boxes = predicted_boxes_from(source_bbox, pred)
        target_boxes = predicted_boxes_from(
            source_bbox, batch.target_delta,
        )
        bidx = batch.batch_idx
        src_ovlp = overlap_pair_count(source_bbox, bidx).item()
        pred_ovlp = overlap_pair_count(pred_boxes, bidx).item()
        tgt_ovlp = overlap_pair_count(target_boxes, bidx).item()
        src_oob = oob_node_count(source_bbox).item()
        pred_oob = oob_node_count(pred_boxes).item()
        tgt_oob = oob_node_count(target_boxes).item()
        # per-node distances
        src_dist = (batch.target_delta).pow(2).sum(-1).sqrt().mean().item()
        pred_dist = (
            (pred - batch.target_delta).pow(2).sum(-1).sqrt().mean().item()
        )
        bucket_name = MATH_BUCKETS[ex.bucket_idx]
        agg = sums[bucket_name]
        agg["src_ovlp"] += src_ovlp
        agg["pred_ovlp"] += pred_ovlp
        agg["tgt_ovlp"] += tgt_ovlp
        agg["src_oob"] += src_oob
        agg["pred_oob"] += pred_oob
        agg["tgt_oob"] += tgt_oob
        agg["src_dist"] += src_dist
        agg["pred_dist"] += pred_dist
        counts[bucket_name] += 1

    # Roll up
    print()
    hdr = ["bucket", "n", "src→tgt", "pred→tgt", "improve%",
           "ovlp:s→p→t", "oob:s→p→t"]
    print(f"{hdr[0]:22s} {hdr[1]:>5s} {hdr[2]:>8s} {hdr[3]:>9s} "
          f"{hdr[4]:>10s} {hdr[5]:>16s} {hdr[6]:>14s}")
    total: dict[str, float] = defaultdict(float)
    total_n = 0
    for bucket in sorted(counts, key=counts.get, reverse=True):
        n = counts[bucket]
        if n == 0:
            continue
        a = sums[bucket]
        src_d = a["src_dist"] / n
        pred_d = a["pred_dist"] / n
        improve_pct = 100.0 * (1 - pred_d / max(src_d, 1e-9))
        ovlp = (f"{a['src_ovlp']/n:.1f}→"
                f"{a['pred_ovlp']/n:.1f}→{a['tgt_ovlp']/n:.1f}")
        oob = (f"{a['src_oob']/n:.1f}→"
               f"{a['pred_oob']/n:.1f}→{a['tgt_oob']/n:.1f}")
        print(f"{bucket:22s} {n:>5d} "
              f"{src_d:>8.4f} {pred_d:>9.4f} {improve_pct:>9.1f}% "
              f"{ovlp:>16s} {oob:>14s}")
        for k, v in a.items():
            total[k] += v
        total_n += n
    print()
    if total_n > 0:
        src_d = total["src_dist"] / total_n
        pred_d = total["pred_dist"] / total_n
        improve_pct = 100.0 * (1 - pred_d / max(src_d, 1e-9))
        ovlp = (f"{total['src_ovlp']/total_n:.1f}→"
                f"{total['pred_ovlp']/total_n:.1f}→"
                f"{total['tgt_ovlp']/total_n:.1f}")
        oob = (f"{total['src_oob']/total_n:.1f}→"
               f"{total['pred_oob']/total_n:.1f}→"
               f"{total['tgt_oob']/total_n:.1f}")
        print(f"{'OVERALL':22s} {total_n:>5d} "
              f"{src_d:>8.4f} {pred_d:>9.4f} {improve_pct:>9.1f}% "
              f"{ovlp:>16s} {oob:>14s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
