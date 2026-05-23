"""Export the telemetry corpus as a Qwen-format fine-tuning dataset.

Three modes, three different supervised signals:

  * ``--mode sft-clean``    — pristine ``user → assistant`` pairs from
                              first-try-pass turns.  This is what
                              ``train_lora.py`` consumed for v1/v2.

  * ``--mode sft-corrected`` — three-message conversations distilled
                              from the (bad → critique → good) triples
                              the math-correctness inspector produces:
                              ``user → bad_assistant → critique →
                              good_assistant``.  Teaches the model to
                              APPLY a reviewer's critique, which is the
                              core skill that turns gpt-4o into a
                              teacher-of-Qwen.

  * ``--mode dpo-pairs``    — preference pairs ready for DPO / IPO /
                              KTO style preference optimisation.  Each
                              row: ``{prompt, chosen, rejected}`` where
                              chosen is the corrected output and
                              rejected is the original mistake.

All modes support ``--since <unix_ts>`` for incremental exports — the
distillation cycle script (PR9) writes the timestamp of the last
exported row to a manifest and passes it back next run, so the export
is idempotent and cheap to schedule.

Output destinations:

  * ``--out PATH``                  local file (default
                                    ``~/.local/share/sevim/finetune.<mode>.jsonl``)
  * ``--s3-bucket BUCKET [--s3-prefix PREFIX]``
                                    upload the JSONL to S3 after writing
                                    locally.  Honours
                                    ``SEVIM_EXPORT_S3_BUCKET`` env var
                                    as a default.

Examples
--------
    python -m studio.export_finetune --mode sft-clean
    python -m studio.export_finetune --mode sft-corrected --since 1715300000
    python -m studio.export_finetune --mode dpo-pairs \\
            --s3-bucket <your-training-bucket> --s3-prefix dpo/
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from sevim.telemetry import Telemetry, _resolved_db_url


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _system_prompt() -> str:
    """The express system prompt the model is being trained against.
    Matched at runtime so the JSONL stays in sync with whatever
    ``studio/express.py`` is currently shipping."""
    from studio.express import _EXPRESS_SYSTEM
    return _EXPRESS_SYSTEM


def _assistant_payload(svg: str, narration: list[dict], title: str = "") -> str:
    return json.dumps(
        {"svg": svg, "narration": narration, "title": title or ""},
        ensure_ascii=False,
    )


def _retry_user_message(critique: str) -> str:
    """The retry prompt the express loop actually sends back to the model
    when a review fails.  Mirroring it during training means the
    model sees the same context shape it'll see at inference time."""
    return (
        "Your previous figure failed review.  Below is the structured "
        "list of specific fixes.  APPLY EVERY LISTED FIX — do not just "
        "regenerate a near-identical SVG.  Each fix names a concrete "
        "action, the element it applies to, where it goes, and the "
        "exact content/values to use.\n\n"
        + (critique or "")
        + "\n\nNow re-emit the corrected svg + narration in the same "
        "JSON schema, with every numbered fix above actually applied."
    )


def _open_telemetry() -> Telemetry:
    return Telemetry(db_url=_resolved_db_url())


# ---------------------------------------------------------------------------
# Mode: sft-clean — first-try-pass turns
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PII scrub — applied to user prompts before they reach a training corpus.
# ---------------------------------------------------------------------------

