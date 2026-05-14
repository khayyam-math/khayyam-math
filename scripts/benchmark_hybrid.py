"""A/B benchmark: no-op vs CP-SAT-only vs model-only vs hybrid.

For every (broken, fixed) pair in a sample, render four candidate
"corrected" SVGs and compute layout-quality metrics on each:

1. **no-op**          — return the broken SVG unchanged.
2. **planner-only**   — `studio.layout_planner.plan_layout`.
3. **model-only**     — LayoutDM repair without CP-SAT.
4. **hybrid**         — LayoutDM → CP-SAT.

Metrics (per output, on the rendered SVG):
- # of overlapping text bboxes
- # of out-of-canvas elements
- distance to the ground-truth target's bboxes (lower is better)

Reports per-mode means and a "wins" tally — how many pairs each
mode wins on each metric.

Usage:
    .venv/bin/python scripts/benchmark_hybrid.py \\
        --ckpt runs/layoutdm_real_v1/best.pt \\
        --pairs data/neural_layout/corpus_v1.jsonl \\
        --n 50
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

import torch

from studio.neural_layout.hybrid import (  # noqa: E402
    correct_layout_hybrid, correct_layout_planner_only,
)
from studio.neural_layout.inference_layoutdm import (  # noqa: E402
    correct_scene_graph, load_model_layoutdm,
)
from studio.neural_layout.schema import TrainingPair  # noqa: E402
from studio.neural_layout.svg_to_graph import parse_svg  # noqa: E402


def _scene_graph_to_svg_for_compare(graph, original_svg):
    """For 'model-only' mode we don't have a clean rewrite of the
    full SVG back from the SceneGraph; we approximate by computing
    the metric on the predicted bboxes directly (skipping CP-SAT
    enforcement). Returns None to signal 'no SVG, score from graph'."""
    return None


def overlap_pair_count(nodes, threshold_area=10.0):
    n = len(nodes)
    if n < 2:
        return 0
    count = 0
    for i in range(n):
        ax, ay, aw, ah = nodes[i].bbox
        for j in range(i + 1, n):
            bx, by, bw, bh = nodes[j].bbox
            dx = max(0, min(ax + aw, bx + bw) - max(ax, bx))
            dy = max(0, min(ay + ah, by + bh) - max(ay, by))
            if dx * dy > threshold_area:
                count += 1
    return count


def oob_count(nodes, vw, vh, margin=5.0):
    return sum(
        1 for n in nodes
        if n.bbox[0] < -margin or n.bbox[1] < -margin
        or n.bbox[0] + n.bbox[2] > vw + margin
        or n.bbox[1] + n.bbox[3] > vh + margin
    )


def per_pair_dist_to_target(nodes, target_nodes, vw, vh):
    """Mean per-node centre distance, normalised by canvas size."""
    target_by_id = {n.id: n for n in target_nodes}
    dists = []
    for n in nodes:
        if n.id not in target_by_id:
            continue
        t = target_by_id[n.id]
        ncx = n.bbox[0] + n.bbox[2] / 2
        ncy = n.bbox[1] + n.bbox[3] / 2
        tcx = t.bbox[0] + t.bbox[2] / 2
        tcy = t.bbox[1] + t.bbox[3] / 2
        dists.append(
            ((ncx - tcx) ** 2 + (ncy - tcy) ** 2) ** 0.5 / max(vw, vh)
        )
    return sum(dists) / max(1, len(dists))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ckpt", type=Path, required=True)
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mask-frac", type=float, default=0.5)
    ap.add_argument("--n-steps", type=int, default=12)
    ap.add_argument("--device", default="cpu",
                    help="Use cpu — CP-SAT is the bottleneck anyway")
    args = ap.parse_args(argv)

    print(f"loading model {args.ckpt} on {args.device}", flush=True)
    model = load_model_layoutdm(args.ckpt, device=args.device)
    print(f"model params: {sum(p.numel() for p in model.parameters()):,}",
          flush=True)

    # Pick `args.n` pairs that have a reconstructable source SVG.
    # The TrainingPair stores SceneGraphs; we have to round-trip
    # through SVG. For broken-source we use the bbox info directly.
    # For now we read pairs with `metadata.source` indicating a real
    # repair, where we have a parseable original SVG embedded in
    # the meta. If not available, we synthesise a plausible SVG from
    # the SceneGraph for the planner to operate on.
    rng = random.Random(args.seed)
    print(f"loading pairs from {args.pairs}", flush=True)
    all_pairs: list[TrainingPair] = []
    with args.pairs.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                all_pairs.append(TrainingPair.from_dict(json.loads(line)))
            except Exception:
                continue
    print(f"  loaded {len(all_pairs)} pairs", flush=True)
    rng.shuffle(all_pairs)
    sample = all_pairs[: args.n]

    # We can't easily round-trip SceneGraph→SVG for the planner. So
    # for this benchmark, we measure on the SceneGraph DIRECTLY,
    # both for source (no-op) and for model-only inference. For the
    # planner+hybrid paths we'd need real SVG strings; for that we'd
    # require keeping the source_svg around — which we don't store
    # in the corpus.
    #
    # Practical compromise: benchmark the MODEL'S contribution by
    # comparing source vs model-only on SceneGraph metrics; document
    # that planner-only and hybrid require a full-pipeline test with
    # raw SVGs from the express loop (Phase D integration test).

    metrics = defaultdict(lambda: defaultdict(list))
    t0 = time.monotonic()
    for i, pair in enumerate(sample):
        src = pair.source
        tgt = pair.target
        if len(src.nodes) < 3:
            continue
        vw, vh = src.canvas_w, src.canvas_h
        # 1) no-op = source
        d_src = per_pair_dist_to_target(src.nodes, tgt.nodes, vw, vh)
        o_src = overlap_pair_count(src.nodes)
        b_src = oob_count(src.nodes, vw, vh)
        metrics["no_op"]["dist"].append(d_src)
        metrics["no_op"]["ovlp"].append(o_src)
        metrics["no_op"]["oob"].append(b_src)
        # 2) model-only — apply LayoutDM denoise on the source graph
        try:
            pred = correct_scene_graph(
                model, src,
                mask_frac=args.mask_frac, n_steps=args.n_steps,
                temperature=0.5, device=args.device, seed=42 + i,
            )
            d_pred = per_pair_dist_to_target(pred.nodes, tgt.nodes, vw, vh)
            o_pred = overlap_pair_count(pred.nodes)
            b_pred = oob_count(pred.nodes, vw, vh)
            metrics["model_only"]["dist"].append(d_pred)
            metrics["model_only"]["ovlp"].append(o_pred)
            metrics["model_only"]["oob"].append(b_pred)
        except Exception as exc:
            print(f"  model_only failed on pair {i}: {exc}", flush=True)
        if (i + 1) % 10 == 0:
            elapsed = time.monotonic() - t0
            print(f"  [{i+1}/{len(sample)}]  {elapsed:.1f}s",
                  flush=True)
    elapsed = time.monotonic() - t0

    print()
    print(f"=== benchmark ({len(sample)} pairs, {elapsed:.1f}s) ===")
    print()
    print(f"{'mode':12s}  {'dist→target':>11s}  "
          f"{'overlap_pairs':>13s}  {'oob_nodes':>9s}")
    for mode in ("no_op", "model_only"):
        if not metrics[mode]["dist"]:
            continue
        d = sum(metrics[mode]["dist"]) / len(metrics[mode]["dist"])
        o = sum(metrics[mode]["ovlp"]) / len(metrics[mode]["ovlp"])
        b = sum(metrics[mode]["oob"]) / len(metrics[mode]["oob"])
        print(f"{mode:12s}  {d:>11.4f}  {o:>13.1f}  {b:>9.1f}")

    print()
    if metrics["model_only"]["dist"]:
        wins = 0
        n = min(
            len(metrics["no_op"]["dist"]),
            len(metrics["model_only"]["dist"]),
        )
        for i in range(n):
            if metrics["model_only"]["dist"][i] < metrics["no_op"]["dist"][i]:
                wins += 1
        print(f"model beats no_op on dist for {wins}/{n} pairs "
              f"({100*wins/n:.1f}%)")
        wins = 0
        for i in range(n):
            if metrics["model_only"]["ovlp"][i] < metrics["no_op"]["ovlp"][i]:
                wins += 1
        print(f"model beats no_op on overlap for {wins}/{n} pairs "
              f"({100*wins/n:.1f}%)")
        wins = 0
        for i in range(n):
            if metrics["model_only"]["oob"][i] <= metrics["no_op"]["oob"][i]:
                wins += 1
        print(f"model ≤ no_op on oob for {wins}/{n} pairs "
              f"({100*wins/n:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
