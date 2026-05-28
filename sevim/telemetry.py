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
    country      TEXT,
    region       TEXT,
    city         TEXT,
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
    error              TEXT,
    model_id           TEXT NOT NULL DEFAULT 'gpt-4o'
);

CREATE TABLE IF NOT EXISTS canvases (
    canvas_id        TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    turn_id          INTEGER,
    timestamp        REAL NOT NULL,
    title            TEXT,
    svg              TEXT,
    narration_json   TEXT,
    math_claims_json TEXT,
    accepted         INTEGER NOT NULL DEFAULT 0,
    model_id         TEXT NOT NULL DEFAULT 'gpt-4o'
);

-- One row per (canvas, claim_idx).  Populated by the OFFLINE
-- Lean+Mathlib catalog verifier (studio/catalog_verifier.py).
-- Status: 'verified' (Lean kernel accepted) | 'failed' (kernel
-- rejected) | 'unsupported' (translator couldn't formalise) |
-- 'timeout' | 'queued' (not yet attempted).
CREATE TABLE IF NOT EXISTS lean_verifications (
    canvas_id        TEXT NOT NULL,
    claim_idx        INTEGER NOT NULL,
    claim_a          TEXT,
    claim_b          TEXT,
    claim_kind       TEXT,
    status           TEXT NOT NULL DEFAULT 'queued',
    lean_source      TEXT,
    lean_output      TEXT,
    duration_s       REAL,
    verified_at      REAL,
    PRIMARY KEY (canvas_id, claim_idx)
);

-- User-reported figure problems captured via the "Not quite right?"
-- button.  Pure capture — NO live regeneration happens.  Admins
-- review accumulated reports, ship a backend fix (prompt update,
-- new template, post-processor), and mark items resolved with the
-- commit SHA so future reports referencing the same complaint can
-- be flagged as 'recurrence after fix'.
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp          REAL NOT NULL,
    session_id         TEXT,
    user_email         TEXT,
    canvas_id          TEXT,
    original_prompt    TEXT,
    user_description   TEXT NOT NULL,
    git_sha            TEXT,
    model_id           TEXT,
    status             TEXT NOT NULL DEFAULT 'open',
    resolved_at        REAL,
    resolved_sha       TEXT,
    resolution_note    TEXT
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
    good_narration_json TEXT,
    model_id           TEXT NOT NULL DEFAULT 'gpt-4o'
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS users (
    email                 TEXT PRIMARY KEY,
    first_seen_at         REAL NOT NULL,
    last_seen_at          REAL NOT NULL,
    login_count           INTEGER NOT NULL DEFAULT 1,
    last_login_ip_hash    TEXT,
    last_login_country    TEXT,
    last_login_region     TEXT,
    last_login_city       TEXT
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_canvases_session ON canvases (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_repairs_session ON repairs (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_repairs_turn ON repairs (turn_id);
CREATE INDEX IF NOT EXISTS idx_users_country ON users (last_login_country);
"""

_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    created_at   DOUBLE PRECISION NOT NULL,
    last_seen_at DOUBLE PRECISION NOT NULL,
    user_agent   TEXT,
    ip_hash      TEXT,
    country      TEXT,
    region       TEXT,
    city         TEXT,
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
    error              TEXT,
    model_id           TEXT NOT NULL DEFAULT 'gpt-4o'
);

CREATE TABLE IF NOT EXISTS canvases (
    canvas_id        TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    turn_id          BIGINT,
    timestamp        DOUBLE PRECISION NOT NULL,
    title            TEXT,
    svg              TEXT,
    narration_json   TEXT,
    math_claims_json TEXT,
    accepted         INTEGER NOT NULL DEFAULT 0,
    model_id         TEXT NOT NULL DEFAULT 'gpt-4o'
);

CREATE TABLE IF NOT EXISTS lean_verifications (
    canvas_id        TEXT NOT NULL,
    claim_idx        INTEGER NOT NULL,
    claim_a          TEXT,
    claim_b          TEXT,
    claim_kind       TEXT,
    status           TEXT NOT NULL DEFAULT 'queued',
    lean_source      TEXT,
    lean_output      TEXT,
    duration_s       DOUBLE PRECISION,
    verified_at      DOUBLE PRECISION,
    PRIMARY KEY (canvas_id, claim_idx)
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id        BIGSERIAL PRIMARY KEY,
    timestamp          DOUBLE PRECISION NOT NULL,
    session_id         TEXT,
    user_email         TEXT,
    canvas_id          TEXT,
    original_prompt    TEXT,
    user_description   TEXT NOT NULL,
    git_sha            TEXT,
    model_id           TEXT,
    status             TEXT NOT NULL DEFAULT 'open',
    resolved_at        DOUBLE PRECISION,
    resolved_sha       TEXT,
    resolution_note    TEXT
);

CREATE INDEX IF NOT EXISTS idx_feedback_status_ts
    ON feedback (status, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_canvas
    ON feedback (canvas_id);

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
    good_narration_json TEXT,
    model_id           TEXT NOT NULL DEFAULT 'gpt-4o'
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    updated_by TEXT
);

-- Additive migration for pre-existing deployments (Postgres-only;
-- SQLite developer DBs get the column at CREATE-time above). All
-- historical rows backfill to 'gpt-4o' (the only backend used to date).
ALTER TABLE turns    ADD COLUMN IF NOT EXISTS model_id TEXT NOT NULL DEFAULT 'gpt-4o';
ALTER TABLE canvases ADD COLUMN IF NOT EXISTS model_id TEXT NOT NULL DEFAULT 'gpt-4o';
ALTER TABLE repairs  ADD COLUMN IF NOT EXISTS model_id TEXT NOT NULL DEFAULT 'gpt-4o';
-- Phase C (Lean+Mathlib offline catalog verifier) — store math
-- claims alongside the canvas so the offline service can read them.
ALTER TABLE canvases ADD COLUMN IF NOT EXISTS math_claims_json TEXT;
-- Anonymous-visitor geo (populated at session creation via GeoLite2).
-- Backfills NULL for existing rows; new rows get the geo when the
-- session is upserted with an IP that GeoLite2 can resolve.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS country TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS region  TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS city    TEXT;

CREATE TABLE IF NOT EXISTS users (
    email                 TEXT PRIMARY KEY,
    first_seen_at         DOUBLE PRECISION NOT NULL,
    last_seen_at          DOUBLE PRECISION NOT NULL,
    login_count           INTEGER NOT NULL DEFAULT 1,
    last_login_ip_hash    TEXT,
    last_login_country    TEXT,
    last_login_region     TEXT,
    last_login_city       TEXT
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_canvases_session ON canvases (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_repairs_session ON repairs (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_repairs_turn ON repairs (turn_id);
CREATE INDEX IF NOT EXISTS idx_turns_model ON turns (model_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_users_country ON users (last_login_country);
CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_country ON sessions (country);
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
        country: str | None = None,
        region: str | None = None,
        city: str | None = None,
    ) -> None:
        now = time.time()
        # ON CONFLICT … DO UPDATE — supported by SQLite ≥3.24 and Postgres.
        # Geo columns only get filled if previously NULL, so we never
        # overwrite an earlier resolved value with NULL from a later
        # request whose IP didn't resolve (private range, missing
        # mmdb, etc.).
        self._exec(
            """
            INSERT INTO sessions (session_id, created_at, last_seen_at,
                                  user_agent, ip_hash, country, region,
                                  city, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                user_agent   = COALESCE(sessions.user_agent, EXCLUDED.user_agent),
                ip_hash      = COALESCE(sessions.ip_hash,    EXCLUDED.ip_hash),
                country      = COALESCE(sessions.country,    EXCLUDED.country),
                region       = COALESCE(sessions.region,     EXCLUDED.region),
                city         = COALESCE(sessions.city,       EXCLUDED.city)
            """,
            (session_id, now, now, user_agent, ip_hash,
             country, region, city, note),
        )

    def record_login(
        self,
        email: str,
        ip_hash: str | None = None,
        country: str | None = None,
        region: str | None = None,
        city: str | None = None,
    ) -> None:
        """Upsert a row in the users table on every successful login.

        New row: first_seen_at = now, login_count = 1.
        Existing row: bumps last_seen_at + login_count, refreshes geo with
        the most recent values (only when non-NULL, so a failed lookup
        doesn't wipe the previous reading).
        """
        if not email:
            return
        now = time.time()
        self._exec(
            """
            INSERT INTO users (email, first_seen_at, last_seen_at, login_count,
                               last_login_ip_hash, last_login_country,
                               last_login_region, last_login_city)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                last_seen_at       = EXCLUDED.last_seen_at,
                login_count        = users.login_count + 1,
                last_login_ip_hash = COALESCE(EXCLUDED.last_login_ip_hash, users.last_login_ip_hash),
                last_login_country = COALESCE(EXCLUDED.last_login_country, users.last_login_country),
                last_login_region  = COALESCE(EXCLUDED.last_login_region,  users.last_login_region),
                last_login_city    = COALESCE(EXCLUDED.last_login_city,    users.last_login_city)
            """,
            (email, now, now, ip_hash, country, region, city),
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
        model_id: str = "gpt-4o",
    ) -> int | None:
        """Record one user turn; return its turn_id (or None on failure).

        ``model_id`` tags the row with the backend that produced it
        (``gpt-4o``, ``qwen_lora_v4``, ``qwen_base``, …) so the
        finetuning exporter can filter per-model when assembling
        future training corpora.
        """
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
                                       intent, error, model_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING turn_id
                    """,
                    (
                        session_id, time.time(), user_prompt,
                        canvas_id,
                        json.dumps(prior_canvas_ids or []),
                        n_phrases, retries_used,
                        json.dumps(review_history or []),
                        duration_s, cost_usd_estimate,
                        intent, error, model_id,
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

    def record_feedback(
        self,
        user_description: str,
        *,
        session_id: str | None = None,
        user_email: str | None = None,
        canvas_id: str | None = None,
        original_prompt: str | None = None,
        git_sha: str | None = None,
        model_id: str | None = None,
    ) -> None:
        """Record a "Not quite right?" report.  Pure capture — does NOT
        trigger any regeneration.  Admins review and mark resolved."""
        self._exec(
            """
            INSERT INTO feedback
                (timestamp, session_id, user_email, canvas_id,
                 original_prompt, user_description, git_sha, model_id,
                 status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (time.time(), session_id, user_email, canvas_id,
             original_prompt, user_description, git_sha, model_id),
        )

    def record_canvas(
        self,
        canvas_id: str,
        session_id: str,
        turn_id: int | None,
        title: str | None,
        svg: str | None,
        narration: list[dict] | None,
        model_id: str = "gpt-4o",
        math_claims: list[dict] | None = None,
    ) -> None:
        # ON CONFLICT (canvas_id) DO UPDATE replaces SQLite-specific
        # `INSERT OR REPLACE`; portable to Postgres.
        self._exec(
            """
            INSERT INTO canvases (canvas_id, session_id, turn_id, timestamp,
                                  title, svg, narration_json, math_claims_json,
                                  model_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (canvas_id) DO UPDATE SET
                session_id       = EXCLUDED.session_id,
                turn_id          = EXCLUDED.turn_id,
                timestamp        = EXCLUDED.timestamp,
                title            = EXCLUDED.title,
                svg              = EXCLUDED.svg,
                narration_json   = EXCLUDED.narration_json,
                math_claims_json = EXCLUDED.math_claims_json,
                model_id         = EXCLUDED.model_id
            """,
            (
                canvas_id, session_id, turn_id, time.time(),
                title, svg, json.dumps(narration or []),
                json.dumps(math_claims or []) if math_claims else None,
                model_id,
            ),
        )
        # Enqueue each claim for the offline Lean+Mathlib catalog
        # verifier.  Status starts as 'queued'; the verifier picks
        # them up and updates in-place.
        for idx, claim in enumerate(math_claims or []):
            try:
                self._exec(
                    """
                    INSERT INTO lean_verifications
                        (canvas_id, claim_idx, claim_a, claim_b,
                         claim_kind, status)
                    VALUES (?, ?, ?, ?, ?, 'queued')
                    ON CONFLICT (canvas_id, claim_idx) DO NOTHING
                    """,
                    (canvas_id, idx,
                     str(claim.get("a", "")),
                     str(claim.get("b", "")),
                     str(claim.get("kind", ""))),
                )
            except Exception:  # noqa: BLE001
                pass   # telemetry failures must never break canvas write

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
        model_id: str = "gpt-4o",
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
                                 good_svg, good_narration_json,
                                 model_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id, session_id, time.time(),
                attempt_index, user_prompt,
                bad_svg, json.dumps(bad_narration or []),
                critique,
                good_svg, json.dumps(good_narration or []),
                model_id,
            ),
        )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Read a server-side setting (e.g.\\ active model selection).

        Returns ``default`` (or None) when the key isn't set.  Used by
        the admin page to persist operator choices across task restarts.
        """
        try:
            cur = self._backend.execute(
                "SELECT value FROM settings WHERE key = ?", (key,),
            )
            row = cur.fetchone()
            return row[0] if row else default
        except Exception as exc:  # noqa: BLE001
            print(f"[telemetry] get_setting({key!r}) failed: {exc}",
                  flush=True, file=sys.stderr)
            return default

    def set_setting(self, key: str, value: str,
                    updated_by: str | None = None) -> None:
        """Upsert a server-side setting; ``updated_by`` is the operator
        e-mail recorded for auditability."""
        try:
            with self._lock:
                self._backend.execute(
                    """
                    INSERT INTO settings (key, value, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (key) DO UPDATE SET
                        value      = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at,
                        updated_by = EXCLUDED.updated_by
                    """,
                    (key, value, time.time(), updated_by),
                )
                self._backend.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"[telemetry] set_setting({key!r}) failed: {exc}",
                  flush=True, file=sys.stderr)

    def usage_by_model(self, since_s: float = 86400.0) -> list[dict]:
        """Aggregate turns by ``model_id`` over the last ``since_s``
        seconds (default 24 h).  Returns a list of dicts with
        ``model_id, turns, avg_duration_s, avg_retries, total_cost``.
        """
        cutoff = time.time() - since_s
        try:
            cur = self._backend.execute(
                """
                SELECT model_id,
                       COUNT(*),
                       COALESCE(AVG(duration_s), 0),
                       COALESCE(AVG(CAST(retries_used AS REAL)), 0),
                       COALESCE(SUM(cost_usd_estimate), 0),
                       COALESCE(SUM(CASE WHEN error IS NULL OR error = ''
                                         THEN 0 ELSE 1 END), 0)
                FROM turns
                WHERE timestamp >= ?
                GROUP BY model_id
                ORDER BY 2 DESC
                """,
                (cutoff,),
            )
            return [
                {"model_id": r[0], "turns": int(r[1]),
                 "avg_duration_s": float(r[2]),
                 "avg_retries": float(r[3]),
                 "total_cost": float(r[4]),
                 "errors": int(r[5])}
                for r in cur.fetchall()
            ]
        except Exception as exc:  # noqa: BLE001
            print(f"[telemetry] usage_by_model failed: {exc}",
                  flush=True, file=sys.stderr)
            return []

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
