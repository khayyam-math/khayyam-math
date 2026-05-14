"""Train the layout-quality scorer.

Input data: a JSONL of {"label": 0|1, "scene_graph": {...}, "prompt": "...",
"math_bucket": "..."} records, produced by
``scripts/extract_quality_labels.py``.

Class-weighted BCE: PASS examples outnumber FAIL roughly 3:1, so
we upweight the FAIL class to keep gradients balanced.

Usage:
    .venv/bin/python -m studio.neural_layout.train_scorer \\
        --data data/neural_layout/quality_labels.jsonl \\
        --out runs/quality_scorer_v1 \\
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
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from studio.neural_layout.data import (  # noqa: E402
    _edge_rel_idx, _node_type_idx, _numeric_features, _type_indices,
    _edge_tensors, GraphBatch, GraphExample,
)
from studio.neural_layout.models.quality_scorer import (  # noqa: E402
    QualityScorer, build_default,
)
from studio.neural_layout.schema import SceneGraph  # noqa: E402


# ── A small custom dataset because we need a different example
#    shape (no source/target deltas — just a single graph with a
#    label).
class _QualityExample:
    __slots__ = (
        "node_types", "numeric_feats", "edge_index", "edge_rel",
        "label", "bucket_idx",
    )

    def __init__(self, node_types, numeric_feats, edge_index,
                 edge_rel, label, bucket_idx):
        self.node_types = node_types
        self.numeric_feats = numeric_feats
        self.edge_index = edge_index
        self.edge_rel = edge_rel
        self.label = label
        self.bucket_idx = bucket_idx

    @property
    def n_nodes(self):
        return self.node_types.shape[0]


def _graph_to_example(graph: SceneGraph, label: int, bucket_idx: int):
    if not graph.nodes:
        return None
    id_to_idx = {n.id: i for i, n in enumerate(graph.nodes)}
    node_types = _type_indices(graph)
    numeric = _numeric_features(graph)
    edge_index, edge_rel = _edge_tensors(graph, id_to_idx)
    return _QualityExample(
        node_types=node_types, numeric_feats=numeric,
        edge_index=edge_index, edge_rel=edge_rel,
        label=label, bucket_idx=bucket_idx,
    )


class QualityDataset(Dataset):
    def __init__(self, path: Path, min_nodes: int = 2, max_nodes: int = 250):
        self._records: list[tuple[Path, int]] = []
        self._cache: dict[int, _QualityExample] = {}
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes
        offset = 0
        with path.open("rb") as fh:
            for line in fh:
                if line.strip():
                    self._records.append((path, offset))
                offset += len(line)

    def __len__(self):
        return len(self._records)

    def _load(self, idx: int):
        path, off = self._records[idx]
        with path.open("rb") as fh:
            fh.seek(off)
            line = fh.readline()
        try:
            row = json.loads(line)
            sg = SceneGraph.from_dict(row["scene_graph"])
        except Exception:
            return None
        from studio.neural_layout.schema import MATH_BUCKETS
        b = row.get("math_bucket", "other")
        try:
            bidx = MATH_BUCKETS.index(b)
        except ValueError:
            bidx = MATH_BUCKETS.index("other")
        ex = _graph_to_example(sg, int(row["label"]), bidx)
        if ex is None:
            return None
        if not (self.min_nodes <= ex.n_nodes <= self.max_nodes):
            return None
        return ex

    def __getitem__(self, idx: int) -> _QualityExample:
        cached = self._cache.get(idx)
        if cached is not None:
            return cached
        N = len(self._records)
        for off in range(N):
            cur = (idx + off) % N
            cached = self._cache.get(cur)
            if cached is not None:
                return cached
            ex = self._load(cur)
            if ex is not None:
                self._cache[cur] = ex
                return ex
        raise RuntimeError("no valid examples")


def _collate(examples: list[_QualityExample]) -> GraphBatch:
    """Stacks into a GraphBatch but with `target_delta` repurposed
    as the per-graph label (broadcast to one row per graph). We
    reuse GraphBatch for convenience even though the field is
    semantically different here."""
    node_offsets = [0]
    for ex in examples:
        node_offsets.append(node_offsets[-1] + ex.n_nodes)
    node_types = torch.cat([ex.node_types for ex in examples], dim=0)
    numeric = torch.cat([ex.numeric_feats for ex in examples], dim=0)
    batch_idx_parts = []
    edges_parts = []
    edge_rel_parts = []
    for b, ex in enumerate(examples):
        batch_idx_parts.append(
            torch.full((ex.n_nodes,), b, dtype=torch.long),
        )
        if ex.edge_index.numel() > 0:
            edges_parts.append(ex.edge_index + node_offsets[b])
            edge_rel_parts.append(ex.edge_rel)
    batch_idx = torch.cat(batch_idx_parts, dim=0)
    edge_index = (
        torch.cat(edges_parts, dim=1) if edges_parts
        else torch.zeros((2, 0), dtype=torch.long)
    )
    edge_rel = (
        torch.cat(edge_rel_parts, dim=0) if edge_rel_parts
        else torch.zeros((0,), dtype=torch.long)
    )
    labels = torch.tensor(
        [ex.label for ex in examples], dtype=torch.float32,
    )
    return GraphBatch(
        node_types=node_types, numeric_feats=numeric,
        edge_index=edge_index, edge_rel=edge_rel,
        target_delta=labels,    # repurposed
        is_protected=torch.zeros(node_types.shape[0], dtype=torch.bool),
        batch_idx=batch_idx,
        canvas_wh=torch.ones(len(examples), 2),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-workers", type=int, default=2)
    ap.add_argument("--device", default=None)
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

    _log("=== train_scorer ===")
    ds = QualityDataset(args.data)
    n = len(ds)
    _log(f"  data: {args.data}  pairs: {n}")
    # Compute class balance and per-record pos weight
    labels = []
    for i in range(min(n, 100000)):  # sample for class weight
        try:
            ex = ds._load(i)
            if ex is not None:
                labels.append(ex.label)
        except Exception:
            continue
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    pos_weight = (n_neg / max(1, n_pos))
    _log(f"  class balance: pos={n_pos} neg={n_neg} → pos_weight={pos_weight:.3f}")

    idx = list(range(n))
    random.Random(args.seed).shuffle(idx)
    n_val = max(1, int(n * args.val_frac))
    val_idx = idx[:n_val]
    train_idx = idx[n_val:]
    _log(f"  train/val: {len(train_idx)} / {len(val_idx)}")

    class Subset(torch.utils.data.Dataset):
        def __init__(self, base, indices):
            self.base, self.indices = base, indices
        def __len__(self):
            return len(self.indices)
        def __getitem__(self, i):
            return self.base[self.indices[i]]

    train_loader = DataLoader(
        Subset(ds, train_idx), batch_size=args.batch_size,
        shuffle=True, num_workers=args.num_workers,
        collate_fn=_collate,
        persistent_workers=args.num_workers > 0,
    )
    val_loader = DataLoader(
        Subset(ds, val_idx), batch_size=args.batch_size,
        shuffle=False, num_workers=args.num_workers,
        collate_fn=_collate,
        persistent_workers=args.num_workers > 0,
    )
    model = build_default().to(device)
    _log(f"  params: {model.num_params():,} ({model.num_params()/1e6:.2f} M)")
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr,
        weight_decay=args.weight_decay,
    )
    total_steps = args.epochs * (
        (len(train_idx) + args.batch_size - 1) // args.batch_size
    )
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=args.lr, total_steps=total_steps,
        pct_start=0.05,
    )
    bce_pos_weight = torch.tensor([pos_weight], device=device)

    best_val_auc = 0.0
    t0 = time.monotonic()
    global_step = 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        ep_t0 = time.monotonic()
        ep_loss = 0.0
        ep_correct = 0
        ep_total = 0
        for batch in train_loader:
            batch = batch.to(torch.device(device))
            logits = model(
                batch.node_types, batch.numeric_feats,
                batch.edge_index, batch.edge_rel,
                batch.batch_idx,
            )
            labels = batch.target_delta  # repurposed as labels
            loss = F.binary_cross_entropy_with_logits(
                logits, labels, pos_weight=bce_pos_weight,
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            with torch.no_grad():
                pred = (torch.sigmoid(logits) > 0.5).float()
                ep_correct += (pred == labels).sum().item()
                ep_total += labels.shape[0]
            ep_loss += loss.item() * labels.shape[0]
            global_step += 1
        train_loss = ep_loss / max(1, ep_total)
        train_acc = ep_correct / max(1, ep_total)

        # validation
        model.eval()
        all_logits, all_labels = [], []
        with torch.no_grad():
            for batch in val_loader:
                batch = batch.to(torch.device(device))
                logits = model(
                    batch.node_types, batch.numeric_feats,
                    batch.edge_index, batch.edge_rel,
                    batch.batch_idx,
                )
                all_logits.append(logits.cpu())
                all_labels.append(batch.target_delta.cpu())
        all_logits = torch.cat(all_logits)
        all_labels = torch.cat(all_labels)
        val_loss = F.binary_cross_entropy_with_logits(
            all_logits, all_labels, pos_weight=bce_pos_weight.cpu(),
        ).item()
        val_pred = (torch.sigmoid(all_logits) > 0.5).float()
        val_acc = (val_pred == all_labels).float().mean().item()
        # AUC via Mann-Whitney U statistic: P(pos > neg) for
        # random pos/neg pair. AUC = U / (n_pos * n_neg).
        probs = torch.sigmoid(all_logits)
        # Ranks (1-indexed); ties get average rank.
        sorted_idx = probs.argsort()
        ranks = torch.empty_like(probs, dtype=torch.float64)
        ranks[sorted_idx] = torch.arange(
            1, len(probs) + 1, dtype=torch.float64,
        )
        n_pos = int((all_labels > 0.5).sum().item())
        n_neg = len(all_labels) - n_pos
        if n_pos == 0 or n_neg == 0:
            val_auc = float("nan")
        else:
            sum_ranks_pos = ranks[all_labels > 0.5].sum().item()
            val_auc = (
                (sum_ranks_pos - n_pos * (n_pos + 1) / 2.0)
                / (n_pos * n_neg)
            )
        ep_dt = time.monotonic() - ep_t0
        emit = {
            "epoch": epoch, "step": global_step,
            "train_loss": train_loss, "train_acc": train_acc,
            "val_loss": val_loss, "val_acc": val_acc, "val_auc": val_auc,
            "train_seconds": ep_dt,
        }
        _emit(emit)
        _log(f"epoch {epoch:3d}/{args.epochs}  t={ep_dt:.1f}s  "
             f"tloss={train_loss:.3f}  tacc={train_acc:.3f}  "
             f"vloss={val_loss:.3f}  vacc={val_acc:.3f}  "
             f"vauc={val_auc:.4f}")
        torch.save({
            "model": model.state_dict(),
            "epoch": epoch,
            "args": vars(args),
            "metrics": emit,
        }, ckpt_last)
        if val_auc > best_val_auc:
            best_val_auc = val_auc
            torch.save({
                "model": model.state_dict(),
                "epoch": epoch,
                "args": vars(args),
                "metrics": emit,
            }, ckpt_best)
            _log(f"  ↑ new best val AUC = {val_auc:.4f}")

    total_dt = time.monotonic() - t0
    _log(f"=== done in {total_dt:.1f}s. best val AUC = {best_val_auc:.4f} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
