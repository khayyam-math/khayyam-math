"""Blinded gpt-4o judge restricted to a configurable set of models.

Same scoring rubric as judge_figures.py but lets you pick which
models go in the comparison.  Used to A/B LoRA hyperparameter
variants (qwen_lora vs qwen_lora_v2) without re-judging gpt-4o.

Writes /tmp/sevim_compare/judge_<tag>.csv and prints roll-up.

Usage:
    python scripts/judge_lora_variants.py \
        --models qwen_base qwen_lora qwen_lora_v2 \
        --tag lora_v1_vs_v2
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.judge_figures import judge_one  # noqa: E402

OUT_DIR = Path("/tmp/sevim_compare")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True,
                    help="model tags to judge, e.g. qwen_base qwen_lora qwen_lora_v2")
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    import httpx  # noqa: PLC0415
    client = httpx.Client(http2=False)

    prompt_dirs = sorted(p for p in OUT_DIR.iterdir() if p.is_dir())
    print(f"judging {len(prompt_dirs)} prompts × {len(args.models)} models",
          flush=True)

    rows = []
    t0 = time.monotonic()
    for d in prompt_dirs:
        prompt = (d / "prompt.txt").read_text().strip()
        png_by_model = {m: d / f"{m}.png" for m in args.models}
        avail = [m for m, p in png_by_model.items() if p.exists()]
        print(f"[{d.name[:40]}] {len(avail)} figures: {avail}", flush=True)
        try:
            judged = judge_one(prompt, png_by_model, client, api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {type(exc).__name__}: {exc}", flush=True)
            judged = {m: {"visual_clarity": 0, "math_correctness": 0,
                          "completeness": 0, "rationale": f"error: {exc}"}
                      for m in args.models}
        for m in args.models:
            j = judged.get(m, {"visual_clarity": 0, "math_correctness": 0,
                               "completeness": 0,
                               "rationale": "no figure produced"})
            rows.append({
                "prompt_dir": d.name, "prompt": prompt, "model": m,
                "visual_clarity": j["visual_clarity"],
                "math_correctness": j["math_correctness"],
                "completeness": j["completeness"],
                "total": (j["visual_clarity"] + j["math_correctness"]
                          + j["completeness"]),
                "rationale": j["rationale"],
            })
        time.sleep(0.5)
    client.close()
    elapsed = time.monotonic() - t0

    csv_path = OUT_DIR / f"judge_{args.tag}.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {csv_path}  ({elapsed:.0f}s)", flush=True)

    # Roll-up
    print("\n=== mean scores (out of 30) ===")
    by_prompt: dict[str, dict[str, dict]] = {}
    summary: dict[str, dict] = {m: {"vc": 0, "mc": 0, "c": 0, "n": 0,
                                    "wins": 0, "produced": 0}
                                for m in args.models}
    for r in rows:
        m = r["model"]
        s = summary[m]
        s["vc"] += r["visual_clarity"]
        s["mc"] += r["math_correctness"]
        s["c"] += r["completeness"]
        s["n"] += 1
        if r["total"] > 0:
            s["produced"] += 1
        by_prompt.setdefault(r["prompt_dir"], {})[m] = r
    for d, per in by_prompt.items():
        best = max(per[m]["total"] for m in args.models)
        if best == 0:
            continue
        for m in args.models:
            if per[m]["total"] == best:
                summary[m]["wins"] += 1
    n = len(by_prompt)
    for m in args.models:
        s = summary[m]
        if s["n"] == 0:
            continue
        print(f"  {m:<14}  total={(s['vc']+s['mc']+s['c'])/s['n']:.1f}/30  "
              f"  produced={s['produced']}/{n}  top={s['wins']}/{n}")

    print("\n=== per-prompt totals ===")
    header = "  " + "  ".join(f"{m[:14]:>14}" for m in args.models)
    print(f"  prompt{' '*40}{header}")
    for d in sorted(by_prompt):
        per = by_prompt[d]
        cells = "  ".join(f"{per[m]['total']:>14}" for m in args.models)
        print(f"  {d[:45]:<45}{cells}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
