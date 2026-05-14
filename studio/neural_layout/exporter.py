"""Convert express-loop output into TrainingPair records.

Two entry points:

- `pairs_from_express_result(prompt, result)` — for live capture in
  the corpus-generation script.
- `pairs_from_teacher_corpus_row(row)` — for offline extraction from
  the existing `data/distill/teacher_v6_mini.jsonl` (`mode=corrected`
  rows). This gives us 1045 free pairs without any new generation.

Both produce a list[TrainingPair] (a single express turn can yield
multiple pairs if the retry loop ran more than once).

Pair IDs are deterministic: SHA-1 of (prompt + bad_svg + good_svg)
truncated. Re-running the exporter on the same input is idempotent.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .schema import (
    MATH_BUCKETS, VIEWPORT_KINDS, TrainingPair, classify_math_bucket,
)
from .svg_to_graph import parse_svg


def _pair_id(prompt: str, bad: str, good: str) -> str:
    h = hashlib.sha1()
    h.update(prompt.encode("utf-8", errors="replace"))
    h.update(b"|")
    h.update(bad.encode("utf-8", errors="replace"))
    h.update(b"|")
    h.update(good.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


def _viewport_from_canvas(canvas_w: int) -> str:
    if canvas_w <= 480:
        return "phone"
    if canvas_w <= 800:
        return "tablet"
    return "desktop"


def _parse_assistant_payload(content: str) -> dict[str, Any] | None:
    """The assistant messages in teacher_v6_mini are JSON strings."""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None


def _make_pair(
    *,
    prompt: str,
    bad_svg: str,
    good_svg: str,
    critique: str = "",
    extra_meta: dict[str, Any] | None = None,
) -> TrainingPair | None:
    """Parse both SVGs and build a TrainingPair. Returns None if
    either parse produces zero nodes (e.g. malformed SVG)."""
    if not bad_svg or not good_svg:
        return None
    src = parse_svg(bad_svg)
    tgt = parse_svg(good_svg)
    if not src.graph.nodes or not tgt.graph.nodes:
        return None
    viewport = _viewport_from_canvas(src.graph.canvas_w)
    if viewport not in VIEWPORT_KINDS:
        viewport = "desktop"
    bucket = classify_math_bucket(prompt)
    if bucket not in MATH_BUCKETS:
        bucket = "other"
    meta: dict[str, Any] = {
        "critique": critique[:2000] if critique else "",
        "source_warnings": src.warnings,
        "target_warnings": tgt.warnings,
        "source_node_count": len(src.graph.nodes),
        "target_node_count": len(tgt.graph.nodes),
    }
    if extra_meta:
        meta.update(extra_meta)
    return TrainingPair(
        pair_id=_pair_id(prompt, bad_svg, good_svg),
        prompt=prompt,
        source=src.graph,
        target=tgt.graph,
        viewport_kind=viewport,
        math_bucket=bucket,
        metadata=meta,
    )


def pairs_from_express_result(
    prompt: str,
    result: dict[str, Any],
    *,
    extra_meta: dict[str, Any] | None = None,
) -> list[TrainingPair]:
    """Live capture: read `result["repairs"]` from `express_figure`.

    Each repair entry has `bad_svg`, `good_svg`, `critique`,
    `attempt_index`. We emit one TrainingPair per repair.

    We additionally emit a "long-distance" pair: first failed attempt
    paired with the final accepted output, when there were 2+ retries
    (teaches more aggressive corrections than any single step does).
    """
    pairs: list[TrainingPair] = []
    repairs = result.get("repairs") or []
    for r in repairs:
        meta = {
            "source": "express_live",
            "attempt_index": r.get("attempt_index"),
        }
        if extra_meta:
            meta.update(extra_meta)
        p = _make_pair(
            prompt=prompt,
            bad_svg=r.get("bad_svg", ""),
            good_svg=r.get("good_svg", ""),
            critique=r.get("critique", ""),
            extra_meta=meta,
        )
        if p is not None:
            pairs.append(p)

    # Long-distance pair: first-fail → final-accepted.
    if len(repairs) >= 2:
        first = repairs[0]
        last_good_svg = result.get("svg", "")
        if last_good_svg and first.get("bad_svg"):
            meta = {"source": "express_live_long"}
            if extra_meta:
                meta.update(extra_meta)
            p = _make_pair(
                prompt=prompt,
                bad_svg=first.get("bad_svg", ""),
                good_svg=last_good_svg,
                critique="long-distance: first-fail → final-accept",
                extra_meta=meta,
            )
            if p is not None:
                pairs.append(p)
    return pairs


def pairs_from_teacher_corpus_row(
    row: dict[str, Any],
) -> list[TrainingPair]:
    """Extract pairs from one row of `teacher_v6_mini.jsonl`.

    Layout:
        messages = [system, user, assistant(bad), user(critique),
                    assistant(good)]
        meta = {"mode": "corrected" | "clean", "prompt": ...}

    "clean" rows have no broken counterpart → return [].
    "corrected" rows yield exactly one TrainingPair.
    """
    meta = row.get("meta") or {}
    if meta.get("mode") != "corrected":
        return []
    msgs = row.get("messages") or []
    if len(msgs) < 5:
        return []
    prompt = meta.get("prompt") or msgs[1].get("content", "")
    bad_obj = _parse_assistant_payload(msgs[2].get("content", ""))
    critique = msgs[3].get("content", "")
    good_obj = _parse_assistant_payload(msgs[4].get("content", ""))
    if not bad_obj or not good_obj:
        return []
    p = _make_pair(
        prompt=prompt,
        bad_svg=bad_obj.get("svg", ""),
        good_svg=good_obj.get("svg", ""),
        critique=critique,
        extra_meta={"source": "teacher_v6_mini"},
    )
    return [p] if p is not None else []
