"""Usage stats for Khayyam Math production telemetry.

The ``users`` table (populated at magic-link verification time) is the
authoritative source of distinct people + their last-known location.
The salted IP-hash on ``sessions`` is reported alongside as a secondary
proxy that also covers anonymous traffic (signed-out browsing).

Run with:
    DATABASE_URL=postgres://user:pw@host:5432/db  python scripts/count_users.py

Or, if SEVIM_TELEMETRY_DB_URL is set (the variable the FastAPI app uses):
    SEVIM_TELEMETRY_DB_URL=...  python scripts/count_users.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone


def _get_url() -> str:
    for var in ("DATABASE_URL", "SEVIM_TELEMETRY_DB_URL", "TELEMETRY_DB_URL"):
        if os.environ.get(var):
            return os.environ[var]
    sys.exit(
        "Set DATABASE_URL (or SEVIM_TELEMETRY_DB_URL) to the prod Postgres URL.\n"
        "Pull it from AWS Secrets Manager: telemetry-db-url"
    )


def _connect(url: str):
    try:
        import psycopg2  # type: ignore
    except ImportError:
        sys.exit("psycopg2 not installed. `pip install psycopg2-binary`")
    return psycopg2.connect(url)


def _q(cur, sql: str) -> object:
    cur.execute(sql)
    row = cur.fetchone()
    return row[0] if row else None


def main() -> None:
    conn = _connect(_get_url())
    cur = conn.cursor()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Khayyam Math usage as of {now}")
    print("-" * 60)

    total_sessions = _q(cur, "SELECT COUNT(*) FROM sessions")
    distinct_ips = _q(cur, "SELECT COUNT(DISTINCT ip_hash) FROM sessions WHERE ip_hash IS NOT NULL")
    total_turns = _q(cur, "SELECT COUNT(*) FROM turns")
    total_cost = _q(cur, "SELECT COALESCE(SUM(cost_usd_estimate), 0) FROM sessions")
    distinct_emails = _q(cur, "SELECT COUNT(*) FROM users")
    total_logins = _q(cur, "SELECT COALESCE(SUM(login_count), 0) FROM users")

    print(f"  Distinct signed-in emails  : {distinct_emails}   (authoritative — populated at magic-link verify)")
    print(f"  Total logins ever          : {total_logins}")
    print(f"  Distinct IP hashes         : {distinct_ips}   (also covers anonymous browsing)")
    print(f"  Total sessions ever opened : {total_sessions}")
    print(f"  Total prompts (turns)      : {total_turns}")
    print(f"  Total estimated spend      : ${total_cost:.2f}")
    print()

    for label, secs in (("24 hours", 86_400), ("7 days", 7 * 86_400), ("30 days", 30 * 86_400)):
        since_clause = f"WHERE last_seen_at > extract(epoch from now()) - {secs}"
        active_sessions = _q(cur, f"SELECT COUNT(*) FROM sessions {since_clause}")
        active_ips = _q(cur, f"SELECT COUNT(DISTINCT ip_hash) FROM sessions {since_clause} AND ip_hash IS NOT NULL")
        active_emails = _q(cur, f"SELECT COUNT(*) FROM users {since_clause}")
        print(f"  Active in last {label:8s}: {active_emails} emails, "
              f"{active_sessions} sessions, {active_ips} distinct IPs")

    print()
    print("By country (last-known location of signed-in users)")
    print("-" * 60)
    cur.execute(
        """
        SELECT COALESCE(last_login_country, '??') AS cc,
               COUNT(*) AS n
        FROM users
        GROUP BY cc
        ORDER BY n DESC, cc
        LIMIT 25
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no signed-in users yet)")
    else:
        for cc, n in rows:
            print(f"  {cc:4s}  {n}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
