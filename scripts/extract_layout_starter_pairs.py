"""Extract layout-correction training pairs from
`data/distill/teacher_v6_mini.jsonl`.

The existing corpus was built for OpenAI fine-tuning of gpt-4o-mini.
Its `mode=corrected` rows are 5-message conversations of the form:

    system → user(prompt) → assistant(BAD svg) →
        user(critique) → assistant(GOOD svg)

Those 4 SVGs per row encode exactly the (broken, fixed) signal we
want to teach a layout-correction model. This script parses every
corrected row into a `TrainingPair` and writes them to a JSONL.

Usage:
    .venv/bin/python scripts/extract_layout_starter_pairs.py \\
        --in data/distill/teacher_v6_mini.jsonl \\
        --out data/neural_layout/starter_pairs.jsonl

No network, no LLM calls; runs in seconds.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.neural_layout import exporter  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after this many input rows (debug).")
    args = ap.parse_args(argv)

    if not args.inp.exists():
        print(f"ERROR: input {args.inp} not found", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()  # fresh write — this is an idempotent extract

    buckets: Counter[str] = Counter()
    viewports: Counter[str] = Counter()
    skipped = 0
    written = 0
    n_rows = 0

    with args.inp.open() as fh, args.out.open("a") as out_fh:
        for line in fh:
            n_rows += 1
            if args.limit and n_rows > args.limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            pairs = exporter.pairs_from_teacher_corpus_row(row)
            if not pairs:
                continue
            for p in pairs:
                out_fh.write(p.to_json() + "\n")
                buckets[p.math_bucket] += 1
                viewports[p.viewport_kind] += 1
                written += 1

    print(f"Read    : {n_rows} rows")
    print(f"Written : {written} pairs")
    print(f"Skipped : {skipped} (bad JSON / unparseable SVG)")
    print(f"By bucket:")
    for k, v in buckets.most_common():
        print(f"  {k:24s} {v:5d}")
    print(f"By viewport:")
    for k, v in viewports.most_common():
        print(f"  {k:10s} {v:5d}")
    print(f"\nOutput: {args.out}  ({args.out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
