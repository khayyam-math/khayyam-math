"""Repair-pair capture unit test (no network).

Exercises express_figure with a fake LLM client + fake vision reviewer
to confirm:
  * a fail-then-pass run yields exactly one entry in result['repairs']
  * the entry has the expected (bad_svg, critique, good_svg) fields
  * a first-try-pass yields no repair pairs
  * exhausting max_retries with all fails yields no repair pairs

Then drives the telemetry layer end-to-end: record a repair pair into a
fresh sqlite DB and SELECT it back to verify the row landed.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sevim.telemetry import Telemetry  # noqa: E402
from studio import express  # noqa: E402


def _fake_chat_response(payload: dict) -> object:
    """Build a minimal httpx-Response stand-in."""
    class _R:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(payload)}}]}

        async def aread(self) -> bytes:
            return json.dumps(payload).encode()

        def raise_for_status(self) -> None:
            return None

    return _R()


async def _run_express(
    chat_payloads: list[dict],
    review_verdicts: list[str | None],
) -> dict:
    """Drive express_figure with scripted LLM + reviewer responses."""
    payload_iter = iter(chat_payloads)

    async def fake_post(self, url, headers=None, json=None, **kw):
        return _fake_chat_response(next(payload_iter))

    review_iter = iter(review_verdicts)

    async def fake_review(*args, **kwargs):
        return next(review_iter)

    # Stub PNG conversion so cairosvg isn't required for this unit test.
    # All the SEVIM_*=off flags disable LLM-emitting pipeline steps
    # that would consume payloads from chat_payloads and exhaust
    # the iterator before the test's intended `attempt 0 / attempt
    # 1` calls run.
    #
    #   TEMPLATE_ROUTER     — gpt-4o-mini classifier (one call)
    #   FIGURE_GROUND_TRUTH — Tier-5 ground-truth proposer (one call)
    #   COMPLETENESS_CRITIC — pure-regex, no LLM call, but the
    #                          rubric brief in the system prompt
    #                          could interfere; off for parity with
    #                          the original test setup.
    #   ALGO_TRACE / PROCESS / SYMBOLIC / HOMOM / PANELS /
    #   GRAPHVIZ / MATPLOTLIB / FDL / SEQUENTIAL — every
    #   deterministic route that could fire and steal a payload
    #   before the LLM-SVG attempt loop runs.
    import os
    off_env = {
        "SEVIM_TEMPLATE_ROUTER": "off",
        "SEVIM_FIGURE_GROUND_TRUTH": "0",
        "SEVIM_COMPLETENESS_CRITIC": "off",
        "SEVIM_ALGO_TRACE": "off",
        "SEVIM_PROCESS_ROUTE": "off",
        "SEVIM_SYMBOLIC_ROUTE": "off",
        "SEVIM_HOMOM_ROUTE": "off",
        "SEVIM_PANELS_ROUTE": "off",
        "SEVIM_GRAPHVIZ_ROUTE": "off",
        "SEVIM_MATPLOTLIB_ROUTE": "off",
        "SEVIM_FDL_ROUTE": "off",
        "SEVIM_SEQUENTIAL_ROUTE": "off",
    }
    with patch.object(express, "_svg_to_png", return_value=b"\x89PNG"), \
         patch.object(express, "_vision_review", AsyncMock(side_effect=fake_review)), \
         patch("httpx.AsyncClient.post", new=fake_post), \
         patch.dict(os.environ, off_env):
        return await express.express_figure(
            user_prompt="show me X",
            base_url="http://stub",
            model="stub-model",
            api_key="stub",
            max_retries=2,
        )


def test_repair_pair_captured_on_fail_then_pass() -> None:
    bad = {"svg": "<svg id='bad'/>", "narration": [{"speak": "wrong", "highlight": []}], "title": "T"}
    good = {"svg": "<svg id='good'/>", "narration": [{"speak": "right", "highlight": []}], "title": "T"}
    result = asyncio.run(_run_express(
        chat_payloads=[bad, good],
        review_verdicts=["FAIL: wrong claim", None],
    ))
    assert len(result["repairs"]) == 1, result
    pair = result["repairs"][0]
    assert pair["bad_svg"] == "<svg id='bad'/>", pair
    assert pair["good_svg"] == "<svg id='good'/>", pair
    assert pair["critique"] == "FAIL: wrong claim", pair
    assert pair["bad_narration"] == [{"speak": "wrong", "highlight": []}]
    assert pair["good_narration"] == [{"speak": "right", "highlight": []}]
    print("OK: fail-then-pass produced one repair pair")


def test_no_repair_pair_on_first_try_pass() -> None:
    good = {"svg": "<svg id='good'/>", "narration": [{"speak": "right", "highlight": []}], "title": "T"}
    result = asyncio.run(_run_express(
        chat_payloads=[good],
        review_verdicts=[None],
    ))
    assert result["repairs"] == [], result
    print("OK: first-try-pass produced no repair pairs")


def test_no_repair_pair_when_all_attempts_fail() -> None:
    bad1 = {"svg": "<svg id='b1'/>", "narration": [], "title": "T"}
    bad2 = {"svg": "<svg id='b2'/>", "narration": [], "title": "T"}
    bad3 = {"svg": "<svg id='b3'/>", "narration": [], "title": "T"}
    result = asyncio.run(_run_express(
        chat_payloads=[bad1, bad2, bad3],
        review_verdicts=["FAIL 1", "FAIL 2", "FAIL 3"],
    ))
    assert result["repairs"] == [], result
    assert result["retries_used"] == 2, result
    print("OK: all-fail yields no repair pairs")


def test_telemetry_round_trip() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        tel = Telemetry(db_path=db)
        tel.upsert_session("sess1")
        tel.record_repair_pair(
            session_id="sess1",
            turn_id=42,
            attempt_index=1,
            user_prompt="show me X",
            bad_svg="<svg id='bad'/>",
            bad_narration=[{"speak": "wrong", "highlight": []}],
            critique="FAIL: wrong claim",
            good_svg="<svg id='good'/>",
            good_narration=[{"speak": "right", "highlight": []}],
        )
        rows = tel.query(
            "SELECT session_id, attempt_index, user_prompt, bad_svg, "
            "critique, good_svg FROM repairs WHERE turn_id = ?",
            (42,),
        )
        assert len(rows) == 1, rows
        sess, attempt, prompt, bad_svg, critique, good_svg = rows[0]
        assert sess == "sess1"
        assert attempt == 1
        assert prompt == "show me X"
        assert bad_svg == "<svg id='bad'/>"
        assert critique == "FAIL: wrong claim"
        assert good_svg == "<svg id='good'/>"
        print("OK: telemetry round-trip")


if __name__ == "__main__":
    test_repair_pair_captured_on_fail_then_pass()
    test_no_repair_pair_on_first_try_pass()
    test_no_repair_pair_when_all_attempts_fail()
    test_telemetry_round_trip()
    print("\nAll repair-pair tests passed.")
