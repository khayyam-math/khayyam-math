"""Backend-selection + portable-SQL tests for sevim/telemetry.py.

Exercises the URL routing (file path → SQLite, postgresql:// → Postgres)
and runs a full record/query round trip against SQLite to make sure the
new ON CONFLICT / RETURNING SQL works end-to-end.

Postgres path is import-checked but not actually run here because no
local Postgres is available; the integration test for that lives at
deploy time (tests/test_telemetry_postgres.py, gated on CI env var).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sevim import telemetry as T  # noqa: E402


def test_backend_selection_sqlite_path() -> None:
    b = T._make_backend("/tmp/test.db")
    assert b.driver == "sqlite", b.driver
    print("OK: bare path → sqlite backend")


def test_backend_selection_file_url() -> None:
    with tempfile.TemporaryDirectory() as d:
        url = f"file://{d}/t.db"
        b = T._make_backend(url)
        assert b.driver == "sqlite", b.driver
    print("OK: file:// URL → sqlite backend")


def test_backend_selection_postgres_url() -> None:
    # Don't actually connect (no Postgres locally) — just verify the
    # router picks the Postgres class.  We catch the connection error
    # and check it was raised during psycopg.connect, not during URL
    # routing.
    try:
        T._make_backend("postgresql://no:no@127.0.0.1:1/none")
    except Exception as exc:
        # Expect either a psycopg connection error OR ImportError if
        # psycopg isn't installed in this venv.  Both are fine; what
        # matters is we got past the routing.
        assert (
            "psycopg" in str(exc).lower()
            or "OperationalError" in type(exc).__name__
            or "Connection" in type(exc).__name__
            or "could not" in str(exc).lower()
        ), repr(exc)
        print("OK: postgresql:// URL routed to Postgres backend (connect failed as expected)")
        return
    raise AssertionError("expected connection error against fake Postgres host")


def test_full_round_trip_sqlite() -> None:
    """Drive every public method through a fresh DB to confirm the
    portable SQL (RETURNING, ON CONFLICT … DO UPDATE) works end-to-end."""
    with tempfile.TemporaryDirectory() as d:
        tel = T.Telemetry(db_path=Path(d) / "t.db")

        # session
        tel.upsert_session("s1", user_agent="ua1", ip_hash="h1")
        # again — must update via ON CONFLICT, not crash
        tel.upsert_session("s1", note="hello")

        # turn
        turn_id = tel.record_turn(
            session_id="s1",
            user_prompt="show X",
            canvas_id="c1",
            n_phrases=4,
            retries_used=0,
            duration_s=1.2,
            cost_usd_estimate=0.05,
            intent="express",
        )
        assert turn_id is not None and turn_id > 0, f"got turn_id={turn_id}"

        # canvas (insert + replace)
        tel.record_canvas("c1", "s1", turn_id, "title", "<svg/>", [])
        tel.record_canvas("c1", "s1", turn_id, "title-v2", "<svg id='x'/>", [])
        rows = tel.query(
            "SELECT title, svg FROM canvases WHERE canvas_id = ?", ("c1",),
        )
        assert rows[0][0] == "title-v2", rows
        assert rows[0][1] == "<svg id='x'/>", rows

        # repair pair
        tel.record_repair_pair(
            session_id="s1",
            turn_id=turn_id,
            attempt_index=1,
            user_prompt="show X",
            bad_svg="<svg id='bad'/>",
            bad_narration=[{"speak": "wrong", "highlight": []}],
            critique="FAIL: wrong",
            good_svg="<svg id='good'/>",
            good_narration=[{"speak": "right", "highlight": []}],
        )
        rows = tel.query(
            "SELECT critique, good_svg FROM repairs WHERE turn_id = ?",
            (turn_id,),
        )
        assert rows[0][0] == "FAIL: wrong", rows
        assert rows[0][1] == "<svg id='good'/>", rows

        # cost / count helpers
        assert tel.session_cost("s1") >= 0.05
        assert tel.session_request_count("s1", 86400) == 1

        # second turn — should backfill refined_within_s on the previous
        tel.record_turn(session_id="s1", user_prompt="refine that", intent="express")
        rows = tel.query(
            "SELECT refined_within_s FROM turns WHERE turn_id = ?",
            (turn_id,),
        )
        assert rows[0][0] is not None, rows  # backfill worked

    print("OK: full round trip on SQLite (RETURNING + ON CONFLICT DO UPDATE work)")


if __name__ == "__main__":
    test_backend_selection_sqlite_path()
    test_backend_selection_file_url()
    test_backend_selection_postgres_url()
    test_full_round_trip_sqlite()
    print("\nAll telemetry-backend tests passed.")
