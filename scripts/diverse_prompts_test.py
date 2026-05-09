"""Run a diverse set of math/CS visualization prompts through Studio
and let the telemetry layer capture everything.

Used to seed a fine-tuning corpus.  Each prompt is sent as a fresh
session (no refinement chain) so we get clean (prompt → figure)
training pairs.

Run this with Studio already running locally:
    SEVIM_TELEMETRY=1 sevim-studio  (in another terminal)
    python -m scripts.diverse_prompts_test
"""
from __future__ import annotations

import json
import sys
import time
import uuid

import httpx


PROMPTS = [
    # Reductions
    "reduce 3SAT to vertex cover with a small worked example",
    "reduce 3SAT to clique with a worked example",
    "reduce 3SAT to hamiltonian path on a 3-clause example",
    "show that vertex cover is NP-hard via reduction from independent set",
    "subset sum to partition reduction with a small example",
    # Linear algebra
    "matrix multiplication of a 3x5 by a 5x4 matrix with concrete numbers and a worked dot product",
    "eigenvalue decomposition of a 2x2 symmetric matrix with worked example",
    "Gaussian elimination on a 3x3 system, step by step",
    "singular value decomposition shown geometrically",
    # Calculus
    "derivative of sin(x) shown as the slope of the tangent at x = pi/4",
    "definite integral of x^2 from 0 to 2 as area under the curve",
    "Taylor series of e^x around 0, first three terms",
    # Set theory and logic
    "Venn diagram for A union B intersect C with concrete elements",
    "truth table for (p AND q) OR (NOT p)",
    # Graph algorithms
    "BFS traversal on a small graph step by step",
    "Dijkstra's shortest path on a 5-node weighted graph",
    # Probability
    "Bayes theorem visualization with disease testing example",
    # Number theory
    "Euclidean algorithm to find gcd(252, 105) step by step",
    # Combinatorics / discrete
    "pigeonhole principle illustrated with 5 pigeons in 4 holes",
    # Geometry
    "Pythagorean theorem proof by squares-on-sides arrangement",
]


def main(studio_url: str = "http://127.0.0.1:7781",
         out_path: str = "/tmp/diverse_prompts_results.jsonl") -> None:
    print(f"sending {len(PROMPTS)} prompts to {studio_url}", flush=True)
    results = []
    fh = open(out_path, "w")
    for i, prompt in enumerate(PROMPTS, start=1):
        # Fresh session per prompt so they don't refine each other.
        session_id = "diverse_" + uuid.uuid4().hex[:8]
        print(f"\n[{i}/{len(PROMPTS)}] {prompt!r}", flush=True)
        t0 = time.monotonic()
        body = {
            "history": [],
            "user": prompt,
            "canvas_id": None,
            "prior_canvas_ids": [],
            "session_id": session_id,
        }
        canvas_id = None
        retries = None
        verdict = None
        error = None
        try:
            with httpx.stream(
                "POST", f"{studio_url}/studio/chat",
                headers={"content-type": "application/json"},
                json=body, timeout=300.0,
            ) as r:
                if r.status_code != 200:
                    error = f"HTTP {r.status_code}"
                else:
                    for line in r.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        try:
                            ev = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if "canvas_id" in ev:
                            canvas_id = ev["canvas_id"]
                        if "retries_used" in ev:
                            retries = ev["retries_used"]
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - t0
        record = {
            "i": i, "prompt": prompt, "session_id": session_id,
            "canvas_id": canvas_id, "retries_used": retries,
            "duration_s": round(elapsed, 1),
            "error": error,
        }
        results.append(record)
        fh.write(json.dumps(record) + "\n")
        fh.flush()
        print(f"  → canvas={canvas_id} retries={retries} "
              f"duration={elapsed:.1f}s error={error}", flush=True)
        # Be polite to OpenAI; small inter-prompt gap.
        time.sleep(2)
    fh.close()
    print(f"\nDone.  {len(results)} prompts sent.  Results: {out_path}")


if __name__ == "__main__":
    main()
