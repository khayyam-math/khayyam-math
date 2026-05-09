"""Tests for sevim.telemetry — the SQLite event log behind data
collection, fine-tuning corpus export, and cost guarding."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from sevim.telemetry import Telemetry


@pytest.fixture()
def tel(tmp_path: Path) -> Telemetry:
    return Telemetry(db_path=tmp_path / "t.db")


def test_record_session_and_turn(tel: Telemetry) -> None:
    tel.upsert_session("s1", user_agent="test/1", ip_hash="abc")
    turn_id = tel.record_turn(
        session_id="s1", user_prompt="reduce 3SAT to clique",
        canvas_id="express_xyz", n_phrases=11, retries_used=0,
        cost_usd_estimate=0.05, intent="express",
    )
    assert turn_id is not None
    rows = tel.query("SELECT user_prompt FROM turns WHERE turn_id = ?", (turn_id,))
    assert rows[0][0] == "reduce 3SAT to clique"


def test_session_request_count_and_cost(tel: Telemetry) -> None:
    tel.upsert_session("s1")
    for i in range(3):
        tel.record_turn(
            session_id="s1", user_prompt=f"prompt {i}",
            canvas_id=f"c_{i}", cost_usd_estimate=0.05,
        )
    assert tel.session_request_count("s1", since_s=86400) == 3
    assert abs(tel.session_cost("s1") - 0.15) < 1e-9


def test_refined_within_s_is_backfilled_for_prev_turn(tel: Telemetry) -> None:
    tel.upsert_session("s1")
    tel.record_turn(session_id="s1", user_prompt="A")
    time.sleep(0.05)  # short delay; we just need a measurable gap
    tel.record_turn(session_id="s1", user_prompt="now refine A")
    rows = tel.query(
        "SELECT user_prompt, refined_within_s FROM turns ORDER BY turn_id"
    )
    # First turn now has a refined_within_s value > 0; second turn not yet.
    assert rows[0][0] == "A"
    assert rows[0][1] is not None and rows[0][1] >= 0
    assert rows[1][0] == "now refine A"
    assert rows[1][1] is None


def test_canvas_record(tel: Telemetry) -> None:
    tel.upsert_session("s1")
    tid = tel.record_turn(session_id="s1", user_prompt="P", canvas_id="c1")
    tel.record_canvas(
        canvas_id="c1", session_id="s1", turn_id=tid,
        title="T", svg="<svg/>",
        narration=[{"speak": "hi", "highlight": []}],
    )
    rows = tel.query("SELECT title, svg FROM canvases WHERE canvas_id = ?", ("c1",))
    assert rows[0] == ("T", "<svg/>")


def test_swallows_errors_without_raising(tel: Telemetry) -> None:
    """Telemetry must never raise into the request path.  Force a
    bogus query and verify it logs but doesn't crash."""
    tel._exec("INVALID SQL HERE")  # silently logs
    # Subsequent valid writes still work:
    tel.upsert_session("s2")
    assert tel.session_request_count("s2", since_s=10) == 0
