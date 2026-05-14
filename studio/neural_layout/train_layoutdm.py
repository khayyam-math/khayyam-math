"""Train the LayoutDM denoiser on the clean-target distribution.

Key difference vs `train_gnn.py`:

- We don't use the synthetic (broken, fixed) pairs. We use the
  CLEAN ACCEPTED LAYOUTS ONLY as the target distribution, and let
  the absorbing-state diffusion process inject the noise itself.
  This means the model learns "what good layouts look like" rather
  than "what mistake-X looks like undone".
- Inputs: only the `target` SceneGraph from each pair (which is the
  clean accepted SVG).
- Loss: cross-entropy on MASKED positions (BERT-style), averaged
  over masked tokens only.

Usage:
    .venv/bin/python -m studio.neural_layout.train_layoutdm \\
        --data data/neural_layout/clean_seeds.jsonl \\
        --out runs/layoutdm_v1 \\
        --epochs 50 --batch-size 64
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from studio.neural_layout.data import (  # noqa: E402
    LayoutPairDataset, collate,
)
from studio.neural_layout.diffusion import (  # noqa: E402
    MASK_ID, N_BINS, NoiseSchedule, forward_mask, quantise_bbox_norm,
)
from studio.neural_layout.models.layoutdm import (  # noqa: E402
    build_default, build_small, NUM_NONPOS_FEATS,
)


def _split_indices(n: int, val_frac: float, seed: int) -> tuple[list[int], list[int]]:
    idx = list(range(n))
    rng = random.Random(seed)
    rng.shuffle(idx)
    n_val = max(1, int(n * val_frac))
    return idx[n_val:], idx[:n_val]


class _SubsetDataset(torch.utils.data.Dataset):
    def __init__(self, base, indices):
        self.base = base
        self.indices = indices

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, i):
        return self.base[self.indices[i]]


def _extract_target_features(batch):
    """For LayoutDM training, take the TARGET bbox (clean accepted)
    as the layout to denoise. Returns (target_bbox_norm,
    non_position_features)."""
    # numeric_feats columns: 0-3 = source bbox; 4-9 = text features;
    # 10-13 = viewbox. We need target bbox + non-position feats.
    # The dataset gives us source bbox in numeric_feats; we have to
    # reconstruct target_bbox = source + target_delta.
    source_bbox = batch.numeric_feats[:, :4]
    target_bbox = source_bbox + batch.target_delta
    non_pos = batch.numeric_feats[:, 4:]  # [N, 10]
    return target_bbox.clamp(0, 1.0 - 1e-7), non_pos


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", action="append", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-workers", type=int, default=2)
    ap.add_argument("--device", default=None)
    ap.add_argument("--model-size", choices=["small", "default"],
                    default="default")
    ap.add_argument("--T", type=int, default=100,
                    help="Diffusion steps in the noise schedule.")
    args = ap.parse_args(argv)
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    log_path = args.out / "train.log"
    metric_path = args.out / "metrics.jsonl"
    ckpt_best = args.out / "best.pt"
    ckpt_last = args.out / "last.pt"

    def _log(msg):
        print(msg, flush=True)
        with log_path.open("a") as fh:
            fh.write(msg + "\n")

    def _emit(row):
        with metric_path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    _log("=== train_layoutdm ===")
    _log(f"  device:    {device}")
    _log(f"  out:       {args.out}")
    _log(f"  data:      {args.data}")
    _log(f"  T:         {args.T}")

    dataset = LayoutPairDataset(args.data)
    n_total = len(dataset)
    _log(f"  pairs:     {n_total}")
    train_idx, val_idx = _split_indices(n_total, args.val_frac, args.seed)
    _log(f"  train/val: {len(train_idx)} / {len(val_idx)}")

    train_subset = _SubsetDataset(dataset, train_idx)
    val_subset = _SubsetDataset(dataset, val_idx)
    train_loader = DataLoader(
        train_subset, batch_size=args.batch_size, shuffle=True,
        num_workers=args.num_workers, collate_fn=collate,
        persistent_workers=args.num_workers > 0,
    )
    val_loader = DataLoader(
        val_subset, batch_size=args.batch_size, shuffle=False,
        num_workers=args.num_workers, collate_fn=collate,
        persistent_workers=args.num_workers > 0,
    )

    model = (build_small() if args.model_size == "small"
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
    schedule = NoiseSchedule(T=args.T)

    best_val = float("inf")
    t0 = time.monotonic()
    global_step = 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        ep_t0 = time.monotonic()
        ep_loss = 0.0
        ep_acc = 0.0
        ep_n = 0
        for batch in train_loader:
            batch = batch.to(torch.device(device))
            target_bbox, non_pos = _extract_target_features(batch)
            clean_tokens = quantise_bbox_norm(target_bbox)
            B = int(batch.batch_idx.max().item()) + 1
            t = torch.randint(
                1, args.T + 1, (B,),
                device=clean_tokens.device,
            )
            noisy, mask = forward_mask(
                clean_tokens, t, batch.batch_idx, schedule,
            )
            logits = model(
                node_types=batch.node_types,
                position_tokens=noisy,
                text_feats=non_pos,
                edge_index=batch.edge_index,
                edge_rel=batch.edge_rel,
                t=t,
                batch_idx=batch.batch_idx,
            )  # [N, 4, N_BINS]
            # Only compute loss on masked positions.
            if not mask.any():
                continue
            # Reshape for CE: [N*4, N_BINS] vs targets [N*4]
            logits_flat = logits.reshape(-1, N_BINS)
            tgt_flat = clean_tokens.reshape(-1)
            mask_flat = mask.reshape(-1)
            ce = F.cross_entropy(
                logits_flat[mask_flat], tgt_flat[mask_flat],
                reduction="mean",
            )
            # Accuracy: prediction matches ground truth at masked positions
            with torch.no_grad():
                pred_flat = logits_flat[mask_flat].argmax(-1)
                acc = (pred_flat == tgt_flat[mask_flat]).float().mean()
            optimizer.zero_grad(set_to_none=True)
            ce.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            ep_loss += ce.item()
            ep_acc += acc.item()
            ep_n += 1
            global_step += 1

        ep_loss /= max(1, ep_n)
        ep_acc /= max(1, ep_n)
        ep_dt = time.monotonic() - ep_t0

        model.eval()
        val_loss = 0.0
        val_acc = 0.0
        val_n = 0
        with torch.no_grad():
            for batch in val_loader:
                batch = batch.to(torch.device(device))
                target_bbox, non_pos = _extract_target_features(batch)
                clean = quantise_bbox_norm(target_bbox)
                B = int(batch.batch_idx.max().item()) + 1
                t = torch.randint(1, args.T + 1, (B,),
                                  device=clean.device)
                noisy, mask = forward_mask(
                    clean, t, batch.batch_idx, schedule,
                )
                logits = model(
                    node_types=batch.node_types,
                    position_tokens=noisy,
                    text_feats=non_pos,
                    edge_index=batch.edge_index,
                    edge_rel=batch.edge_rel,
                    t=t,
                    batch_idx=batch.batch_idx,
                )
                logits_flat = logits.reshape(-1, N_BINS)
                tgt_flat = clean.reshape(-1)
                mask_flat = mask.reshape(-1)
                if not mask_flat.any():
                    continue
                ce = F.cross_entropy(
                    logits_flat[mask_flat], tgt_flat[mask_flat],
                )
                pred = logits_flat[mask_flat].argmax(-1)
                val_loss += ce.item()
                val_acc += (pred == tgt_flat[mask_flat]).float().mean().item()
                val_n += 1
        val_loss /= max(1, val_n)
        val_acc /= max(1, val_n)

        row = {
            "epoch": epoch, "step": global_step, "train_seconds": ep_dt,
            "train_loss": ep_loss, "train_acc": ep_acc,
            "val_loss": val_loss, "val_acc": val_acc,
        }
        _emit(row)
        _log(f"epoch {epoch:3d}/{args.epochs}  "
             f"step {global_step}  t={ep_dt:.1f}s  "
             f"loss={ep_loss:.4f}  acc={ep_acc:.3f}  "
             f"vloss={val_loss:.4f}  vacc={val_acc:.3f}")

        torch.save({
            "model": model.state_dict(), "epoch": epoch,
            "args": vars(args), "metrics": row,
        }, ckpt_last)
        if val_loss < best_val:
            best_val = val_loss
            torch.save({
                "model": model.state_dict(), "epoch": epoch,
                "args": vars(args), "metrics": row,
            }, ckpt_best)
            _log(f"  ↑ new best val loss = {val_loss:.4f}")

    total_dt = time.monotonic() - t0
    _log(f"=== done in {total_dt:.1f}s. best val loss = {best_val:.4f} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