# Conservative regexes.  Each one matches a clearly-personal pattern; on a
# hit, the whole turn is DROPPED (not redacted) so the model never sees
# even a sanitised version of the original.
_PII_PATTERNS: tuple[tuple[str, "re.Pattern"], ...] = (
    ("email",   __import__("re").compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")),
    ("phone",   __import__("re").compile(r"\+?\d[\d\s\-()]{7,}\d")),
    ("student", __import__("re").compile(
        r"\b(my (?:student|son|daughter|child|kid|teacher|professor|advisor|"
        r"supervisor|tutor)|named\s+[A-Z][a-z]+|for (?:my|the) "
        r"(?:student|son|daughter)\s+[A-Z][a-z]+)\b",
        __import__("re").IGNORECASE)),
)


def _pii_flag(text: str) -> str | None:
    """Return the name of the first PII pattern that matches, or None."""
    if not text:
        return None
    for name, pat in _PII_PATTERNS:
        if pat.search(text):
            return name
    return None


def _export_sft_clean(
    tel: Telemetry,
    out_path: Path,
    keep_all: bool,
    limit: int | None,
    since_ts: float,
    refined_threshold_s: float,
) -> dict:
    rows = tel.query(
        """
        SELECT t.turn_id, t.timestamp, t.user_prompt, t.canvas_id,
               t.retries_used, t.refined_within_s,
               c.svg, c.narration_json, c.title
          FROM turns t
          JOIN canvases c ON c.canvas_id = t.canvas_id
         WHERE t.canvas_id IS NOT NULL
           AND t.timestamp > ?
         ORDER BY t.timestamp ASC
        """,
        (since_ts,),
    )

    stats = {
        "mode": "sft-clean", "walked": 0, "kept": 0,
        "skipped_no_svg": 0, "skipped_retries": 0, "skipped_refined": 0,
        "skipped_pii": 0,
        "out_path": str(out_path), "last_ts": since_ts,
    }
    sys_prompt = _system_prompt()

    with out_path.open("w") as fh:
        for row in rows:
            stats["walked"] += 1
            if limit is not None and stats["kept"] >= limit:
                break
            (turn_id, ts, user_prompt, canvas_id, retries_used,
             refined_within_s, svg, narration_json, title) = row
            if not svg:
                stats["skipped_no_svg"] += 1
                continue
            if not keep_all:
                if (retries_used or 0) > 0:
                    stats["skipped_retries"] += 1
                    continue
                if (refined_within_s is not None
                        and refined_within_s < refined_threshold_s):
                    stats["skipped_refined"] += 1
                    continue
            if _pii_flag(user_prompt):
                stats["skipped_pii"] += 1
                continue
            try:
                narration = json.loads(narration_json or "[]")
            except json.JSONDecodeError:
                narration = []
            line = {
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt or ""},
                    {"role": "assistant",
                     "content": _assistant_payload(svg, narration, title)},
                ],
                "meta": {
                    "mode": "sft-clean", "turn_id": turn_id,
                    "canvas_id": canvas_id,
                    "retries_used": retries_used,
                    "refined_within_s": refined_within_s,
                    "ts": ts,
                },
            }
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            stats["kept"] += 1
            stats["last_ts"] = max(stats["last_ts"], ts)
    return stats


# ---------------------------------------------------------------------------
# Mode: sft-corrected — (bad → critique → good) repair triples
# ---------------------------------------------------------------------------

def _export_sft_corrected(
    tel: Telemetry,
    out_path: Path,
    limit: int | None,
    since_ts: float,
) -> dict:
    rows = tel.query(
        """
        SELECT repair_id, turn_id, timestamp, user_prompt,
               bad_svg, bad_narration_json, critique,
               good_svg, good_narration_json
          FROM repairs
         WHERE timestamp > ?
         ORDER BY timestamp ASC
        """,
        (since_ts,),
    )

    stats = {
        "mode": "sft-corrected", "walked": 0, "kept": 0,
        "skipped_incomplete": 0,
        "out_path": str(out_path), "last_ts": since_ts,
    }
    sys_prompt = _system_prompt()

    with out_path.open("w") as fh:
        for row in rows:
            stats["walked"] += 1
            if limit is not None and stats["kept"] >= limit:
                break
            (repair_id, turn_id, ts, user_prompt,
             bad_svg, bad_narr_json, critique,
             good_svg, good_narr_json) = row
            if not (bad_svg and good_svg and user_prompt and critique):
                stats["skipped_incomplete"] += 1
                continue
            try:
                bad_narr = json.loads(bad_narr_json or "[]")
                good_narr = json.loads(good_narr_json or "[]")
            except json.JSONDecodeError:
                stats["skipped_incomplete"] += 1
                continue
            line = {
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant",
                     "content": _assistant_payload(bad_svg, bad_narr)},
                    {"role": "user",
                     "content": _retry_user_message(critique)},
                    {"role": "assistant",
                     "content": _assistant_payload(good_svg, good_narr)},
                ],
                "meta": {
                    "mode": "sft-corrected",
                    "repair_id": repair_id, "turn_id": turn_id, "ts": ts,
                },
            }
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            stats["kept"] += 1
            stats["last_ts"] = max(stats["last_ts"], ts)
    return stats


# ---------------------------------------------------------------------------
# Mode: dpo-pairs — chosen/rejected preference data
# ---------------------------------------------------------------------------

