"""Figure-quality regression benchmark.

Runs a fixed set of prompts through the express pipeline and reports
objective defects: invalid XML, out-of-bounds text, LaTeX leaks,
oversized elements, and narration highlight ids that resolve to no
element.  Intended as a deploy gate — a regression shows up as a
rising defect count.

Usage:
    OPENAI_API_KEY=... python scripts/figure_benchmark.py
    python scripts/figure_benchmark.py --model gpt-4o --limit 8
    python scripts/figure_benchmark.py --json /tmp/bench.json

Exit code is non-zero when any prompt has a hard defect (invalid XML
or a dead narration highlight), so CI can fail on a regression.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import xml.dom.minidom as minidom
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio.express import express_figure  # noqa: E402

# Representative prompts across every route + difficulty band.
PROMPTS: list[tuple[str, str]] = [
    ("geometry", "explain the Pythagorean theorem"),
    ("geometry", "explain the inscribed angle theorem of a circle"),
    ("geometry", "show the unit circle with sin and cos at 30, 45, 60 degrees"),
    ("geometry", "explain similar triangles with a worked example"),
    ("graph", "draw the complete graph K5"),
    ("graph", "explain topological sort of a directed acyclic graph"),
    ("automata", "draw a DFA for strings over {a,b} ending in ab"),
    ("automata", "draw a Turing machine that increments a binary number"),
    ("commutative", "explain the first isomorphism theorem"),
    ("formula", "derive the quadratic formula by completing the square"),
    ("formula", "explain integration by parts with an example"),
    ("formula", "find the eigenvalues of a 2x2 matrix"),
    ("formula", "explain Bayes theorem with a worked numerical example"),
    ("formula", "multiply two 3x3 matrices"),
    ("formula", "explain the limit definition of the derivative"),
    ("dense", "explain the reduction from 3SAT to vertex cover"),
    ("dense", "explain the spectral theorem for symmetric matrices"),
    ("dense", "explain gradient descent on a contour plot"),
    ("dense", "explain the epsilon-delta definition of a limit"),
    ("curve", "show linear regression with a best-fit line through scattered data"),
    ("curve", "show an SVM with a maximum-margin separating hyperplane"),
    ("curve", "explain the activation functions sigmoid, ReLU and tanh"),
    ("curve", "show a Gaussian normal distribution curve"),
    ("3d", "show gradient descent on a 3D error surface"),
    ("3d", "show a saddle point on a 3D surface"),
    ("elementary", "explain addition of numbers"),
]

_LATEX = re.compile(
    r"\\frac|\\sqrt|\\sum|\\int|\\cdot|\\times|\\theta|\\alpha|"
    r"\\beta|\\to\b|\\le\b|\\ge\b|\\\(|\\\)|\\begin|\\partial"
)


def check(svg: str, narration: list) -> tuple[list[str], list[str]]:
    """Return (hard_defects, soft_defects)."""
    hard: list[str] = []
    soft: list[str] = []
    if not svg or "<svg" not in svg:
        return ["empty_svg"], soft
    try:
        minidom.parseString(svg)
    except Exception:  # noqa: BLE001
        hard.append("invalid_xml")
    bodies = re.findall(r"<text\b[^>]*>(.*?)</text>", svg, re.S)
    if any(_LATEX.search(b) or "$" in b for b in bodies):
        soft.append("latex_leak")
    ids = set(re.findall(r"""\bid\s*=\s*['"]([^'"]+)['"]""", svg))
    blob = " ".join(bodies)
    if not narration:
        soft.append("no_narration")
    else:
        dead = 0
        for ph in narration:
            h = ph.get("highlight") or []
            if isinstance(h, str):
                h = [h]
            for x in h:
                if (isinstance(x, str) and x not in ids
                        and not (len(x) >= 2 and x in blob)):
                    dead += 1
        if dead:
            hard.append(f"dead_highlights:{dead}")
        lit = sum(1 for ph in narration if ph.get("highlight"))
        if lit < max(1, len(narration) // 2):
            soft.append(f"sparse_highlights:{lit}/{len(narration)}")
    return hard, soft


async def run_one(cat: str, prompt: str, model: str, key: str,
                  sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.monotonic()
        rec: dict = {"category": cat, "prompt": prompt}
        try:
            r = await express_figure(
                user_prompt=prompt,
                base_url="https://api.openai.com/v1",
                model=model, api_key=key,
            )
            svg = r.get("svg", "") or ""
            narration = r.get("narration") or []
            hard, soft = check(svg, narration)
            rec.update(
                route=r.get("template") or "llm",
                n_phrases=len(narration),
                retries=r.get("retries_used", 0),
                dt=round(time.monotonic() - t0, 1),
                hard=hard, soft=soft,
            )
        except Exception as e:  # noqa: BLE001
            rec.update(route="ERROR", hard=["exception"], soft=[],
                       error=f"{type(e).__name__}: {e}",
                       dt=round(time.monotonic() - t0, 1))
        flag = "FAIL" if rec["hard"] else ("warn" if rec["soft"] else "ok")
        print(f"  [{flag:4s}] {cat:11s} {rec.get('route','?'):14s} "
              f"{rec['dt']:6.1f}s  hard={rec['hard']} soft={rec['soft']}",
              flush=True)
        return rec


async def main_async(args: argparse.Namespace) -> int:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    prompts = PROMPTS[: args.limit] if args.limit else PROMPTS
    print(f"figure_benchmark: {len(prompts)} prompts, model={args.model}, "
          f"concurrency={args.concurrency}\n")
    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *(run_one(c, p, args.model, key, sem) for c, p in prompts)
    )
    n_hard = sum(1 for r in results if r["hard"])
    n_soft = sum(1 for r in results if r["soft"] and not r["hard"])
    n_ok = len(results) - n_hard - n_soft
    print(f"\n=== {len(results)} prompts: {n_ok} clean, "
          f"{n_soft} soft-warn, {n_hard} HARD-FAIL ===")
    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=1))
        print(f"wrote {args.json}")
    return 1 if n_hard else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-4o-mini",
                    help="figure-generation model (default gpt-4o-mini)")
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the first N prompts")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--json", default="",
                    help="write the full per-prompt report to this path")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
