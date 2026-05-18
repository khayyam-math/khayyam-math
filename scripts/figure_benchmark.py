"""Figure-quality regression benchmark.

Runs a fixed set of prompts through the express pipeline and reports
two things:

  * objective DEFECTS — invalid XML, dead narration highlights, LaTeX
    leaks, sparse highlighting (cheap, deterministic, always on);
  * a QUALITY SCORE — with --judge, every rendered figure is shown to
    a gpt-4o vision judge that scores it 1-10 on whether it correctly
    and legibly depicts the prompt.  This is the honest quality
    metric; the defect checks only catch mechanical faults and say
    nothing about whether the figure is actually any good.

Usage:
    OPENAI_API_KEY=... python scripts/figure_benchmark.py
    python scripts/figure_benchmark.py --judge          # quality score
    python scripts/figure_benchmark.py --judge --model gpt-4o
    python scripts/figure_benchmark.py --json /tmp/bench.json

Exit code is non-zero when any prompt has a hard defect, or (with
--judge) when the mean quality score falls below --min-score.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import time
import xml.dom.minidom as minidom
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio.express import express_figure, _svg_to_png  # noqa: E402

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
    # Classes added once routing was widened (#1) — these used to fall
    # to the weak LLM-SVG path; the benchmark must include them so the
    # routing win is actually measured.
    ("timeseries", "explain the autocorrelation function of a time series"),
    ("timeseries", "decompose a time series into trend, seasonality and residual"),
    ("graph", "draw a recursion tree for the merge sort recurrence"),
    ("graph", "explain Dijkstra's algorithm on a 6-node weighted graph"),
    ("curve", "show a Riemann sum converging to a definite integral"),
    ("dense", "draw a phase portrait of a 2D linear dynamical system"),
]

_LATEX = re.compile(
    r"\\frac|\\sqrt|\\sum|\\int|\\cdot|\\times|\\theta|\\alpha|"
    r"\\beta|\\to\b|\\le\b|\\ge\b|\\\(|\\\)|\\begin|\\partial")

JUDGE_SYSTEM = """\
You grade a figure produced by an AI math tutor.  You are given the
user's prompt and the rendered figure (PNG).  Score the figure 1-10
on whether a learner would actually be helped by it:

  9-10  excellent — correct, complete, clean layout, legible.
  7-8   good — correct and usable; minor blemishes only.
  5-6   mediocre — readable but with real flaws (some overlap, a
        missing element, awkward spacing).
  3-4   bad — a serious defect: text overlapping a curve/shape,
        empty placeholder boxes, the main content missing (e.g. a
        "Riemann sum" with no rectangles), a broken/misaligned grid,
        irrelevant decoration covering content.
  1-2   broken — near-empty, unreadable, or wrong figure entirely.

Judge what is DRAWN, not the caption's claims.  Be strict about
overlap and missing content — those are what make a figure useless.

Respond with ONLY JSON:
  {"score": <1-10>, "verdict": "good"|"mediocre"|"bad",
   "defects": ["...", ...]}
where verdict is good for >=7, mediocre for 5-6, bad for <=4.
"""


def check(svg: str, narration: list) -> tuple[list[str], list[str]]:
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


async def judge_figure(prompt: str, svg: str, key: str) -> dict:
    """Vision-grade one figure with gpt-4o.  Returns {score, verdict,
    defects} or {score:0,...} on any failure."""
    import httpx
    try:
        png = await asyncio.to_thread(_svg_to_png, svg, 1100)
        b64 = base64.b64encode(png).decode("ascii")
    except Exception as e:  # noqa: BLE001
        return {"score": 0, "verdict": "bad",
                "defects": [f"render failed: {e}"]}
    payload = {
        "model": "gpt-4o",
        "max_tokens": 400,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": f"User prompt: {prompt}"},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "content-type": "application/json"},
                json=payload)
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        return {"score": int(data.get("score", 0)),
                "verdict": data.get("verdict", "bad"),
                "defects": data.get("defects", [])}
    except Exception as e:  # noqa: BLE001
        return {"score": 0, "verdict": "bad",
                "defects": [f"judge call failed: {e}"]}


async def run_one(cat, prompt, model, key, judge, sem):
    async with sem:
        t0 = time.monotonic()
        rec: dict = {"category": cat, "prompt": prompt}
        try:
            r = await express_figure(
                user_prompt=prompt,
                base_url="https://api.openai.com/v1",
                model=model, api_key=key)
            svg = r.get("svg", "") or ""
            narration = r.get("narration") or []
            hard, soft = check(svg, narration)
            rec.update(route=r.get("template") or "llm",
                       n_phrases=len(narration),
                       dt=round(time.monotonic() - t0, 1),
                       hard=hard, soft=soft)
            if judge:
                j = await judge_figure(prompt, svg, key)
                rec.update(score=j["score"], verdict=j["verdict"],
                           defects=j["defects"])
        except Exception as e:  # noqa: BLE001
            rec.update(route="ERROR", hard=["exception"], soft=[],
                       error=f"{type(e).__name__}: {e}",
                       dt=round(time.monotonic() - t0, 1),
                       score=0, verdict="bad", defects=["exception"])
        flag = "FAIL" if rec.get("hard") else (
            "warn" if rec.get("soft") else "ok")
        extra = (f"  score={rec['score']}/10 {rec['verdict']:8s} "
                 f"{rec.get('defects')}" if judge else "")
        print(f"  [{flag:4s}] {cat:11s} {rec.get('route','?'):14s} "
              f"{rec['dt']:6.1f}s{extra}", flush=True)
        return rec


async def main_async(args) -> int:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    prompts = PROMPTS[: args.limit] if args.limit else PROMPTS
    print(f"figure_benchmark: {len(prompts)} prompts, model={args.model}, "
          f"judge={'on' if args.judge else 'off'}\n")
    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *(run_one(c, p, args.model, key, args.judge, sem)
          for c, p in prompts))
    n_hard = sum(1 for r in results if r.get("hard"))
    print(f"\n=== {len(results)} prompts: {n_hard} with hard defects ===")
    rc = 1 if n_hard else 0
    if args.judge:
        scores = [r["score"] for r in results if r.get("score") is not None]
        mean = sum(scores) / len(scores) if scores else 0.0
        vd = {"good": 0, "mediocre": 0, "bad": 0}
        for r in results:
            vd[r.get("verdict", "bad")] = vd.get(r.get("verdict", "bad"), 0) + 1
        print(f"=== QUALITY: mean {mean:.2f}/10 — "
              f"{vd['good']} good, {vd['mediocre']} mediocre, "
              f"{vd['bad']} bad ===")
        # by route
        routes: dict[str, list[int]] = {}
        for r in results:
            routes.setdefault(r.get("route", "?"), []).append(
                r.get("score", 0))
        for rt, sc in sorted(routes.items()):
            print(f"  {rt:14s}: mean {sum(sc)/len(sc):.1f}/10  (n={len(sc)})")
        if mean < args.min_score:
            rc = 1
    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=1))
        print(f"wrote {args.json}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--judge", action="store_true",
                    help="vision-grade every figure with gpt-4o")
    ap.add_argument("--min-score", type=float, default=6.0,
                    help="fail the run if mean quality is below this")
    ap.add_argument("--json", default="")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
