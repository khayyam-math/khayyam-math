"""Re-classify the `math_bucket` field of an existing TrainingPair
JSONL file using the latest `classify_math_bucket` heuristics.

Useful when we expand the bucket vocabulary or refine keyword lists
and want existing data to reflect the new categorisation without
regenerating any SVGs.

Usage:
    .venv/bin/python scripts/rebucket_pairs.py \\
        --in data/neural_layout/starter_pairs.jsonl \\
        --out data/neural_layout/starter_pairs.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.neural_layout.schema import classify_math_bucket  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    if not args.inp.exists():
        print(f"ERROR: {args.inp} not found", file=sys.stderr)
        return 1

    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    moved = 0
    total = 0

    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)

    with args.inp.open() as in_fh, tmp.open("w") as out_fh:
        for line in in_fh:
            line = line.rstrip("\n")
            if not line:
                continue
            row = json.loads(line)
            total += 1
            old = row.get("math_bucket", "other")
            new = classify_math_bucket(row.get("prompt", ""))
            row["math_bucket"] = new
            before[old] += 1
            after[new] += 1
            if old != new:
                moved += 1
            out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    tmp.replace(args.out)
    print(f"Read   : {total}")
    print(f"Moved  : {moved} ({100.0 * moved / total:.1f}%)")
    print("\nBefore -> After:")
    keys = sorted(set(before) | set(after))
    for k in keys:
        b = before.get(k, 0)
        a = after.get(k, 0)
        arrow = "" if b == a else f"  ({a - b:+d})"
        print(f"  {k:24s} {b:5d} -> {a:5d}{arrow}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
