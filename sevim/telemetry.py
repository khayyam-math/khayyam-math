"""SQLite-backed telemetry log for Sevim.

Captures every user turn and every figure produced so we can:
  * Mine the data for prompt-improvement signals (what kinds of
    requests does gpt-4o struggle with?  what do users have to refine?)
  * Build a fine-tuning corpus for a smaller self-hosted model.
  * Detect abuse / cost runaway in production.

Schema mirrors what we'll deploy on RDS Postgres in AWS, so the
migration is a driver swap, not a rewrite.

Off by default in dev: set ``SEVIM_TELEMETRY=1`` to enable.
DB location: ``$SEVIM_TELEMETRY_DB`` (default
``~/.local/share/sevim/telemetry.db``).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    user_agent   TEXT,
    ip_hash      TEXT,
    request_count INTEGER NOT NULL DEFAULT 0,
    cost_usd_estimate REAL NOT NULL DEFAULT 0.0,
    note TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT NOT NULL,
    timestamp          REAL NOT NULL,
    user_prompt        TEXT NOT NULL,
    canvas_id          TEXT,
    prior_canvas_ids   TEXT,
    n_phrases          INTEGER,
    retries_used       INTEGER,
    review_history     TEXT,
    duration_s         REAL,
    cost_usd_estimate  REAL,
    refined_within_s   REAL,
    intent             TEXT,
    error              TEXT
);

CREATE TABLE IF NOT EXISTS canvases (
    canvas_id        TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    turn_id          INTEGER,
    timestamp        REAL NOT NULL,
    title            TEXT,
    svg              TEXT,
    narration_json   TEXT,
    accepted         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_canvases_session ON canvases (session_id, timestamp);
"""


def default_db_path() -> Path:
    base = os.environ.get("SEVIM_TELEMETRY_DB")
    if base:
        return Path(base)
    return Path.home() / ".local/share/sevim/telemetry.db"


def is_enabled() -> bool:
    return os.environ.get("SEVIM_TELEMETRY", "0") not in ("0", "", "false", "no")


