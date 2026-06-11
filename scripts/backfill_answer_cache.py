#!/usr/bin/env python3
"""Backfill the answer-cache index (canvas_index) from existing turns.

Embeds the prompt of every already-shipped canvas and writes a
canvas_index row, so the answer cache has history to retrieve from the
moment it's switched on (SEVIM_ANSWER_CACHE=1).

Idempotent: re-running upserts by canvas_id and skips prompts whose
embedding is already present (so it costs nothing on the second run).

Usage:
    OPENAI_API_KEY=... SEVIM_DB_URL=... \
        python scripts/backfill_answer_cache.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sevim import embeddings as emb       # noqa: E402
from sevim.telemetry import get_telemetry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="max canvases to process (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not emb.available():
        print("No embedding API key (OPENAI_API_KEY / SEVIM_EMBED_API_KEY); "
              "cannot backfill.", file=sys.stderr)
        return 2
    tel = get_telemetry()
    if tel is None:
        print("Telemetry not configured (set SEVIM_DB_URL).", file=sys.stderr)
        return 2

    # Most recent shipped canvas per distinct prompt — one good exemplar
    # per question is enough, and the newest is usually the best.
    rows = tel.query(
        """
        SELECT t.canvas_id, t.user_prompt, c.accepted
          FROM turns t
          JOIN canvases c ON c.canvas_id = t.canvas_id
         WHERE t.canvas_id IS NOT NULL AND c.svg IS NOT NULL
         ORDER BY t.timestamp DESC
        """
    )
    already = {r[0] for r in tel.iter_canvas_index(accepted_only=False)}
    model = os.environ.get("SEVIM_EMBED_MODEL", "text-embedding-3-small")

    seen_prompts: set[str] = set()
    done = skipped = 0
    for canvas_id, prompt, accepted in rows:
        prompt = (prompt or "").strip()
        if not prompt or canvas_id in already:
            skipped += 1
            continue
        key = prompt.lower()
        if key in seen_prompts:
            skipped += 1
            continue
        seen_prompts.add(key)
        if args.dry_run:
            done += 1
            continue
        vec = emb.embed(prompt)
        if vec is None:
            skipped += 1
            continue
        tel.index_canvas(canvas_id, prompt, json.dumps(vec), model,
                         accepted=bool(accepted) or True)
        done += 1
        if args.limit and done >= args.limit:
            break
        if done % 25 == 0:
            print(f"  …{done} indexed", flush=True)

    print(f"backfill complete: indexed={done} skipped={skipped} "
          f"({'dry-run' if args.dry_run else 'written'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
