"""Benchmark the re-ranker on real broken SVGs.

Compares 5 strategies on 30 broken SVGs from teacher_v6_mini.jsonl:

1. no_op                — broken SVG unchanged
2. planner              — CP-SAT alone
3. rerank_planner_only  — generate K planner candidates, scorer picks best
4. rerank_with_layoutdm — full pool including LayoutDM-hybrid candidates
5. ground_truth         — the loop's accepted fixed SVG

For each, compute overlap-pairs + OOB on the resulting SVG.

Usage:
    .venv/bin/python scripts/benchmark_rerank.py \\
        --scorer-ckpt runs/quality_scorer_v1/best.pt \\
        --layoutdm-ckpt runs/layoutdm_real_v1/best.pt \\
        --in data/distill/teacher_v6_mini.jsonl \\
        --n 30
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.layout_planner import plan_layout  # noqa: E402
from studio.neural_layout.inference_layoutdm import (  # noqa: E402
    load_model_layoutdm,
)
from studio.neural_layout.rerank import (  # noqa: E402
    generate_candidates, load_scorer, rerank, score_svg,
)
from studio.neural_layout.svg_to_graph import parse_svg  # noqa: E402


def metrics_on_svg(svg_text: str):
    res = parse_svg(svg_text)
    g = res.graph
    if not g.nodes:
        return None
    vw, vh = g.canvas_w, g.canvas_h
    margin = 5.0
    nodes = g.nodes
    count = 0
    for i in range(len(nodes)):
        ax, ay, aw, ah = nodes[i].bbox
        for j in range(i + 1, len(nodes)):
            if nodes[i].top_level_group_id == nodes[j].top_level_group_id:
                continue
            bx, by, bw, bh = nodes[j].bbox
            dx = max(0, min(ax + aw, bx + bw) - max(ax, bx))
            dy = max(0, min(ay + ah, by + bh) - max(ay, by))
            if dx * dy > 10.0:
                count += 1
    oob = sum(
        1 for n in nodes
        if n.bbox[0] < -margin or n.bbox[1] < -margin
        or n.bbox[0] + n.bbox[2] > vw + margin
        or n.bbox[1] + n.bbox[3] > vh + margin
    )
    return {"overlap": count, "oob": oob}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scorer-ckpt", type=Path, required=True)
    ap.add_argument("--layoutdm-ckpt", type=Path, required=True)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    print("loading scorer + layoutdm", flush=True)
    scorer = load_scorer(args.scorer_ckpt, device="cpu")
    layoutdm = load_model_layoutdm(args.layoutdm_ckpt, device="cpu")

    # Read bad svgs from mode=corrected rows.
    bad_svgs = []
    with args.inp.open() as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if (row.get("meta") or {}).get("mode") != "corrected":
                continue
            msgs = row.get("messages", [])
            if len(msgs) < 5:
                continue
            try:
                bad = json.loads(msgs[2]["content"])["svg"]
                good = json.loads(msgs[4]["content"])["svg"]
                bad_svgs.append((bad, good))
            except Exception:
                continue
    random.Random(args.seed).shuffle(bad_svgs)
    bad_svgs = bad_svgs[: args.n]
    print(f"benchmarking on {len(bad_svgs)} pairs", flush=True)

    results = defaultdict(lambda: defaultdict(list))
    t0 = time.monotonic()
    for i, (bad, good) in enumerate(bad_svgs):
        # no_op
        m = metrics_on_svg(bad)
        if m is None:
            continue
        results["no_op"]["overlap"].append(m["overlap"])
        results["no_op"]["oob"].append(m["oob"])
        # planner alone
        try:
            pl = plan_layout(bad, time_limit_s=2.0)
            m = metrics_on_svg(pl)
            if m:
                results["planner"]["overlap"].append(m["overlap"])
                results["planner"]["oob"].append(m["oob"])
        except Exception:
            pass
        # rerank planner-only (no LayoutDM)
        try:
            best, cands = rerank(
                bad, scorer=scorer, layoutdm_model=None,
                n_planner_seeds=3, n_hybrid_seeds=0,
            )
            m = metrics_on_svg(best.svg)
            if m:
                results["rerank_planner"]["overlap"].append(m["overlap"])
                results["rerank_planner"]["oob"].append(m["oob"])
                results["rerank_planner"]["picked"].append(best.name)
        except Exception as e:
            print(f"  rerank_planner failed at {i}: {e}", flush=True)
        # rerank with LayoutDM
        try:
            best, cands = rerank(
                bad, scorer=scorer, layoutdm_model=layoutdm,
                n_planner_seeds=3, n_hybrid_seeds=4,
            )
            m = metrics_on_svg(best.svg)
            if m:
                results["rerank_full"]["overlap"].append(m["overlap"])
                results["rerank_full"]["oob"].append(m["oob"])
                results["rerank_full"]["picked"].append(best.name)
        except Exception as e:
            print(f"  rerank_full failed at {i}: {e}", flush=True)
        # ground truth
        m = metrics_on_svg(good)
        if m:
            results["ground_truth"]["overlap"].append(m["overlap"])
            results["ground_truth"]["oob"].append(m["oob"])
        if (i + 1) % 5 == 0:
            print(f"  [{i+1}/{len(bad_svgs)}]  "
                  f"{time.monotonic() - t0:.1f}s", flush=True)
    elapsed = time.monotonic() - t0
    print()
    print(f"=== rerank benchmark ({len(bad_svgs)} pairs, {elapsed:.1f}s) ===")
    print()
    print(f"{'mode':22s} {'n':>4s} {'mean ovlp':>10s} {'mean oob':>10s}")
    for name in ("no_op", "planner", "rerank_planner",
                 "rerank_full", "ground_truth"):
        if name not in results:
            continue
        d = results[name]
        n = len(d["overlap"])
        if n == 0:
            continue
        mo = sum(d["overlap"]) / n
        mb = sum(d["oob"]) / n
        print(f"{name:22s} {n:>4d} {mo:>10.1f} {mb:>10.1f}")
    # Which candidate type wins most often in each rerank pool
    print()
    for mode in ("rerank_planner", "rerank_full"):
        if "picked" not in results[mode]:
            continue
        from collections import Counter
        pick_counts = Counter(results[mode]["picked"])
        print(f"  {mode}: picks =", dict(pick_counts.most_common()))
    print()
    # Wins vs no_op
    n_total = len(results["no_op"]["overlap"])
    for name in ("planner", "rerank_planner", "rerank_full"):
        if name not in results:
            continue
        d = results[name]
        n = min(n_total, len(d["overlap"]))
        wins_o = sum(
            1 for i in range(n)
            if d["overlap"][i] < results["no_op"]["overlap"][i]
        )
        ties_o = sum(
            1 for i in range(n)
            if d["overlap"][i] == results["no_op"]["overlap"][i]
        )
        wins_b = sum(
            1 for i in range(n)
            if d["oob"][i] <= results["no_op"]["oob"][i]
        )
        print(f"  {name:18s}: ovlp<no_op {wins_o}/{n} (tie {ties_o}) | "
              f"oob≤no_op {wins_b}/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
