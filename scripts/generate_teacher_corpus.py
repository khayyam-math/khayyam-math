"""Generate a synthetic teacher corpus by running prompts through
gpt-4o-mini and capturing the (prompt, svg, narration, repair) tuples
in the JSONL format ``train_lora.py`` expects.

Headless — no Studio HTTP, no AWS, no users.  Reads prompts from
``scripts/teacher_prompts.py``, writes JSONL to ``--out``.

Cost:  ~$0.001 - 0.002 per prompt with gpt-4o-mini.  164 prompts
       finish in roughly 5 - 10 minutes (concurrent) and cost ~$0.25.

Usage:
    OPENAI_API_KEY=... \\
    .venv/bin/python scripts/generate_teacher_corpus.py \\
        --model gpt-4o-mini --out /tmp/teacher_v3.jsonl --limit 50

    # Then feed it into the existing trainer:
    .venv/bin/python scripts/train_lora.py \\
        --dataset /tmp/teacher_v3.jsonl --out /tmp/qwen_sevim_v3 \\
        --epochs 3 --rank 8 --alpha 16

The output mixes two SFT formats:
  * ``mode=clean``     : plain user → assistant pair (PASS first try).
  * ``mode=corrected`` : user → bad_assistant → critique → good_assistant
                          (5-message conversation; only emitted when
                          the express pipeline retried at least once).

Resumable: if the output file already exists, prompts that have been
written are skipped on a fresh run.  Append-only JSONL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Repo root on path so we can import studio.express
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.express import (  # noqa: E402
    _EXPRESS_SYSTEM,
    express_figure,
)


def _retry_user_message(critique: str) -> str:
    """Mirror the retry prompt express.py uses, so the corrected-SFT
    examples teach the model the same context it will see at inference."""
    return (
        "Your previous figure failed review.  Below is the structured "
        "list of specific fixes.  APPLY EVERY LISTED FIX — do not just "
        "regenerate a near-identical SVG.\n\n"
        + (critique or "")
        + "\n\nNow re-emit the corrected svg + narration in the same "
        "JSON schema, with every numbered fix applied."
    )


def _assistant_payload(svg: str, narration: list, title: str = "") -> str:
    return json.dumps(
        {"svg": svg, "narration": narration, "title": title or ""},
        ensure_ascii=False,
    )


def _existing_prompts(path: Path) -> set[str]:
    """Read whichever prompts have already been written, so we can resume."""
    if not path.exists():
        return set()
    seen: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        meta = row.get("meta") or {}
        if "prompt" in meta:
            seen.add(meta["prompt"])
    return seen


async def _gen_one(prompt: str, model: str, base_url: str,
                   api_key: str) -> dict | None:
    try:
        result = await express_figure(
            user_prompt=prompt,
            base_url=base_url,
            model=model,
            api_key=api_key,
            max_retries=2,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {prompt[:50]!r}: {type(exc).__name__}: {exc}", flush=True)
        return None
    return result


def _build_clean_row(prompt: str, result: dict) -> dict:
    """user → assistant pair."""
    return {
        "messages": [
            {"role": "system", "content": _EXPRESS_SYSTEM},
            {"role": "user", "content": prompt},
            {"role": "assistant",
             "content": _assistant_payload(
                 result.get("svg", ""),
                 result.get("narration") or [],
                 result.get("title") or "",
             )},
        ],
        "meta": {
            "mode": "clean",
            "prompt": prompt,
            "retries_used": result.get("retries_used", 0),
            "n_phrases": len(result.get("narration") or []),
        },
    }


def _build_corrected_rows(prompt: str, result: dict) -> list[dict]:
    """For every (bad → critique → good) repair pair the express loop
    captured, emit a 5-message conversation that teaches the model to
    apply a critique."""
    rows = []
    for pair in result.get("repairs") or []:
        rows.append({
            "messages": [
                {"role": "system", "content": _EXPRESS_SYSTEM},
                {"role": "user", "content": prompt},
                {"role": "assistant",
                 "content": _assistant_payload(
                     pair.get("bad_svg", ""),
                     pair.get("bad_narration") or [],
                 )},
                {"role": "user",
                 "content": _retry_user_message(pair.get("critique", ""))},
                {"role": "assistant",
                 "content": _assistant_payload(
                     pair.get("good_svg", ""),
                     pair.get("good_narration") or [],
                 )},
            ],
            "meta": {"mode": "corrected", "prompt": prompt},
        })
    return rows


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="gpt-4o-mini",
                    help="Teacher model id (gpt-4o-mini, gpt-4o, …)")
    ap.add_argument("--base-url",
                    default=os.environ.get("SEVIM_VLLM_URL",
                                           "https://api.openai.com/v1"))
    ap.add_argument("--out", type=Path, required=True,
                    help="Output JSONL path (append-only; resumable)")
    ap.add_argument("--pool-module", default="scripts.teacher_prompts",
                    help="Python module to import PROMPTS from "
                         "(default: scripts.teacher_prompts; "
                         "use scripts.expanded_prompts:PROMPTS_V4 for v4 pool)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Max prompts to process this run")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="Parallel express_figure calls")
    ap.add_argument("--reject-failed-review", action="store_true",
                    help="Drop the clean row when the FINAL attempt's "
                         "review verdict was FAIL.  Keeps the training "
                         "data inspector-clean: every assistant target "
                         "the model sees has been approved by every "
                         "critic we run (structural + vision + auto-fix "
                         "passes).  Recommended for OpenAI fine-tune "
                         "corpora; the express loop accepts a still-"
                         "failing figure to break the retry chain in "
                         "production, but training on those teaches the "
                         "wrong patterns.")
    args = ap.parse_args(argv)

    # Bootstrap secrets so OPENAI_API_KEY is populated.
    from service.secrets import bootstrap as _boot
    _boot()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    # Resolve --pool-module to a PROMPTS list.  Accepts forms like:
    #   scripts.teacher_prompts            (uses PROMPTS attribute)
    #   scripts.expanded_prompts:PROMPTS_V4 (uses named attribute)
    import importlib
    if ":" in args.pool_module:
        mod_name, attr = args.pool_module.split(":", 1)
    else:
        mod_name, attr = args.pool_module, "PROMPTS"
    PROMPTS = getattr(importlib.import_module(mod_name), attr)

    seen = _existing_prompts(args.out)
    pending = [p for p in PROMPTS if p not in seen]
    if args.limit:
        pending = pending[: args.limit]
    print(f"=== teacher corpus generation ===")
    print(f"  model:       {args.model}")
    print(f"  output:      {args.out}")
    print(f"  total prompts in pool:      {len(PROMPTS)}")
    print(f"  already in output (skip):   {len(seen)}")
    print(f"  pending this run:           {len(pending)}")
    print(f"  concurrency:                {args.concurrency}")

    if not pending:
        print("\nNothing to do — output already covers every prompt.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(args.concurrency)
    fh = args.out.open("a")
    stats = {"ok_clean": 0, "ok_corrected": 0, "fail": 0,
             "rejected_by_review": 0,
             "total_phrases": 0, "total_retries": 0}
    t0 = time.monotonic()

    async def run_one(prompt: str) -> None:
        async with sem:
            result = await _gen_one(prompt, args.model,
                                    args.base_url, api_key)
        if result is None or not result.get("svg"):
            stats["fail"] += 1
            return
        # Inspector filter: review_history is the list of FAILed
        # verdicts the express loop accumulated.  Each retry appends
        # one entry.  If the FINAL attempt PASSed, the verdict for
        # that attempt is NOT appended (the loop returns early).  So
        # `len(review_history) > max_retries` means every attempt
        # failed, and the SVG returned was the last-ditch
        # accept-anyway version — we exclude it from the training
        # corpus when --reject-failed-review is set.
        if args.reject_failed_review:
            history_len = len(result.get("review_history") or [])
            # express_figure's default max_retries=1 → 2 attempts.
            # We don't know the model's max_retries from here, but
            # the rule is the same: if the count is >= the number of
            # attempts the loop made, the final verdict was FAIL.
            if (result.get("retries_used", 0) > 0
                    and history_len > result.get("retries_used", 0)):
                stats["rejected_by_review"] += 1
                print(f"  [reject] {prompt[:60]!r}: final review FAIL "
                      f"(history_len={history_len})", flush=True)
                return
        # Always emit the clean row.
        row = _build_clean_row(prompt, result)
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        stats["ok_clean"] += 1
        stats["total_phrases"] += row["meta"]["n_phrases"]
        stats["total_retries"] += row["meta"]["retries_used"]
        # Plus any corrected examples from retries.
        for cr in _build_corrected_rows(prompt, result):
            fh.write(json.dumps(cr, ensure_ascii=False) + "\n")
            fh.flush()
            stats["ok_corrected"] += 1
        n_done = stats["ok_clean"] + stats["fail"]
        print(f"  [{n_done}/{len(pending)}] {prompt[:70]!r}  "
              f"retries={row['meta']['retries_used']}  "
              f"phrases={row['meta']['n_phrases']}",
              flush=True)

    await asyncio.gather(*(run_one(p) for p in pending))
    fh.close()
    elapsed = time.monotonic() - t0
    print(f"\n=== done in {elapsed:.1f}s ===")
    print(json.dumps(stats, indent=2))
    print(f"\noutput: {args.out}  ({args.out.stat().st_size} bytes)")
    return 0 if stats["fail"] < len(pending) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
