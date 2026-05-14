"""End-to-end vision-audit validation of the re-ranker.

For each broken-SVG sample, generate three candidate corrections
(planner alone, rerank-planner-only, rerank-full-pool), send each
through gpt-4o vision audit, and compare PASS rates.

This is the definitive test of "does the trained scorer pick
layouts that gpt-4o actually likes more than the planner alone."

Cost: ~$0.005/audit × 3 modes × N pairs. At N=150, ~$2.25.

Usage:
    OPENAI_API_KEY=... \\
    .venv/bin/python scripts/vision_audit_rerank.py \\
        --scorer-ckpt runs/quality_scorer_v1/best.pt \\
        --layoutdm-ckpt runs/layoutdm_real_v1/best.pt \\
        --in data/distill/teacher_v6_mini.jsonl \\
        --n 150 --concurrency 8 \\
        --out runs/vision_audit_rerank.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.express import _vision_review  # noqa: E402
from studio.layout_planner import plan_layout  # noqa: E402
from studio.neural_layout.inference_layoutdm import (  # noqa: E402
    load_model_layoutdm,
)
from studio.neural_layout.rerank import (  # noqa: E402
    load_scorer, rerank,
)


async def _audit_one(
    prompt: str, svg: str, *, base_url: str, model: str, api_key: str,
) -> dict:
    """Returns {"verdict": "PASS"|"FAIL", "n_fixes": int}."""
    critique = await _vision_review(
        user_prompt=prompt,
        svg=svg,
        base_url=base_url,
        model=model,
        api_key=api_key,
        narration=None,
    )
    if critique is None:
        return {"verdict": "PASS", "n_fixes": 0}
    # critique is a formatted string; count the listed fixes.
    n_fixes = max(0, critique.count("\n") - 1)
    return {"verdict": "FAIL", "n_fixes": n_fixes}


def _read_pairs(path: Path, n: int, seed: int):
    out = []
    with path.open() as fh:
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
            except Exception:
                continue
            prompt = (row.get("meta") or {}).get(
                "prompt", msgs[1].get("content", ""),
            )
            out.append((prompt, bad, good))
    random.Random(seed).shuffle(out)
    return out[:n]


async def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scorer-ckpt", type=Path, required=True)
    ap.add_argument("--layoutdm-ckpt", type=Path, required=True)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    # Bootstrap API key from .env via service.secrets if present.
    try:
        from service.secrets import bootstrap as _boot
        _boot()
    except Exception:
        pass
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY missing", file=sys.stderr)
        return 1
    base_url = os.environ.get(
        "SEVIM_VLLM_URL", "https://api.openai.com/v1",
    )
    model = os.environ.get("SEVIM_REVIEW_MODEL", "gpt-4o")
    print(f"reviewer: model={model} url={base_url[:40]}...", flush=True)
    # Force vision mode regardless of env so we actually run gpt-4o on PNG.
    os.environ["SEVIM_REVIEW_MODE"] = "vision"

    print("loading scorer + LayoutDM", flush=True)
    scorer = load_scorer(args.scorer_ckpt, device="cpu")
    layoutdm = load_model_layoutdm(args.layoutdm_ckpt, device="cpu")

    pairs = _read_pairs(args.inp, args.n, args.seed)
    print(f"benchmarking on {len(pairs)} pairs", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    sem = asyncio.Semaphore(args.concurrency)
    write_lock = asyncio.Lock()
    fh = args.out.open("a")

    stats: dict[str, Counter[str]] = {
        m: Counter() for m in (
            "no_op", "planner", "rerank_planner", "rerank_full",
            "ground_truth",
        )
    }
    fixes: dict[str, list[int]] = defaultdict(list)

    async def run_one(idx: int, prompt: str, bad: str, good: str):
        async with sem:
            # Generate 3 corrected SVGs from bad (in addition to no_op).
            try:
                planner_svg = plan_layout(bad, time_limit_s=2.0)
            except Exception:
                planner_svg = bad
            try:
                rp, _ = rerank(
                    bad, scorer=scorer, layoutdm_model=None,
                    n_planner_seeds=3, n_hybrid_seeds=0,
                )
                rerank_planner_svg = rp.svg
            except Exception:
                rerank_planner_svg = planner_svg
            try:
                rf, _ = rerank(
                    bad, scorer=scorer, layoutdm_model=layoutdm,
                    n_planner_seeds=3, n_hybrid_seeds=4,
                )
                rerank_full_svg = rf.svg
            except Exception:
                rerank_full_svg = planner_svg
            modes = {
                "no_op": bad,
                "planner": planner_svg,
                "rerank_planner": rerank_planner_svg,
                "rerank_full": rerank_full_svg,
                "ground_truth": good,
            }
            row = {"idx": idx, "prompt": prompt}
            for mode, svg in modes.items():
                try:
                    r = await _audit_one(
                        prompt, svg,
                        base_url=base_url, model=model, api_key=api_key,
                    )
                except Exception as exc:
                    r = {"verdict": "ERROR", "n_fixes": 0,
                         "error": f"{type(exc).__name__}: {exc}"}
                row[mode] = r
                stats[mode][r["verdict"]] += 1
                if "n_fixes" in r:
                    fixes[mode].append(r["n_fixes"])
            async with write_lock:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
            if (idx + 1) % 10 == 0:
                print(f"  [{idx+1}/{len(pairs)}]", flush=True)

    t0 = time.monotonic()
    await asyncio.gather(*(
        run_one(i, p, b, g) for i, (p, b, g) in enumerate(pairs)
    ))
    fh.close()
    elapsed = time.monotonic() - t0

    print()
    print(f"=== vision-audit benchmark ({len(pairs)} pairs, {elapsed:.1f}s) ===")
    print()
    print(f"{'mode':18s} {'PASS':>5s} {'FAIL':>5s} {'ERROR':>6s}  "
          f"{'pass_rate':>9s}  {'mean_fixes':>10s}")
    n_tot = len(pairs)
    for mode in ("no_op", "planner", "rerank_planner", "rerank_full",
                 "ground_truth"):
        s = stats[mode]
        pass_n = s["PASS"]
        fail_n = s["FAIL"]
        err_n = s["ERROR"]
        rate = pass_n / max(1, pass_n + fail_n)
        mean_f = (sum(fixes[mode]) / max(1, len(fixes[mode])))
        print(f"{mode:18s} {pass_n:>5d} {fail_n:>5d} {err_n:>6d}  "
              f"{rate:>8.1%}  {mean_f:>10.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
