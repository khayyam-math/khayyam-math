"""Extract `mode=clean` rows from `teacher_v6_mini.jsonl` and emit
them as `TrainingPair` records where source == target.

These records are NOT direct training pairs (source==target gives no
correction signal). They are seed records to be passed to
`scripts/augment_pairs_synthetic.py` which will perturb the TARGET
to create true broken→fixed pairs.

The 2350 clean accepted SVGs we already have are higher quality than
the 1039 corrected ones (the express loop's accept criterion is
strict). Perturbing them 8× gives ~18K free, high-quality pairs.

Usage:
    .venv/bin/python scripts/extract_clean_targets_for_perturbation.py \\
        --in data/distill/teacher_v6_mini.jsonl \\
        --out data/neural_layout/clean_seeds.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.neural_layout import exporter, schema  # noqa: E402
from studio.neural_layout.svg_to_graph import parse_svg  # noqa: E402
from studio.neural_layout.schema import (  # noqa: E402
    MATH_BUCKETS, TrainingPair, classify_math_bucket,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    if args.out.exists():
        args.out.unlink()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    buckets: Counter[str] = Counter()
    n_in = 0
    n_out = 0

    with args.inp.open() as in_fh, args.out.open("a") as out_fh:
        for line in in_fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            n_in += 1
            meta = row.get("meta") or {}
            if meta.get("mode") != "clean":
                continue
            msgs = row.get("messages") or []
            if len(msgs) < 3:
                continue
            try:
                asst = json.loads(msgs[2].get("content", ""))
            except (json.JSONDecodeError, TypeError):
                continue
            svg = asst.get("svg") or ""
            if not svg:
                continue
            prompt = meta.get("prompt") or msgs[1].get("content", "")
            res = parse_svg(svg)
            if not res.graph.nodes:
                continue
            bucket = classify_math_bucket(prompt)
            if bucket not in MATH_BUCKETS:
                bucket = "other"
            h = hashlib.sha1()
            h.update(prompt.encode("utf-8"))
            h.update(b"|seed|")
            h.update(svg.encode("utf-8"))
            pid = "seed_" + h.hexdigest()[:12]
            viewport = exporter._viewport_from_canvas(
                res.graph.canvas_w,
            )
            pair = TrainingPair(
                pair_id=pid,
                prompt=prompt,
                source=res.graph,  # same as target — augmenter
                target=res.graph,  # will perturb the target.
                viewport_kind=viewport,
                math_bucket=bucket,
                metadata={"source": "teacher_v6_mini_clean_seed"},
            )
            out_fh.write(pair.to_json() + "\n")
            buckets[bucket] += 1
            n_out += 1

    print(f"Read   : {n_in} rows")
    print(f"Seeds  : {n_out} clean-target seed pairs")
    print("By bucket:")
    for k, v in buckets.most_common():
        print(f"  {k:24s} {v:5d}")
    print(f"\noutput: {args.out}  ({args.out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
