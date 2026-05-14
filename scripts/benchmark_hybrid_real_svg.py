"""Hybrid benchmark on REAL SVG strings (no SceneGraph round-trip).

Reads BAD SVGs from `mode=corrected` rows in teacher_v6_mini.jsonl
(the `bad_svg` payload before each repair). For each, runs four
correction modes:

1. no_op       — original broken SVG
2. planner     — `studio.layout_planner.plan_layout`
3. model_only  — LayoutDM denoise → rewrite top-level positions
4. hybrid      — model rewrite, then CP-SAT enforces

Then measures overlap-pair-count and OOB on the resulting SVGs
(via the parser → SceneGraph metric pipeline). Reports wins.

Usage:
    .venv/bin/python scripts/benchmark_hybrid_real_svg.py \\
        --ckpt runs/layoutdm_real_v1/best.pt \\
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
from studio.neural_layout.hybrid import (  # noqa: E402
    _rewrite_positions_from_graph, correct_layout_hybrid,
)
from studio.neural_layout.inference_layoutdm import (  # noqa: E402
    correct_scene_graph, load_model_layoutdm,
)
from studio.neural_layout.svg_to_graph import parse_svg  # noqa: E402


def _read_bad_svgs(path: Path, n: int, seed: int = 0):
    """Yield up to n (prompt, bad_svg, good_svg) triples."""
    out = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            meta = row.get("meta") or {}
            if meta.get("mode") != "corrected":
                continue
            msgs = row.get("messages") or []
            if len(msgs) < 5:
                continue
            try:
                bad = json.loads(msgs[2]["content"])["svg"]
                good = json.loads(msgs[4]["content"])["svg"]
            except Exception:
                continue
            out.append((meta.get("prompt", ""), bad, good))
    random.Random(seed).shuffle(out)
    return out[:n]


def metrics_on_svg(svg_text: str):
    """Parse + compute overlap/OOB metrics on a rendered SVG."""
    res = parse_svg(svg_text)
    g = res.graph
    if not g.nodes:
        return None
    vw, vh = g.canvas_w, g.canvas_h
    margin = 5.0
    # overlap pair count (only top-level groups + text, threshold 10px²)
    nodes = g.nodes
    count = 0
    for i in range(len(nodes)):
        ax, ay, aw, ah = nodes[i].bbox
        for j in range(i + 1, len(nodes)):
            if nodes[i].top_level_group_id == nodes[j].top_level_group_id:
                continue  # ignore intra-group overlaps
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
    return {"overlap": count, "oob": oob, "n_nodes": len(nodes)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ckpt", type=Path, required=True)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--mask-frac", type=float, default=0.5)
    ap.add_argument("--n-steps", type=int, default=12)
    args = ap.parse_args(argv)

    print(f"loading model from {args.ckpt}", flush=True)
    model = load_model_layoutdm(args.ckpt, device=args.device)
    print(f"loading bad SVGs from {args.inp}", flush=True)
    pairs = _read_bad_svgs(args.inp, args.n, args.seed)
    print(f"  got {len(pairs)} candidate pairs", flush=True)

    results = defaultdict(lambda: defaultdict(list))
    t0 = time.monotonic()
    for i, (prompt, bad, good) in enumerate(pairs):
        # mode 1: no_op
        m_no = metrics_on_svg(bad)
        if m_no is None:
            continue
        # mode 2: planner only
        try:
            planner_svg = plan_layout(bad, time_limit_s=2.0)
            m_pl = metrics_on_svg(planner_svg)
        except Exception:
            m_pl = None
        # mode 3: model only (rewrite from LayoutDM, no CP-SAT)
        try:
            source = parse_svg(bad).graph
            suggested = correct_scene_graph(
                model, source,
                mask_frac=args.mask_frac, n_steps=args.n_steps,
                temperature=0.5, device=args.device, seed=42 + i,
            )
            model_svg = _rewrite_positions_from_graph(
                bad, source, suggested,
            )
            m_mo = metrics_on_svg(model_svg)
        except Exception:
            m_mo = None
        # mode 4: hybrid
        try:
            hybrid_svg = correct_layout_hybrid(
                bad, model,
                mask_frac=args.mask_frac, n_steps=args.n_steps,
                device=args.device, seed=42 + i,
                plan_time_limit_s=2.0,
            )
            m_hy = metrics_on_svg(hybrid_svg)
        except Exception:
            m_hy = None
        # mode 5: ground-truth (target) — for reference
        m_gt = metrics_on_svg(good)

        for name, m in [
            ("no_op", m_no), ("planner", m_pl),
            ("model_only", m_mo), ("hybrid", m_hy),
            ("ground_truth", m_gt),
        ]:
            if m is None:
                continue
            results[name]["overlap"].append(m["overlap"])
            results[name]["oob"].append(m["oob"])

        if (i + 1) % 5 == 0:
            print(f"  [{i+1}/{len(pairs)}]  "
                  f"{time.monotonic() - t0:.1f}s",
                  flush=True)
    print()
    elapsed = time.monotonic() - t0
    print(f"=== benchmark ({len(pairs)} pairs, {elapsed:.1f}s) ===")
    print()
    header = f"{'mode':14s} {'n':>4s} {'mean ovlp':>10s} {'mean oob':>10s}"
    print(header)
    for name in ("no_op", "planner", "model_only", "hybrid", "ground_truth"):
        if name not in results:
            continue
        d = results[name]
        n = len(d["overlap"])
        if n == 0:
            continue
        mo = sum(d["overlap"]) / n
        mb = sum(d["oob"]) / n
        print(f"{name:14s} {n:>4d} {mo:>10.1f} {mb:>10.1f}")
    print()
    # Per-pair wins for each method vs no_op
    no_op_o = results["no_op"]["overlap"]
    no_op_b = results["no_op"]["oob"]
    n_total = len(no_op_o)
    for name in ("planner", "model_only", "hybrid"):
        if name not in results:
            continue
        d = results[name]
        n = min(n_total, len(d["overlap"]))
        if n == 0:
            continue
        wins_o = sum(
            1 for i in range(n)
            if d["overlap"][i] < no_op_o[i]
        )
        ties_o = sum(
            1 for i in range(n)
            if d["overlap"][i] == no_op_o[i]
        )
        wins_b = sum(
            1 for i in range(n)
            if d["oob"][i] <= no_op_b[i]
        )
        print(f"  {name:11s}: overlap < no_op in {wins_o:2d}/{n} "
              f"(tie {ties_o:2d}), oob ≤ no_op in {wins_b}/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
