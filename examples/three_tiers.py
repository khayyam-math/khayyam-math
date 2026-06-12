#!/usr/bin/env python3
"""Three ways to use Khayyam Math from Python — and where the data comes from.

Run it:

    python examples/three_tiers.py

Tier 3 (deterministic, offline) ALWAYS runs — it needs no API key and makes
no network call.  Tiers 1 and 2 run only if OPENAI_API_KEY is set; otherwise
they print an explanation and skip, so the script is safe to run anywhere.

The one question this script answers concretely:  when you import Khayyam
Math, where does the figure come from?

  • Tier 1 / Tier 2 default  →  the model YOU configure (OpenAI gpt-4o by
                                default, using YOUR OPENAI_API_KEY → the
                                OpenAI API).  Never khayyammath.com.
  • Tier 3                   →  pure local code (a deterministic template).
                                No model, no key, no network at all.

There is no hidden call back to our server in any tier: the package never
phones home to khayyammath.com to generate a figure.
"""
from __future__ import annotations

import asyncio
import os


def banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ─────────────────────────────────────────────────────────────────────
# TIER 1 — the thin public client:  one model call.
#
# `from khayyam_math import KhayyamMath` is the documented public API.
# `client.generate(prompt)` makes ONE chat-completion call to the
# configured backend and parses the SVG + narration out of the reply.
# It does NOT run the deterministic templates, the vision-retry quality
# loop, layout repair, or TTS — that is Tier 2.  Switch backends with the
# `provider=` argument; the call signature and return type never change.
# ─────────────────────────────────────────────────────────────────────
def tier1_thin_client() -> None:
    banner("TIER 1 — KhayyamMath client (single model call)")
    from khayyam_math import KhayyamMath

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set → skipping.")
        print("This tier calls OpenAI's gpt-4o with YOUR key; the figure is")
        print("produced by OpenAI's model, not by our server.")
        print("To use a local fine-tuned model instead (no OpenAI):")
        print('    KhayyamMath(provider="qwen")        # GPU + pip install khayyam-math[qwen]')
        print('    KhayyamMath(provider="qwen-vllm", base_url="http://localhost:8000/v1")')
        return

    client = KhayyamMath()                       # provider="openai", model="gpt-4o"
    result = client.generate("Solve x^2 - 5x + 6 = 0")
    print(f"provider={result.provider}  model={result.model}")
    print(f"solution: {result.solution!r}")
    print(f"svg: {len(result.svg)} chars   narration: {len(result.narration)} phrases")
    print("→ source: OpenAI gpt-4o (cloud). Single call. No deterministic routing.")


# ─────────────────────────────────────────────────────────────────────
# TIER 2 — the full production engine that khayyammath.com runs.
#
# `studio.express.express_figure` is the real pipeline: a deterministic
# route cascade (answer-cache → taxonomy → np-completeness → reduction →
# algorithm-trace → graphviz → matplotlib → … → LLM-SVG fallback), plus
# vision review/retry and layout repair.  It is importable too.  Give it
# an OpenAI key and it behaves like the website, locally.
# ─────────────────────────────────────────────────────────────────────
def tier2_full_engine() -> None:
    banner("TIER 2 — express_figure (the full website engine, run locally)")
    from studio.express import express_figure

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set → skipping the LLM-backed routes.")
        print("With a key, this is the same engine khayyammath.com runs:")
        print("deterministic templates first, LLM-SVG + vision-retry as a fallback.")
        return

    key = os.environ["OPENAI_API_KEY"]
    result = asyncio.run(express_figure(
        "explain Bayes theorem with a tree diagram",
        base_url="https://api.openai.com/v1", model="gpt-4o", api_key=key,
    ))
    print(f"route/template: {result.get('template')}   "
          f"retries: {result.get('retries_used')}   "
          f"svg: {len(result.get('svg') or '')} chars")
    print("→ source: a deterministic route if one matched, else OpenAI gpt-4o.")
    print("  Figure assembly / repair / verification all ran locally in-process.")


# ─────────────────────────────────────────────────────────────────────
# TIER 3 — deterministic, fully offline.  No key, no network.
#
# Whole classes of figure are drawn by local code with zero LLM calls:
# graph-shaped figures (graphviz), function plots (matplotlib), NP-proofs,
# and complexity reductions.  Here we ask for a reduction; the figure is
# produced entirely on your machine.  Passing an empty api_key proves no
# model is involved.
# ─────────────────────────────────────────────────────────────────────
def tier3_offline_deterministic() -> None:
    banner("TIER 3 — deterministic template (no key, no network)")
    from studio.express import express_figure

    result = asyncio.run(express_figure(
        "reduce subset sum to partition",
        base_url="", model="", api_key="",          # no model, no network
    ))
    svg = result.get("svg") or ""
    print(f"route/template: {result.get('template')}   svg: {len(svg)} chars   "
          f"narration: {len(result.get('narration') or [])} phrases")
    out = os.path.join(os.path.dirname(__file__), "tier3_reduction.svg")
    with open(out, "w") as f:
        f.write(svg)
    print(f"wrote {out}")
    print("→ source: pure local code. No model, no API key, no call to anyone.")


def main() -> None:
    print(__doc__)
    tier3_offline_deterministic()   # always works
    tier2_full_engine()             # needs OPENAI_API_KEY
    tier1_thin_client()             # needs OPENAI_API_KEY

    banner("Also available (not shown here)")
    print("• Studio web app + live canvas viewer:   python -m studio   (or: sevim-studio)")
    print("• MCP server for Claude / Cursor:        sevim-mcp")
    print("• None of these contact khayyammath.com to generate figures.")


if __name__ == "__main__":
    main()
