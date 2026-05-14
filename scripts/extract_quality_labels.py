"""Extract (SceneGraph, label) records for the layout-quality scorer.

Label = 1 (PASS) for any SVG that the express loop accepted.
Label = 0 (FAIL) for SVGs that triggered a retry (the `bad_svg` in
`mode=corrected` rows of teacher_v6_mini.jsonl).

Output JSONL (one record per line):
    {
        "label": 0 or 1,
        "prompt": str,
        "math_bucket": str,
        "scene_graph": {...},   # serialised SceneGraph
        "source": "teacher_v6_mini_{clean|corrected_bad|corrected_good}",
    }

Usage:
    .venv/bin/python scripts/extract_quality_labels.py \\
        --in data/distill/teacher_v6_mini.jsonl \\
        --out data/neural_layout/quality_labels.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.neural_layout.schema import (  # noqa: E402
    MATH_BUCKETS, classify_math_bucket,
)
from studio.neural_layout.svg_to_graph import parse_svg  # noqa: E402


def _record(svg: str, *, label: int, prompt: str, source: str) -> dict | None:
    res = parse_svg(svg)
    if not res.graph.nodes:
        return None
    bucket = classify_math_bucket(prompt)
    if bucket not in MATH_BUCKETS:
        bucket = "other"
    return {
        "label": label,
        "prompt": prompt,
        "math_bucket": bucket,
        "source": source,
        "scene_graph": res.graph.to_dict(),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    if args.out.exists():
        args.out.unlink()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    n_in = 0
    labels: Counter[int] = Counter()
    buckets: Counter[str] = Counter()

    with args.inp.open() as in_fh, args.out.open("a") as out_fh:
        for line in in_fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            n_in += 1
            meta = row.get("meta") or {}
            mode = meta.get("mode")
            msgs = row.get("messages") or []
            prompt = meta.get("prompt") or (msgs[1]["content"] if len(msgs) > 1 else "")
            if mode == "clean" and len(msgs) >= 3:
                try:
                    svg = json.loads(msgs[2]["content"])["svg"]
                except Exception:
                    continue
                rec = _record(svg, label=1, prompt=prompt,
                              source="teacher_v6_mini_clean")
                if rec:
                    out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    labels[1] += 1
                    buckets[rec["math_bucket"]] += 1
            elif mode == "corrected" and len(msgs) >= 5:
                try:
                    bad = json.loads(msgs[2]["content"])["svg"]
                    good = json.loads(msgs[4]["content"])["svg"]
                except Exception:
                    continue
                rec_bad = _record(bad, label=0, prompt=prompt,
                                  source="teacher_v6_mini_corrected_bad")
                rec_good = _record(good, label=1, prompt=prompt,
                                   source="teacher_v6_mini_corrected_good")
                if rec_bad:
                    out_fh.write(json.dumps(rec_bad, ensure_ascii=False) + "\n")
                    labels[0] += 1
                    buckets[rec_bad["math_bucket"]] += 1
                if rec_good:
                    out_fh.write(json.dumps(rec_good, ensure_ascii=False) + "\n")
                    labels[1] += 1
                    buckets[rec_good["math_bucket"]] += 1

    print(f"Read   : {n_in} rows")
    print(f"Labels : PASS={labels[1]} FAIL={labels[0]}")
    print(f"Buckets: {dict(buckets.most_common())}")
    print(f"\noutput: {args.out}  ({args.out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
