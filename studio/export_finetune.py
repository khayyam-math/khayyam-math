"""Export the telemetry corpus as a Qwen-format chat-fine-tune dataset.

Walks the SQLite telemetry DB and produces a JSONL file suitable for
LoRA / SFT fine-tuning of Qwen2.5-Instruct.  Output format follows
the de-facto chat-completions standard:

    {"messages": [
        {"role": "system",    "content": "<EXPRESS_SYSTEM>"},
        {"role": "user",      "content": "<user_prompt>"},
        {"role": "assistant", "content": "<json: {svg, narration, title}>"}
    ]}

Filtering rules
---------------
By default, only "good" turns are exported:
  * canvas_id present (we got an actual figure out)
  * retries_used == 0 (model nailed it first try — pristine pair)
  * accepted = True OR refined_within_s is None or > 60 s
    (user didn't immediately re-prompt to fix)

Pass ``--all`` to dump everything; ``--limit N`` to cap; ``--out PATH``
to choose the destination.  Default destination is
``~/.local/share/sevim/finetune.jsonl``.

Usage
-----
    python -m studio.export_finetune
    python -m studio.export_finetune --out my_dataset.jsonl
    python -m studio.export_finetune --all --limit 1000

Stats
-----
Prints a summary at the end: turns walked, turns kept, average prompt
length, average SVG length, etc.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sevim.telemetry import default_db_path, Telemetry


def export(
    db_path: Path | None = None,
    out_path: Path | None = None,
    keep_all: bool = False,
    limit: int | None = None,
    accepted_only: bool = True,
    refined_threshold_s: float = 60.0,
) -> dict:
    """Walk the telemetry DB and write a JSONL training file.

    Returns a stats dict: walked, kept, skipped_by_*, mean_prompt_len, ...
    """
    db_path = db_path or default_db_path()
    out_path = out_path or (Path.home() / ".local/share/sevim/finetune.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        return {"error": f"telemetry db not found at {db_path}"}

    tel = Telemetry(db_path=db_path)
    # We need to read the express system prompt the model was fine-tuned
    # against, so the assistant learns the same input contract.
    from studio.express import _EXPRESS_SYSTEM as SYSTEM_PROMPT  # noqa: PLC0415

    rows = tel.query(
        """
        SELECT t.turn_id, t.user_prompt, t.canvas_id, t.retries_used,
               t.refined_within_s, c.svg, c.narration_json, c.title
          FROM turns t
          JOIN canvases c ON c.canvas_id = t.canvas_id
         WHERE t.canvas_id IS NOT NULL
         ORDER BY t.timestamp ASC
        """,
    )

    stats: dict = {
        "walked": 0, "kept": 0,
        "skipped_no_svg": 0, "skipped_retries": 0,
        "skipped_refined": 0, "skipped_other": 0,
        "mean_prompt_len": 0, "mean_svg_len": 0,
        "out_path": str(out_path),
    }
    prompt_lens: list[int] = []
    svg_lens: list[int] = []

    with out_path.open("w") as fh:
        for row in rows:
            stats["walked"] += 1
            if limit is not None and stats["kept"] >= limit:
                break
            (turn_id, user_prompt, canvas_id, retries_used,
             refined_within_s, svg, narration_json, title) = row
            if not svg:
                stats["skipped_no_svg"] += 1
                continue
            if not keep_all:
                if (retries_used or 0) > 0:
                    stats["skipped_retries"] += 1
                    continue
                if accepted_only and refined_within_s is not None \
                        and refined_within_s < refined_threshold_s:
                    stats["skipped_refined"] += 1
                    continue
            try:
                narration = json.loads(narration_json or "[]")
            except json.JSONDecodeError:
                narration = []
            assistant_payload = json.dumps(
                {"svg": svg, "narration": narration, "title": title or ""},
                ensure_ascii=False,
            )
            line = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt or ""},
                    {"role": "assistant", "content": assistant_payload},
                ],
                "meta": {
                    "turn_id": turn_id, "canvas_id": canvas_id,
                    "retries_used": retries_used,
                    "refined_within_s": refined_within_s,
                },
            }
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            stats["kept"] += 1
            prompt_lens.append(len(user_prompt or ""))
            svg_lens.append(len(svg))

    stats["mean_prompt_len"] = (
        int(sum(prompt_lens) / len(prompt_lens)) if prompt_lens else 0
    )
    stats["mean_svg_len"] = (
        int(sum(svg_lens) / len(svg_lens)) if svg_lens else 0
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None,
                    help="Telemetry SQLite path (default: SEVIM_TELEMETRY_DB env)")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output JSONL path")
    ap.add_argument("--all", action="store_true",
                    help="Skip quality filters; export every turn with a canvas")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--refined-threshold", type=float, default=60.0,
                    help="Skip turns followed by a refinement within this many seconds")
    args = ap.parse_args(argv)

    stats = export(
        db_path=args.db, out_path=args.out, keep_all=args.all,
        limit=args.limit, refined_threshold_s=args.refined_threshold,
    )
    print(json.dumps(stats, indent=2))
    return 0 if "error" not in stats else 1


if __name__ == "__main__":
    sys.exit(main())
