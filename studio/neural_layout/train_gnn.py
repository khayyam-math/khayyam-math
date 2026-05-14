"""Train the Phase B GNN baseline on the layout-correction corpus.

Reads one or more TrainingPair JSONL files (concatenated), splits
90/10 train/val with a fixed seed, trains with the bucket-balanced
sampler, logs progress, and checkpoints best-by-val-overlap.

Usage:
    .venv/bin/python -m studio.neural_layout.train_gnn \\
        --data data/neural_layout/starter_pairs.jsonl \\
        --data data/neural_layout/synthetic_aug_v1.jsonl \\
        --data data/neural_layout/synthetic_aug_v2_from_clean.jsonl \\
        --out runs/gnn_v1 \\
        --epochs 30 --batch-size 64
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from studio.neural_layout.data import (  # noqa: E402
    BucketBalancedSampler, LayoutPairDataset, collate,
)
from studio.neural_layout.eval import per_batch_metrics  # noqa: E402
from studio.neural_layout.losses import (  # noqa: E402
    LossWeights, compute_loss,
)
from studio.neural_layout.models.gnn_baseline import (  # noqa: E402
    build_default, build_large,
)


def _split_indices(n: int, val_frac: float, seed: int) -> tuple[list[int], list[int]]:
    idx = list(range(n))
    rng = random.Random(seed)
    rng.shuffle(idx)
    n_val = max(1, int(n * val_frac))
    return idx[n_val:], idx[:n_val]


def _bucket_idx_fn(dataset: LayoutPairDataset, indices: list[int]):
    """Return a function that maps in-subset index → bucket_idx."""
    cache: dict[int, int] = {}

    def fn(i: int) -> int:
        # `i` here is the subset index (0..len(indices)-1)
        dataset_idx = indices[i]
        if dataset_idx in cache:
            return cache[dataset_idx]
        try:
            ex = dataset[dataset_idx]
            cache[dataset_idx] = ex.bucket_idx
            return ex.bucket_idx
        except Exception:
            cache[dataset_idx] = 19  # "other"
            return 19
    return fn


class SubsetDataset(torch.utils.data.Dataset):
    def __init__(self, base: LayoutPairDataset, indices: list[int]):
        self.base = base
        self.indices = indices

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, i: int):
        return self.base[self.indices[i]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", action="append", required=True,
                    help="JSONL of TrainingPair records. Pass multiple times to concatenate.")
    ap.add_argument("--out", type=Path, required=True,
                    help="Output directory for checkpoints + logs.")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-workers", type=int, default=2)
    ap.add_argument("--device", default=None,
                    help="cuda / cpu / auto-detect")
    ap.add_argument("--max-train-batches", type=int, default=None,
                    help="Cap batches per epoch (smoke testing).")
    ap.add_argument("--model-size", choices=["default", "large"],
                    default="default",
                    help="Model size: default (~2.7M) or large (~11M).")
    ap.add_argument("--delta-weight", type=float, default=1.0,
                    help="Weight on delta-MSE loss. Higher = model "
                         "tries harder to match exact target deltas; "
                         "lower = more emphasis on overlap/OOB.")
    ap.add_argument("--overlap-weight", type=float, default=0.5)
    ap.add_argument("--oob-weight", type=float, default=0.3)
    ap.add_argument("--protected-weight", type=float, default=2.0)
    args = ap.parse_args(argv)

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(args.seed)
    random.seed(args.seed)

    args.out.mkdir(parents=True, exist_ok=True)
    log_path = args.out / "train.log"
    metric_path = args.out / "metrics.jsonl"
    ckpt_best = args.out / "best.pt"
    ckpt_last = args.out / "last.pt"

    def _log(msg: str):
        print(msg, flush=True)
        with log_path.open("a") as fh:
            fh.write(msg + "\n")

    def _emit_metrics(row: dict):
        with metric_path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    _log(f"=== train_gnn ===")
    _log(f"  device:    {device}")
    _log(f"  out:       {args.out}")
    _log(f"  data:      {args.data}")
    _log(f"  epochs:    {args.epochs}")
    _log(f"  batch:     {args.batch_size}")
    _log(f"  lr:        {args.lr}")

    dataset = LayoutPairDataset(args.data)
    n_total = len(dataset)
    _log(f"  pairs:     {n_total}")
    train_idx, val_idx = _split_indices(n_total, args.val_frac, args.seed)
    _log(f"  train/val: {len(train_idx)} / {len(val_idx)}")

    train_subset = SubsetDataset(dataset, train_idx)
    val_subset = SubsetDataset(dataset, val_idx)
    sampler = BucketBalancedSampler(
        train_subset,
        bucket_idx_fn=_bucket_idx_fn(dataset, train_idx),
        length=len(train_idx),
        seed=args.seed,
    )
    train_loader = DataLoader(
        train_subset, batch_size=args.batch_size, sampler=sampler,
        num_workers=args.num_workers, collate_fn=collate,
        persistent_workers=args.num_workers > 0,
    )
    val_loader = DataLoader(
        val_subset, batch_size=args.batch_size, shuffle=False,
        num_workers=args.num_workers, collate_fn=collate,
        persistent_workers=args.num_workers > 0,
    )

    model = (build_large() if args.model_size == "large"
             else build_default()).to(device)
    n_params = model.num_params()
    _log(f"  params:    {n_params:,} ({n_params / 1e6:.2f} M)")
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay,
    )
    total_steps = args.epochs * (
        (len(train_idx) + args.batch_size - 1) // args.batch_size
    )
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=args.lr, total_steps=total_steps,
        pct_start=0.05,
    )
    weights = LossWeights(
        delta=args.delta_weight,
        overlap=args.overlap_weight,
        oob=args.oob_weight,
        protected=args.protected_weight,
    )

    best_val_overlap = float("inf")
    t0 = time.monotonic()
    global_step = 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        ep_t0 = time.monotonic()
        running: dict[str, float] = {"loss/total": 0.0}
        n_batches = 0
        for batch in train_loader:
            if (args.max_train_batches
                    and n_batches >= args.max_train_batches):
                break
            batch = batch.to(torch.device(device))
            pred = model(
                batch.node_types, batch.numeric_feats,
                batch.edge_index, batch.edge_rel,
            )
            source_bbox = batch.numeric_feats[:, :4]
            total, comps = compute_loss(
                pred,
                target_delta=batch.target_delta,
                source_bbox_norm=source_bbox,
                is_protected=batch.is_protected,
                batch_idx=batch.batch_idx,
                weights=weights,
            )
            optimizer.zero_grad(set_to_none=True)
            total.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            for k, v in comps.items():
                running[k] = running.get(k, 0.0) + v
            n_batches += 1
            global_step += 1

        for k in running:
            running[k] /= max(1, n_batches)
        ep_dt = time.monotonic() - ep_t0

        # Validation
        model.eval()
        val_metric_sums: dict[str, float] = {}
        val_n = 0
        with torch.no_grad():
            for batch in val_loader:
                batch = batch.to(torch.device(device))
                pred = model(
                    batch.node_types, batch.numeric_feats,
                    batch.edge_index, batch.edge_rel,
                )
                source_bbox = batch.numeric_feats[:, :4]
                m = per_batch_metrics(
                    source_bbox, pred, batch.target_delta,
                    batch.is_protected, batch.batch_idx,
                )
                _, comps = compute_loss(
                    pred,
                    target_delta=batch.target_delta,
                    source_bbox_norm=source_bbox,
                    is_protected=batch.is_protected,
                    batch_idx=batch.batch_idx,
                    weights=weights,
                )
                for k, v in {**m, **comps}.items():
                    val_metric_sums[f"val/{k}"] = (
                        val_metric_sums.get(f"val/{k}", 0.0) + v
                    )
                val_n += 1
        for k in val_metric_sums:
            val_metric_sums[k] /= max(1, val_n)

        # Log + checkpoint
        emit = {"epoch": epoch, "step": global_step,
                "train_seconds": ep_dt, **running, **val_metric_sums}
        _emit_metrics(emit)
        msg_parts = [
            f"epoch {epoch:3d}/{args.epochs}",
            f"step {global_step}",
            f"t={ep_dt:.1f}s",
            f"loss={running['loss/total']:.4f}",
            f"vloss={val_metric_sums.get('val/loss/total', 0):.4f}",
            f"vovlp={val_metric_sums.get('val/metric/overlap_pairs_per_graph_pred', 0):.2f}",
            f"voob={val_metric_sums.get('val/metric/oob_nodes_per_graph_pred', 0):.2f}",
            f"vdelta={val_metric_sums.get('val/metric/delta_rmse', 0):.4f}",
        ]
        _log("  ".join(msg_parts))

        # Best checkpoint = best val delta RMSE. (Overlap-pair-count
        # is a misleading proxy because predicting near-zero deltas
        # trivially keeps the source-layout's overlap rate, which is
        # often LOWER than the target's — so an untrained model
        # spuriously "wins" on raw overlap. Delta-RMSE measures
        # whether the model actually predicts the right corrections.)
        val_delta = val_metric_sums.get(
            "val/metric/delta_rmse", float("inf"),
        )
        torch.save({
            "model": model.state_dict(),
            "epoch": epoch,
            "args": vars(args),
            "metrics": val_metric_sums,
        }, ckpt_last)
        if val_delta < best_val_overlap:  # variable misnamed; semantic: best metric
            best_val_overlap = val_delta
            torch.save({
                "model": model.state_dict(),
                "epoch": epoch,
                "args": vars(args),
                "metrics": val_metric_sums,
            }, ckpt_best)
            _log(f"  ↑ new best val delta-rmse = {val_delta:.4f}")

    total_dt = time.monotonic() - t0
    _log(f"=== done in {total_dt:.1f}s. best val delta-rmse = {best_val_overlap:.4f} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