class Telemetry:
    """Thread-safe SQLite telemetry logger.

    Designed so writes never raise into the request path: any DB error
    is logged and swallowed.  Telemetry being broken must not break
    Sevim's user-facing pipeline.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path, check_same_thread=False, timeout=5.0,
        )
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- low-level ----------

    def _exec(self, sql: str, params: tuple = ()) -> None:
        try:
            with self._lock:
                self._conn.execute(sql, params)
                self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            import sys
            print(f"[telemetry] write failed (silent): {exc}",
                  flush=True, file=sys.stderr)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    # ---------- public API ----------

    def upsert_session(
        self,
        session_id: str,
        user_agent: str | None = None,
        ip_hash: str | None = None,
        note: str | None = None,
    ) -> None:
        now = time.time()
        self._exec(
            """
            INSERT INTO sessions (session_id, created_at, last_seen_at,
                                  user_agent, ip_hash, note)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                user_agent   = COALESCE(sessions.user_agent, excluded.user_agent),
                ip_hash      = COALESCE(sessions.ip_hash,    excluded.ip_hash)
            """,
            (session_id, now, now, user_agent, ip_hash, note),
        )

    def record_turn(
        self,
        session_id: str,
        user_prompt: str,
        canvas_id: str | None = None,
        prior_canvas_ids: list[str] | None = None,
        n_phrases: int | None = None,
        retries_used: int | None = None,
        review_history: list[str] | None = None,
        duration_s: float | None = None,
        cost_usd_estimate: float | None = None,
        intent: str | None = None,
        error: str | None = None,
    ) -> int | None:
        """Record one user turn; return its turn_id (or None on failure)."""
        try:
            with self._lock:
                cur = self._conn.execute(
                    """
                    INSERT INTO turns (session_id, timestamp, user_prompt,
                                       canvas_id, prior_canvas_ids,
                                       n_phrases, retries_used, review_history,
                                       duration_s, cost_usd_estimate,
                                       intent, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id, time.time(), user_prompt,
                        canvas_id,
                        json.dumps(prior_canvas_ids or []),
                        n_phrases, retries_used,
                        json.dumps(review_history or []),
                        duration_s, cost_usd_estimate,
                        intent, error,
                    ),
                )
                turn_id = cur.lastrowid
                # Bump session counters under the same lock.
                self._conn.execute(
                    """
                    UPDATE sessions
                       SET request_count      = request_count + 1,
                           last_seen_at       = ?,
                           cost_usd_estimate  = cost_usd_estimate + COALESCE(?, 0)
                     WHERE session_id = ?
                    """,
                    (time.time(), cost_usd_estimate or 0.0, session_id),
                )
                self._conn.commit()
            # After commit: backfill the previous turn's `refined_within_s`
            # by computing the gap between this turn and the previous one
            # for the same session.  Do it in a separate statement to keep
            # the hot insert path tight.
            self._backfill_refined_within(session_id, turn_id)
            return turn_id
        except Exception as exc:  # noqa: BLE001
            import sys
            print(f"[telemetry] record_turn failed (silent): {exc}",
                  flush=True, file=sys.stderr)
            return None

    def _backfill_refined_within(self, session_id: str, current_turn_id: int) -> None:
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT turn_id, timestamp FROM turns
                     WHERE session_id = ?
                       AND turn_id < ?
                     ORDER BY turn_id DESC
                     LIMIT 1
                    """,
                    (session_id, current_turn_id),
                ).fetchall()
                if not rows:
                    return
                prev_id, prev_ts = rows[0]
                cur_ts = self._conn.execute(
                    "SELECT timestamp FROM turns WHERE turn_id = ?",
                    (current_turn_id,),
                ).fetchone()[0]
                self._conn.execute(
                    "UPDATE turns SET refined_within_s = ? WHERE turn_id = ?",
                    (cur_ts - prev_ts, prev_id),
                )
                self._conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def record_canvas(
        self,
        canvas_id: str,
        session_id: str,
        turn_id: int | None,
        title: str | None,
        svg: str | None,
        narration: list[dict] | None,
    ) -> None:
        self._exec(
            """
            INSERT OR REPLACE INTO canvases
                (canvas_id, session_id, turn_id, timestamp,
                 title, svg, narration_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canvas_id, session_id, turn_id, time.time(),
                title, svg, json.dumps(narration or []),
            ),
        )

    def session_cost(self, session_id: str, since_s: float = 86400.0) -> float:
        """Sum of cost_usd_estimate for this session in the last `since_s`
        seconds.  Used by the cost guard."""
        cutoff = time.time() - since_s
        rows = self.query(
            """
            SELECT COALESCE(SUM(cost_usd_estimate), 0)
              FROM turns
             WHERE session_id = ? AND timestamp >= ?
            """,
            (session_id, cutoff),
        )
        return float(rows[0][0]) if rows else 0.0

    def session_request_count(self, session_id: str, since_s: float) -> int:
        cutoff = time.time() - since_s
        rows = self.query(
            "SELECT COUNT(*) FROM turns WHERE session_id = ? AND timestamp >= ?",
            (session_id, cutoff),
        )
        return int(rows[0][0]) if rows else 0


# ---------------------------------------------------------------------------
# Singleton instance — created on first use, dormant until enabled.
# ---------------------------------------------------------------------------

_INSTANCE: Telemetry | None = None
_INSTANCE_LOCK = threading.Lock()


def get_telemetry() -> Telemetry | None:
    """Return the global telemetry singleton, or None if disabled."""
    if not is_enabled():
        return None
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = Telemetry()
    return _INSTANCE


def reset_for_tests(db_path: Path | None = None) -> Telemetry:
    """Test helper: force a fresh Telemetry instance pointed at a fresh DB."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is not None:
            try:
                _INSTANCE._conn.close()
            except Exception:
                pass
        _INSTANCE = Telemetry(db_path=db_path)
    return _INSTANCE
