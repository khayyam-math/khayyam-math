"""Telemetry log for Sevim — SQLite locally, Postgres on AWS.

Captures every user turn and every figure produced so we can:
  * Mine the data for prompt-improvement signals (what kinds of
    requests does gpt-4o struggle with?  what do users have to refine?)
  * Build a fine-tuning corpus for a smaller self-hosted model
    (especially the (bad → critique → good) repair triples produced
    by the math-correctness inspector retry loop).
  * Detect abuse / cost runaway in production.

Backend selection is controlled by ``SEVIM_TELEMETRY_DB``:

  * Anything that doesn't look like a URL, or starts with ``file://``,
    is treated as a SQLite file path.  Default:
    ``~/.local/share/sevim/telemetry.db``.
  * ``postgresql://user:pass@host:port/dbname`` (or ``postgres://``)
    routes to a psycopg backend.  Used in AWS deploys (RDS).

Off by default in dev: set ``SEVIM_TELEMETRY=1`` to enable.

The ``Telemetry`` class is a thin facade.  All driver-specific work
lives in the backend implementations so adding a new backend is a
single class.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Schemas — separate per backend because of PK / autoincrement syntax.
# Everything else stays portable (TEXT, INTEGER, REAL, ON CONFLICT, RETURNING).
# ---------------------------------------------------------------------------

_SCHEMA_SQLITE = """
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

