"""Grow the teacher-prompt pool from ~164 seed entries to ~3000 by:

  1. Parametric expansion of seed templates (deterministic).
     E.g. "matrix multiplication of MxN by NxP" with sampled M,N,P.
  2. Asking gpt-4o-mini to propose more distinct prompts at each
     level × branch combo, seeded with the existing list to avoid
     duplication.

Output: `scripts/expanded_prompts.py` containing PROMPTS_V4 list, plus
a deduplicated count.

Cost: ~$0.10 in gpt-4o-mini calls (10-20 calls each generating ~200
prompts).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.teacher_prompts import PROMPTS as SEED_PROMPTS  # noqa: E402


# Parametric templates — `{P}` is a placeholder filled below.
PARAMETRIC: list[tuple[str, list[dict]]] = [
    ("show how the angles of a regular {n}-gon sum to (n-2)π — work the {n}-gon case",
     [{"n": n} for n in [3, 4, 5, 6, 7, 8, 9, 10, 12, 20]]),
    ("matrix multiplication of a {m}x{n} by a {n}x{p} matrix with a worked dot product (Strang style)",
     [{"m": m, "n": n, "p": p}
      for (m, n, p) in [(2,2,2), (2,3,2), (3,2,3), (3,3,3), (3,4,2),
                        (3,5,4), (4,3,5), (4,4,4), (5,2,5), (3,5,3)]]),
    ("Riemann sum approximating ∫₀^{b} {f}(x) dx with {n} rectangles",
     [{"b": b, "f": f, "n": n}
      for b in [1, 2, 3, 4]
      for f in ["x²", "sin(x)", "1/(1+x²)", "e^(-x²)"]
      for n in [4, 8, 16]]),
    ("Taylor series of {f} around 0, first {k} terms, plotted vs the true function",
     [{"f": f, "k": k}
      for f in ["e^x", "sin(x)", "cos(x)", "ln(1+x)", "1/(1-x)", "tan(x)"]
      for k in [3, 5, 7]]),
    ("derivative of {f} at x = {a} shown as the slope of the tangent line",
     [{"f": f, "a": a}
      for f in ["sin(x)", "cos(x)", "x²", "x³", "1/x", "ln(x)", "e^x"]
      for a in ["π/4", "π/3", "π/2", "1", "2", "0.5"]]),
    ("Euclidean algorithm to find gcd({a}, {b}) step by step",
     [{"a": a, "b": b}
      for (a, b) in [(252, 105), (180, 48), (1071, 462), (888, 24),
                     (525, 165), (462, 198), (96, 36), (1024, 768)]]),
    ("Pascal's triangle, first {n} rows, with the binomial-coefficient labels",
     [{"n": n} for n in [4, 5, 6, 7, 8, 9, 10, 12]]),
    ("modular arithmetic: {a} mod {n} = {r}  (visualised on a number-line wrap)",
     [{"a": a, "n": n, "r": a % n}
      for (a, n) in [(17, 5), (23, 7), (100, 13), (47, 9), (35, 6),
                     (88, 11), (123, 4), (256, 17)]]),
    ("Venn diagram for {expr} with concrete elements",
     [{"expr": e} for e in
      ["A ∪ B", "A ∩ B", "A ∪ B ∩ C", "(A ∪ B)'", "A △ B (symmetric difference)",
       "A \\ B", "(A ∩ B) ∪ C", "A ⊆ B"]]),
    ("graph y = {f} from x = {a} to x = {b}",
     [{"f": f, "a": a, "b": b}
      for f in ["x²", "x³ - 3x", "sin(x)", "cos(x) + sin(2x)", "e^(-x²)",
                "1/(1+x²)", "|x|", "ln(x)", "tan(x)"]
      for (a, b) in [("-2", "2"), ("-π", "π"), ("0", "4")]]),
    ("BFS traversal on a graph with {n} nodes, starting from node 0 (CLRS pseudocode + figure)",
     [{"n": n} for n in [5, 6, 7, 8]]),
    ("DFS recursion tree on a graph with {n} nodes (CLRS)",
     [{"n": n} for n in [5, 6, 7, 8]]),
    ("Dijkstra's shortest path on a {n}-node weighted graph (CLRS)",
     [{"n": n} for n in [4, 5, 6, 7]]),
    ("binary-search-tree insertion of {seq} step by step",
     [{"seq": s} for s in
      ["5, 3, 8, 1, 4", "10, 5, 15, 3, 7, 12, 18", "8, 4, 12, 2, 6, 10, 14",
       "7, 3, 11, 1, 5, 9, 13", "20, 10, 30, 5, 15"]]),
    ("draw the unit circle with sin θ and cos θ labelled at θ = {th}",
     [{"th": t} for t in ["π/6", "π/4", "π/3", "π/2", "2π/3", "3π/4",
                          "5π/6", "π", "7π/6", "5π/4", "4π/3", "3π/2"]]),
    ("convex hull of the points {pts}",
     [{"pts": p} for p in
      ["(1,1), (3,2), (2,4), (5,3), (4,1)",
       "(0,0), (1,3), (2,1), (3,4), (4,2), (5,0)",
       "(2,3), (1,1), (4,2), (3,5), (5,4), (2,2)"]]),
    ("histogram of {n} samples drawn from {dist}",
     [{"n": n, "dist": d}
      for n in [50, 100, 200]
      for d in ["a fair die", "a fair coin × 20", "Normal(0, 1)",
                "Exponential(1)", "Poisson(3)"]]),
    ("Bayes' theorem with prior {p}, sensitivity {s}, specificity {sp}",
     [{"p": p, "s": s, "sp": sp}
      for (p, s, sp) in [("0.01", "0.95", "0.95"), ("0.001", "0.99", "0.99"),
                         ("0.10", "0.90", "0.80"), ("0.05", "0.99", "0.95"),
                         ("0.20", "0.85", "0.95")]]),
    ("free-body diagram of a {obj} on a {surface}",
     [{"obj": o, "surface": s}
      for o in ["block", "ball", "crate"]
      for s in ["frictionless inclined plane", "rough inclined plane",
                "horizontal surface with friction", "incline at 30°",
                "incline at 45°"]]),
]


def _instantiate_parametric() -> list[str]:
    """Concretise every parametric template into one prompt per param dict."""
    out: list[str] = []
    for template, params in PARAMETRIC:
        for p in params:
            try:
                out.append(template.format(**p))
            except KeyError:
                continue
    return out


# ─────────────────────────────────────────────────────────────────────
# gpt-4o-mini-as-prompt-generator
# ─────────────────────────────────────────────────────────────────────

LEVELS = [
    ("middle school (grades 7-9)",
     "pre-algebra, basic geometry, intro statistics, intro probability"),
    ("high school (grades 10-12)",
     "trigonometry, calculus 1, intro linear algebra, vectors, complex numbers"),
    ("undergraduate STEM",
     "multivariable calculus, full linear algebra, real analysis, abstract algebra, "
     "intro topology, ODE, intro probability theory, complexity theory, "
     "discrete maths, intro statistics"),
    ("graduate maths",
     "measure theory, functional analysis, algebraic topology, group / ring / field theory, "
     "differential geometry, PDEs, advanced probability, information theory"),
]


async def _generate_prompts_for_level(
    level: str, topics: str, seeds_sample: list[str], n: int,
    api_key: str, base_url: str, model: str, client: httpx.AsyncClient,
) -> list[str]:
    """Ask gpt-4o-mini to propose `n` distinct prompts at this level."""
    seeds_block = "\n".join(f"  - {s}" for s in seeds_sample)
    user_text = (
        f"Generate {n} DISTINCT user-questions for a 'live diagram tutor' "
        f"covering topics at the {level} level: {topics}.\n\n"
        f"Each question must:\n"
        f"  • request a SINGLE figure that can be drawn on a 2D canvas;\n"
        f"  • be specific (mention concrete numbers, named theorems, or "
        f"    specific objects rather than vague concepts);\n"
        f"  • reference a textbook treatment when natural — e.g. "
        f"    '(Strang style)', '(Spivak / Apostol style)', "
        f"    '(Elements I.NN)', '(CLRS pseudocode + figure)';\n"
        f"  • be one sentence, ≤ 30 words.\n\n"
        f"DO NOT duplicate or paraphrase any of these existing prompts:\n"
        f"{seeds_block}\n\n"
        f"Return JSON {{\"prompts\": [...]}} with exactly {n} new prompts."
    )
    payload = {
        "model": model,
        "max_tokens": 6000,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content":
                "You write diverse, high-quality math/CS visualization "
                "prompts in the style of a teaching-focused tutor."},
            {"role": "user", "content": user_text},
        ],
    }
    try:
        r = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload, timeout=120,
        )
        if r.status_code != 200:
            print(f"  ! gpt-4o-mini {r.status_code}: {r.text[:200]}",
                  flush=True)
            return []
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content).get("prompts", [])
    except Exception as exc:  # noqa: BLE001
        print(f"  ! gen error: {type(exc).__name__}: {exc}", flush=True)
        return []


def _dedupe(prompts: list[str]) -> list[str]:
    """Crude prefix-overlap dedupe so we don't ship 50 phrasings of the
    same question."""
    seen: set[str] = set()
    out: list[str] = []
    for p in prompts:
        key = " ".join(p.lower().split()[:8])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True,
                    help="Output Python file with PROMPTS_V4 = [...]")
    ap.add_argument("--per-level", type=int, default=200,
                    help="LLM-generated prompts per level (4 levels)")
    ap.add_argument("--rounds", type=int, default=3,
                    help="Generation rounds per level — boost diversity")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--base-url",
                    default=os.environ.get("SEVIM_VLLM_URL",
                                           "https://api.openai.com/v1"))
    args = ap.parse_args(argv)

    from service.secrets import bootstrap as _boot
    _boot()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    parametric = _instantiate_parametric()
    pool = list(SEED_PROMPTS) + parametric
    print(f"seed: {len(SEED_PROMPTS)}  parametric: {len(parametric)}  "
          f"running total: {len(pool)}", flush=True)

    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(4)

        async def gen_round(level, topics, round_idx):
            async with sem:
                seeds_sample = random.sample(pool, min(15, len(pool)))
                prompts = await _generate_prompts_for_level(
                    level, topics, seeds_sample,
                    args.per_level, api_key, args.base_url, args.model, client)
                print(f"  [{level} r{round_idx}] +{len(prompts)} prompts",
                      flush=True)
                return prompts

        tasks = []
        for r_idx in range(args.rounds):
            for level, topics in LEVELS:
                tasks.append(gen_round(level, topics, r_idx))
        results = await asyncio.gather(*tasks)

    for batch in results:
        pool.extend(batch)

    pool = _dedupe(pool)
    print(f"\nfinal pool size after dedupe: {len(pool)}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as fh:
        fh.write('"""Expanded teacher-prompt pool for v4 distillation.\n\n'
                 f'  • {len(SEED_PROMPTS)} hand-curated seed prompts\n'
                 f'  • {len(parametric)} parametric instantiations\n'
                 f'  • ~{sum(len(b) for b in results)} gpt-4o-mini-generated\n'
                 f'  → {len(pool)} after dedupe\n"""\n\n'
                 'from __future__ import annotations\n\n'
                 'PROMPTS_V4: list[str] = [\n')
        for p in pool:
            esc = p.replace("\\", "\\\\").replace('"', '\\"')
            fh.write(f'    "{esc}",\n')
        fh.write(']\n\n\nif __name__ == "__main__":\n')
        fh.write('    print(f"{len(PROMPTS_V4)} prompts")\n')

    print(f"\nwritten to: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
