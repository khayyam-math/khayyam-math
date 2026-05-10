"""Distillation export mode tests.

Seeds a temp telemetry DB with a clean turn, a noisy retried turn, and
two repair pairs.  Then runs each export mode and inspects:
  * sft-clean keeps only the first-try-pass row.
  * sft-corrected emits a 5-message conversation per repair pair.
  * dpo-pairs emits {prompt, chosen, rejected} per repair pair.
  * --since cursor filters older rows.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _seed(tmpdir: Path) -> Path:
    """Write a fresh telemetry DB and return its path."""
    db_path = tmpdir / "t.db"
    os.environ["SEVIM_TELEMETRY_DB"] = str(db_path)
    # Force a fresh telemetry instance so the env var takes effect.
    sys.modules.pop("sevim.telemetry", None)
    from sevim.telemetry import Telemetry
    tel = Telemetry(db_url=str(db_path))
    tel.upsert_session("s1", user_agent="ua")

    # Noisy turn first — needed retries.  Will be filtered from sft-clean.
    turn_noisy = tel.record_turn(
        session_id="s1",
        user_prompt="show pythagoras",
        canvas_id="c_noisy",
        retries_used=2,
        cost_usd_estimate=0.15,
    )
    tel.record_canvas(
        canvas_id="c_noisy", session_id="s1", turn_id=turn_noisy,
        title="Pythagoras", svg="<svg id='noisy_final'/>",
        narration=[{"speak": "fixed", "highlight": []}],
    )

    # Clean turn LAST so no subsequent turn backfills refined_within_s.
    turn_clean = tel.record_turn(
        session_id="s1",
        user_prompt="show triangle interior angle sum",
        canvas_id="c_clean",
        retries_used=0,
        cost_usd_estimate=0.05,
    )
    tel.record_canvas(
        canvas_id="c_clean", session_id="s1", turn_id=turn_clean,
        title="Triangle angles", svg="<svg id='clean'/>",
        narration=[{"speak": "right", "highlight": []}],
    )

    # Repair pair — what the inspector caught + the corrected version.
    tel.record_repair_pair(
        session_id="s1", turn_id=turn_noisy, attempt_index=1,
        user_prompt="show pythagoras",
        bad_svg="<svg id='bad_pyth'/>",
        bad_narration=[{"speak": "wrong claim", "highlight": []}],
        critique="FAIL: a^2 + b^2 != c (missing exponent)",
        good_svg="<svg id='good_pyth'/>",
        good_narration=[{"speak": "right claim", "highlight": []}],
    )

    # Second repair pair — newer timestamp, used for --since test.
    time.sleep(0.01)
    tel.record_repair_pair(
        session_id="s1", turn_id=None, attempt_index=1,
        user_prompt="show derivative",
        bad_svg="<svg id='bad_deriv'/>",
        bad_narration=[],
        critique="FAIL: derivative of x^2 is 2x not x",
        good_svg="<svg id='good_deriv'/>",
        good_narration=[],
    )
    return db_path


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_sft_clean_filters_retries() -> None:
    with tempfile.TemporaryDirectory() as d:
        _seed(Path(d))
        sys.modules.pop("studio.export_finetune", None)
        from studio.export_finetune import export
        out = Path(d) / "out.jsonl"
        stats = export(mode="sft-clean", out_path=out)
        rows = _read_jsonl(out)
        assert stats["kept"] == 1, stats
        assert stats["skipped_retries"] == 1, stats
        assert rows[0]["meta"]["canvas_id"] == "c_clean"
        # 3 messages: system, user, assistant
        assert len(rows[0]["messages"]) == 3
        print("OK: sft-clean drops retried turns")


def test_sft_corrected_yields_5_message_conversation() -> None:
    with tempfile.TemporaryDirectory() as d:
        _seed(Path(d))
        sys.modules.pop("studio.export_finetune", None)
        from studio.export_finetune import export
        out = Path(d) / "out.jsonl"
        stats = export(mode="sft-corrected", out_path=out)
        rows = _read_jsonl(out)
        assert stats["kept"] == 2, stats
        # 5-msg conversation: system, user, bad_assistant, user(critique), good_assistant
        msgs = rows[0]["messages"]
        assert len(msgs) == 5, msgs
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"
        assert msgs[3]["role"] == "user"
        assert "FAIL" in msgs[3]["content"]
        assert msgs[4]["role"] == "assistant"
        # The bad/good SVGs should round-trip.
        bad_payload = json.loads(msgs[2]["content"])
        good_payload = json.loads(msgs[4]["content"])
        assert "bad_pyth" in bad_payload["svg"]
        assert "good_pyth" in good_payload["svg"]
        print("OK: sft-corrected emits 5-msg (bad → critique → good) conversation")


def test_dpo_pairs_have_chosen_and_rejected() -> None:
    with tempfile.TemporaryDirectory() as d:
        _seed(Path(d))
        sys.modules.pop("studio.export_finetune", None)
        from studio.export_finetune import export
        out = Path(d) / "out.jsonl"
        stats = export(mode="dpo-pairs", out_path=out)
        rows = _read_jsonl(out)
        assert stats["kept"] == 2, stats
        first = rows[0]
        assert "prompt" in first and "chosen" in first and "rejected" in first
        chosen = json.loads(first["chosen"])
        rejected = json.loads(first["rejected"])
        assert "good" in chosen["svg"]
        assert "bad" in rejected["svg"]
        print("OK: dpo-pairs format = {prompt, chosen, rejected}")


def test_since_cursor_filters_older_rows() -> None:
    with tempfile.TemporaryDirectory() as d:
        _seed(Path(d))
        sys.modules.pop("studio.export_finetune", None)
        from studio.export_finetune import export
        out = Path(d) / "out.jsonl"
        # Export everything first to capture the timestamps.
        stats_all = export(mode="dpo-pairs", out_path=out)
        all_ts = [r["meta"]["ts"] for r in _read_jsonl(out)]
        cutoff = (all_ts[0] + all_ts[1]) / 2  # between the two pairs
        stats_recent = export(mode="dpo-pairs", out_path=out, since_ts=cutoff)
        recent_rows = _read_jsonl(out)
        assert stats_all["kept"] == 2, stats_all
        assert stats_recent["kept"] == 1, stats_recent
        # The remaining row must be the newer one.
        assert recent_rows[0]["meta"]["ts"] > cutoff
        print("OK: --since cursor filters older rows")


if __name__ == "__main__":
    test_sft_clean_filters_retries()
    test_sft_corrected_yields_5_message_conversation()
    test_dpo_pairs_have_chosen_and_rejected()
    test_since_cursor_filters_older_rows()
    print("\nAll export-mode tests passed.")
