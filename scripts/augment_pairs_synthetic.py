"""Augment a TrainingPair JSONL with synthetic perturbations.

Strategy: take the `target` (accepted layout) of every input pair,
perturb it K times, and emit each perturbation as a NEW source paired
with the ORIGINAL target. The original prompt and viewport are
preserved. Source-side perturbations expand the model's exposure to
controlled-damage layouts without burning any LLM calls.

This is *augmentation*, not the spine of the corpus — real prompts
remain the primary signal source. Run after the real-prompt pipeline
finishes, then concatenate everything for training.

Usage:
    .venv/bin/python scripts/augment_pairs_synthetic.py \\
        --in data/neural_layout/starter_pairs.jsonl \\
        --out data/neural_layout/synthetic_aug_v1.jsonl \\
        --k 3 --seed 42
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.neural_layout import perturb, schema  # noqa: E402
from studio.neural_layout.schema import TrainingPair  # noqa: E402


def _synthetic_pair_id(orig_id: str, kind: str, seed: int) -> str:
    h = hashlib.sha1()
    h.update(orig_id.encode())
    h.update(b"|")
    h.update(kind.encode())
    h.update(b"|")
    h.update(str(seed).encode())
    return "syn_" + h.hexdigest()[:13]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--k", type=int, default=3,
                    help="Perturbations per source pair (default 3).")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    if not args.inp.exists():
        print(f"ERROR: {args.inp} not found", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    kinds: Counter[str] = Counter()
    n_in = 0
    n_out = 0

    rng = random.Random(args.seed)
    with args.inp.open() as in_fh, args.out.open("a") as out_fh:
        for line in in_fh:
            line = line.strip()
            if not line:
                continue
            pair = TrainingPair.from_dict(json.loads(line))
            n_in += 1
            for k in range(args.k):
                sub_rng = random.Random(
                    args.seed + k * 1000 + n_in,
                )
                kind, perturbed = perturb.random_perturbation(
                    pair.target, sub_rng,
                )
                # Skip if perturbation was a no-op (e.g. only one group)
                if perturbed is pair.target:
                    continue
                meta = dict(pair.metadata or {})
                meta.update({
                    "source": "synthetic_perturb",
                    "perturb_kind": kind,
                    "perturb_seed": args.seed + k * 1000 + n_in,
                    "origin_pair_id": pair.pair_id,
                })
                aug = TrainingPair(
                    pair_id=_synthetic_pair_id(
                        pair.pair_id, kind, args.seed + k,
                    ),
                    prompt=pair.prompt,
                    source=perturbed,
                    target=pair.target,
                    viewport_kind=pair.viewport_kind,
                    math_bucket=pair.math_bucket,
                    metadata=meta,
                )
                out_fh.write(aug.to_json() + "\n")
                kinds[kind] += 1
                n_out += 1

    print(f"In  : {n_in} pairs")
    print(f"Out : {n_out} synthetic pairs ({n_out / max(n_in, 1):.1f}× expansion)")
    print("By perturbation kind:")
    for k, v in kinds.most_common():
        print(f"  {k:18s} {v:5d}")
    print(f"\noutput: {args.out}  ({args.out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