CREATE TABLE IF NOT EXISTS repairs (
    repair_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id            INTEGER,
    session_id         TEXT NOT NULL,
    timestamp          REAL NOT NULL,
    attempt_index      INTEGER NOT NULL,
    user_prompt        TEXT,
    bad_svg            TEXT,
    bad_narration_json TEXT,
    critique           TEXT,
    good_svg           TEXT,
    good_narration_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_canvases_session ON canvases (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_repairs_session ON repairs (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_repairs_turn ON repairs (turn_id);
"""

_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    created_at   DOUBLE PRECISION NOT NULL,
    last_seen_at DOUBLE PRECISION NOT NULL,
    user_agent   TEXT,
    ip_hash      TEXT,
    request_count INTEGER NOT NULL DEFAULT 0,
    cost_usd_estimate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    note TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id            BIGSERIAL PRIMARY KEY,
    session_id         TEXT NOT NULL,
    timestamp          DOUBLE PRECISION NOT NULL,
    user_prompt        TEXT NOT NULL,
    canvas_id          TEXT,
    prior_canvas_ids   TEXT,
    n_phrases          INTEGER,
    retries_used       INTEGER,
    review_history     TEXT,
    duration_s         DOUBLE PRECISION,
    cost_usd_estimate  DOUBLE PRECISION,
    refined_within_s   DOUBLE PRECISION,
    intent             TEXT,
    error              TEXT
);

CREATE TABLE IF NOT EXISTS canvases (
    canvas_id        TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    turn_id          BIGINT,
    timestamp        DOUBLE PRECISION NOT NULL,
    title            TEXT,
    svg              TEXT,
    narration_json   TEXT,
    accepted         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS repairs (
    repair_id          BIGSERIAL PRIMARY KEY,
    turn_id            BIGINT,
    session_id         TEXT NOT NULL,
    timestamp          DOUBLE PRECISION NOT NULL,
    attempt_index      INTEGER NOT NULL,
    user_prompt        TEXT,
    bad_svg            TEXT,
    bad_narration_json TEXT,
    critique           TEXT,
    good_svg           TEXT,
    good_narration_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_canvases_session ON canvases (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_repairs_session ON repairs (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_repairs_turn ON repairs (turn_id);
"""


# ---------------------------------------------------------------------------
# Backends — SQLite (default) and Postgres (RDS).  Both speak the same ?-
# placeholder SQL; the Postgres backend translates to %s at the boundary.
# ---------------------------------------------------------------------------

class _SqliteBackend:
    driver = "sqlite"
    schema = _SCHEMA_SQLITE

    def __init__(self, db_url: str) -> None:
        # db_url is either a plain path or file://path
        if db_url.startswith("file://"):
            path = Path(urlparse(db_url).path)
        else:
            path = Path(db_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            path, check_same_thread=False, timeout=5.0,
        )

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def executescript(self, sql: str) -> None:
        self._conn.executescript(sql)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass


class _PostgresBackend:
    driver = "postgres"
    schema = _SCHEMA_POSTGRES

    def __init__(self, db_url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "psycopg not installed.  Install with: pip install 'sevim[aws]' "
                "(or pip install psycopg[binary])"
            ) from exc
        # autocommit=False so we control transactions; we commit() after
        # each unit of work the way the SQLite backend does.
        self._psycopg = psycopg
        self._conn = psycopg.connect(db_url, autocommit=False)

    def execute(self, sql: str, params: tuple = ()) -> Any:
        # psycopg uses %s placeholders; our SQL is written with ?.  Translate.
        # Our schema/queries never embed '?' inside string literals, so a
        # straight replace is safe; the day that changes, switch to a
        # quote-aware tokeniser.
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def executescript(self, sql: str) -> None:
        # psycopg has no executescript; split on `;` works for our DDL
        # because it doesn't embed semicolons inside literals.
        cur = self._conn.cursor()
        for stmt in sql.split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s)
        cur.close()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass


def _make_backend(db_url: str) -> _SqliteBackend | _PostgresBackend:
    """Pick a backend from the URL scheme."""
    scheme = urlparse(db_url).scheme.lower() if "://" in db_url else ""
    if scheme in ("postgresql", "postgres"):
        return _PostgresBackend(db_url)
    return _SqliteBackend(db_url)


# ---------------------------------------------------------------------------
# Public helpers — preserve the previous module surface.
# ---------------------------------------------------------------------------

def default_db_path() -> Path:
    """Backwards-compat helper for callers that want the SQLite default."""
    base = os.environ.get("SEVIM_TELEMETRY_DB")
    if base and "://" not in base:
        return Path(base)
    if base and base.startswith("file://"):
        return Path(urlparse(base).path)
    # Postgres URL or unset → fall through to the local SQLite default.
    return Path.home() / ".local/share/sevim/telemetry.db"


def _resolved_db_url() -> str:
    """Return the configured db URL (or the SQLite default path as a string)."""
    return os.environ.get("SEVIM_TELEMETRY_DB") or str(
        Path.home() / ".local/share/sevim/telemetry.db"
    )


def is_enabled() -> bool:
    return os.environ.get("SEVIM_TELEMETRY", "0") not in ("0", "", "false", "no")


# ---------------------------------------------------------------------------
# Telemetry — driver-agnostic facade.  All SQL uses ? placeholders; the
# Postgres backend translates to %s.  RETURNING and ON CONFLICT are used
# everywhere because both backends support them.
# ---------------------------------------------------------------------------

class Telemetry:
    """Thread-safe telemetry logger with pluggable backend.

    Writes never raise into the request path: any DB error is logged
    and swallowed.  Telemetry being broken must not break Sevim's
    user-facing pipeline.
    """

    def __init__(self, db_path: Path | None = None, db_url: str | None = None) -> None:
        # Preserve the old (db_path) signature so callers that do
        # `Telemetry(db_path=tmp)` still work.  When both are passed,
        # db_url wins.
        if db_url is None:
            db_url = str(db_path) if db_path is not None else _resolved_db_url()
        self.db_url = db_url
        self._lock = threading.Lock()
        self._backend = _make_backend(db_url)
        self._backend.executescript(self._backend.schema)
        self._backend.commit()
        # Keep the SQLite-style attribute alive for older callers (tests,
        # debug scripts) that touched `tel.db_path` directly.
        if isinstance(self._backend, _SqliteBackend):
            self.db_path: Path | None = Path(
                urlparse(db_url).path if db_url.startswith("file://") else db_url
            )
        else:
            self.db_path = None

    # ---------- low-level ----------

    def _exec(self, sql: str, params: tuple = ()) -> None:
        try:
            with self._lock:
                self._backend.execute(sql, params)
                self._backend.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"[telemetry] write failed (silent): {exc}",
                  flush=True, file=sys.stderr)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self._backend.execute(sql, params)
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
        # ON CONFLICT … DO UPDATE — supported by SQLite ≥3.24 and Postgres.
        self._exec(
            """
            INSERT INTO sessions (session_id, created_at, last_seen_at,
                                  user_agent, ip_hash, note)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                user_agent   = COALESCE(sessions.user_agent, EXCLUDED.user_agent),
                ip_hash      = COALESCE(sessions.ip_hash,    EXCLUDED.ip_hash)
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
                # RETURNING works in both SQLite (≥3.35, shipped with
                # cpython 3.10+) and Postgres.
                cur = self._backend.execute(
                    """
                    INSERT INTO turns (session_id, timestamp, user_prompt,
                                       canvas_id, prior_canvas_ids,
                                       n_phrases, retries_used, review_history,
                                       duration_s, cost_usd_estimate,
                                       intent, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING turn_id
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
                row = cur.fetchone()
                turn_id = int(row[0]) if row else None
                # Bump session counters under the same lock.
                self._backend.execute(
                    """
                    UPDATE sessions
                       SET request_count      = request_count + 1,
                           last_seen_at       = ?,
                           cost_usd_estimate  = cost_usd_estimate + COALESCE(?, 0)
                     WHERE session_id = ?
                    """,
                    (time.time(), cost_usd_estimate or 0.0, session_id),
                )
                self._backend.commit()
            if turn_id is not None:
                self._backfill_refined_within(session_id, turn_id)
            return turn_id
        except Exception as exc:  # noqa: BLE001
            print(f"[telemetry] record_turn failed (silent): {exc}",
                  flush=True, file=sys.stderr)
            return None

    def _backfill_refined_within(self, session_id: str, current_turn_id: int) -> None:
        try:
            with self._lock:
                cur = self._backend.execute(
                    """
                    SELECT turn_id, timestamp FROM turns
                     WHERE session_id = ?
                       AND turn_id < ?
                     ORDER BY turn_id DESC
                     LIMIT 1
                    """,
                    (session_id, current_turn_id),
                )
                rows = cur.fetchall()
                if not rows:
                    return
                prev_id, prev_ts = rows[0]
                cur = self._backend.execute(
                    "SELECT timestamp FROM turns WHERE turn_id = ?",
                    (current_turn_id,),
                )
                cur_ts_row = cur.fetchone()
                if not cur_ts_row:
                    return
                cur_ts = cur_ts_row[0]
                self._backend.execute(
                    "UPDATE turns SET refined_within_s = ? WHERE turn_id = ?",
                    (cur_ts - prev_ts, prev_id),
                )
                self._backend.commit()
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
        # ON CONFLICT (canvas_id) DO UPDATE replaces SQLite-specific
        # `INSERT OR REPLACE`; portable to Postgres.
        self._exec(
            """
            INSERT INTO canvases (canvas_id, session_id, turn_id, timestamp,
                                  title, svg, narration_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (canvas_id) DO UPDATE SET
                session_id     = EXCLUDED.session_id,
                turn_id        = EXCLUDED.turn_id,
                timestamp      = EXCLUDED.timestamp,
                title          = EXCLUDED.title,
                svg            = EXCLUDED.svg,
                narration_json = EXCLUDED.narration_json
            """,
            (
                canvas_id, session_id, turn_id, time.time(),
                title, svg, json.dumps(narration or []),
            ),
        )

    def record_repair_pair(
        self,
        session_id: str,
        turn_id: int | None,
        attempt_index: int,
        user_prompt: str | None,
        bad_svg: str | None,
        bad_narration: list[dict] | None,
        critique: str | None,
        good_svg: str | None,
        good_narration: list[dict] | None,
    ) -> None:
        """Persist a (failed_attempt, critique, corrected_attempt) triple.

        These are the highest-value distillation pairs: the reviewer's
        critique is the explicit reasoning bridge the corrected version
        applied.  Used by export_finetune.py to emit DPO preference data
        and SFT-with-critique training examples.
        """
        self._exec(
            """
            INSERT INTO repairs (turn_id, session_id, timestamp,
                                 attempt_index, user_prompt,
                                 bad_svg, bad_narration_json,
                                 critique,
                                 good_svg, good_narration_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id, session_id, time.time(),
                attempt_index, user_prompt,
                bad_svg, json.dumps(bad_narration or []),
                critique,
                good_svg, json.dumps(good_narration or []),
            ),
        )

    def session_cost(self, session_id: str, since_s: float = 86400.0) -> float:
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
                _INSTANCE._backend.close()
            except Exception:  # noqa: BLE001
                pass
        _INSTANCE = Telemetry(db_path=db_path)
    return _INSTANCE