def _export_dpo_pairs(
    tel: Telemetry,
    out_path: Path,
    limit: int | None,
    since_ts: float,
) -> dict:
    rows = tel.query(
        """
        SELECT repair_id, turn_id, timestamp, user_prompt,
               bad_svg, bad_narration_json,
               good_svg, good_narration_json
          FROM repairs
         WHERE timestamp > ?
         ORDER BY timestamp ASC
        """,
        (since_ts,),
    )

    stats = {
        "mode": "dpo-pairs", "walked": 0, "kept": 0,
        "skipped_incomplete": 0,
        "out_path": str(out_path), "last_ts": since_ts,
    }
    sys_prompt = _system_prompt()

    with out_path.open("w") as fh:
        for row in rows:
            stats["walked"] += 1
            if limit is not None and stats["kept"] >= limit:
                break
            (repair_id, turn_id, ts, user_prompt,
             bad_svg, bad_narr_json,
             good_svg, good_narr_json) = row
            if not (bad_svg and good_svg and user_prompt):
                stats["skipped_incomplete"] += 1
                continue
            try:
                bad_narr = json.loads(bad_narr_json or "[]")
                good_narr = json.loads(good_narr_json or "[]")
            except json.JSONDecodeError:
                stats["skipped_incomplete"] += 1
                continue
            line = {
                "prompt": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "chosen": _assistant_payload(good_svg, good_narr),
                "rejected": _assistant_payload(bad_svg, bad_narr),
                "meta": {
                    "mode": "dpo-pairs",
                    "repair_id": repair_id, "turn_id": turn_id, "ts": ts,
                },
            }
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            stats["kept"] += 1
            stats["last_ts"] = max(stats["last_ts"], ts)
    return stats


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def export(
    mode: str = "sft-clean",
    out_path: Path | None = None,
    keep_all: bool = False,
    limit: int | None = None,
    since_ts: float = 0.0,
    refined_threshold_s: float = 60.0,
    s3_bucket: str | None = None,
    s3_prefix: str = "",
) -> dict:
    out_path = out_path or (
        Path.home() / ".local" / "share" / "sevim" / f"finetune.{mode}.jsonl"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tel = _open_telemetry()

    if mode == "sft-clean":
        stats = _export_sft_clean(
            tel, out_path, keep_all, limit, since_ts, refined_threshold_s,
        )
    elif mode == "sft-corrected":
        stats = _export_sft_corrected(tel, out_path, limit, since_ts)
    elif mode == "dpo-pairs":
        stats = _export_dpo_pairs(tel, out_path, limit, since_ts)
    else:
        return {"error": f"unknown mode {mode!r} (sft-clean | sft-corrected | dpo-pairs)"}

    # Optional S3 upload — best effort; failures don't crash the export.
    if s3_bucket:
        key = f"{s3_prefix.rstrip('/')}/{out_path.name}".lstrip("/")
        try:
            import boto3
            boto3.client("s3").upload_file(
                Filename=str(out_path), Bucket=s3_bucket, Key=key,
                ExtraArgs={"ContentType": "application/x-ndjson"},
            )
            stats["s3_url"] = f"s3://{s3_bucket}/{key}"
        except Exception as exc:  # noqa: BLE001
            stats["s3_error"] = f"{type(exc).__name__}: {exc}"

    return stats


def main(argv: list[str] | None = None) -> int:
    import os
    # Translate SEVIM_DB_SECRET_JSON (the form ECS injects) into the
    # SEVIM_TELEMETRY_DB URL the telemetry layer expects.  Without this
    # the export silently falls back to an empty local SQLite when run
    # as a one-off task (`python -m studio.export_finetune ...`).
    try:
        from service.secrets import bootstrap as _bootstrap_secrets  # noqa: PLC0415
        _bootstrap_secrets()
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["sft-clean", "sft-corrected", "dpo-pairs"],
                    default="sft-clean")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--all", action="store_true",
                    help="(sft-clean only) skip quality filters")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--since", type=float, default=0.0,
                    help="Only export rows with timestamp > this (unix epoch)")
    ap.add_argument("--refined-threshold", type=float, default=60.0,
                    help="Skip clean turns followed by a refinement "
                         "within N seconds")
    ap.add_argument("--s3-bucket", default=os.environ.get("SEVIM_EXPORT_S3_BUCKET"))
    ap.add_argument("--s3-prefix", default=os.environ.get("SEVIM_EXPORT_S3_PREFIX", ""))
    args = ap.parse_args(argv)

    stats = export(
        mode=args.mode, out_path=args.out, keep_all=args.all,
        limit=args.limit, since_ts=args.since,
        refined_threshold_s=args.refined_threshold,
        s3_bucket=args.s3_bucket, s3_prefix=args.s3_prefix,
    )
    stats["exported_at"] = time.time()
    print(json.dumps(stats, indent=2))
    return 0 if "error" not in stats else 1


if __name__ == "__main__":
    sys.exit(main())
