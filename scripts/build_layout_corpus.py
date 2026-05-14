"""Build the neural-layout-correction training corpus.

Runs every prompt in `scripts.expanded_prompts_v5:PROMPTS_V5` through
``studio.express.express_figure`` with retries, then converts each
turn's `result["repairs"]` into `TrainingPair` records via
``studio.neural_layout.exporter.pairs_from_express_result``.

Output JSONL schema is `studio.neural_layout.schema.TrainingPair`.

Resumable: pair IDs are deterministic SHA-1 of (prompt, bad_svg,
good_svg); on resume we skip prompts that already have at least one
recorded pair in the output. Append-only.

Usage:
    OPENAI_API_KEY=... \\
    .venv/bin/python scripts/build_layout_corpus.py \\
        --model gpt-4o-mini \\
        --pool-module scripts.expanded_prompts_v5:PROMPTS_V5 \\
        --out data/neural_layout/corpus_v1.jsonl \\
        --max-retries 4 \\
        --concurrency 8

Resume the same command after a crash — already-processed prompts
are auto-skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.express import express_figure  # noqa: E402
from studio.neural_layout import exporter, schema  # noqa: E402


def _seen_prompts(path: Path) -> set[str]:
    """Read which prompts already have a pair in the output."""
    if not path.exists():
        return set()
    seen: set[str] = set()
    with path.open() as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "prompt" in row:
                seen.add(row["prompt"])
    return seen


async def _gen_one(
    prompt: str,
    *,
    model: str,
    base_url: str,
    api_key: str,
    max_retries: int,
) -> dict | None:
    try:
        result = await express_figure(
            user_prompt=prompt,
            base_url=base_url,
            model=model,
            api_key=api_key,
            max_retries=max_retries,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {prompt[:50]!r}: {type(exc).__name__}: {exc}",
              flush=True)
        return None
    return result


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--base-url",
                    default=os.environ.get("SEVIM_VLLM_URL",
                                           "https://api.openai.com/v1"))
    ap.add_argument("--out", type=Path, required=True,
                    help="Output JSONL of TrainingPair records.")
    ap.add_argument("--pool-module",
                    default="scripts.expanded_prompts_v5:PROMPTS_V5",
                    help="`module[:attr]` — list of prompts to process.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Max prompts to process this run.")
    ap.add_argument("--max-retries", type=int, default=4,
                    help="Per-prompt retries inside express_figure. More "
                         "retries = more (broken, fixed) pairs per prompt.")
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args(argv)

    from service.secrets import bootstrap as _boot
    _boot()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    import importlib
    if ":" in args.pool_module:
        mod_name, attr = args.pool_module.split(":", 1)
    else:
        mod_name, attr = args.pool_module, "PROMPTS"
    PROMPTS = list(getattr(importlib.import_module(mod_name), attr))

    seen = _seen_prompts(args.out)
    pending = [p for p in PROMPTS if p not in seen]
    if args.limit:
        pending = pending[: args.limit]

    print("=== layout-correction corpus build ===")
    print(f"  model         : {args.model}")
    print(f"  base_url      : {args.base_url}")
    print(f"  output        : {args.out}")
    print(f"  pool          : {args.pool_module}")
    print(f"  pool size     : {len(PROMPTS)}")
    print(f"  already done  : {len(seen)}")
    print(f"  pending       : {len(pending)}")
    print(f"  concurrency   : {args.concurrency}")
    print(f"  max retries   : {args.max_retries}")

    if not pending:
        print("\nNothing to do.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fh = args.out.open("a")
    write_lock = asyncio.Lock()

    sem = asyncio.Semaphore(args.concurrency)
    stats = {
        "ok_with_pairs": 0,
        "ok_no_repair": 0,  # passed first try → no pair to emit
        "fail": 0,
        "pairs_written": 0,
    }
    buckets: Counter[str] = Counter()
    t0 = time.monotonic()

    async def run_one(prompt: str, idx: int) -> None:
        async with sem:
            result = await _gen_one(
                prompt, model=args.model, base_url=args.base_url,
                api_key=api_key, max_retries=args.max_retries,
            )
        if result is None or not result.get("svg"):
            stats["fail"] += 1
            return
        pairs = exporter.pairs_from_express_result(
            prompt, result,
            extra_meta={
                "model": args.model,
                "max_retries": args.max_retries,
                "retries_used": result.get("retries_used", 0),
            },
        )
        if not pairs:
            stats["ok_no_repair"] += 1
            return
        async with write_lock:
            for p in pairs:
                fh.write(p.to_json() + "\n")
                buckets[p.math_bucket] += 1
                stats["pairs_written"] += 1
            fh.flush()
        stats["ok_with_pairs"] += 1
        print(f"  [{idx}/{len(pending)}] {prompt[:60]!r}  "
              f"retries={result.get('retries_used', 0)}  "
              f"pairs={len(pairs)}",
              flush=True)

    await asyncio.gather(*(
        run_one(p, i + 1) for i, p in enumerate(pending)
    ))
    fh.close()
    elapsed = time.monotonic() - t0
    print(f"\n=== done in {elapsed:.1f}s ===")
    print(json.dumps(stats, indent=2))
    print("By bucket:")
    for k, v in buckets.most_common():
        print(f"  {k:24s} {v:5d}")
    print(f"\noutput: {args.out}  ({args.out.stat().st_size:,} bytes)")
    return 0 if stats["fail"] < len(pending) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
