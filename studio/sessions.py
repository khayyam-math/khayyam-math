"""Session identifier helpers + token-bucket rate limiter for Studio.

The session_id is a stable per-tab identifier the Studio frontend
generates on first load (UUID4) and persists in localStorage.  Studio
uses it to:

  * Group turns into a conversation in the telemetry log.
  * Apply rate limits ("max 30 requests / hour per session").
  * Apply a cost-cap ("max $1 / day per session").

In a public deployment we'd back the rate limiter with Redis; the
in-memory token bucket here is good for single-process use and
sufficient for the local-private + small-scale public phase.

Both rate limit and cost guard are off by default.  Toggle with:
  * SEVIM_RATE_LIMIT=1
  * SEVIM_COST_GUARD=1
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Iterable


# ── Rate limit defaults (tunable via env) ───────────────────────────────

# Token-bucket: capacity tokens, refilling at refill_per_sec.
# Default capacity 30 lets a quick burst of refinements through; refill
# 30/3600 = 0.00833/s averages back to 30 requests/hour steady state.
def _rate_capacity() -> int:
    return int(os.environ.get("SEVIM_RATE_CAPACITY", "30"))


def _rate_refill_per_sec() -> float:
    return float(os.environ.get("SEVIM_RATE_REFILL_PER_SEC", "0.00833"))


def _rate_daily_max() -> int:
    return int(os.environ.get("SEVIM_RATE_DAILY_MAX", "100"))


# IP-keyed bucket — defended against the trivial "clear localStorage to
# get a fresh session_id" bypass.  Deliberately lower than the session
# limits because an IP can be NAT-shared but is much harder to rotate.
def _ip_rate_capacity() -> int:
    return int(os.environ.get("SEVIM_RATE_IP_CAPACITY", "10"))


def _ip_rate_refill_per_sec() -> float:
    # 30/3600 = 0.00833/s — same steady rate as session bucket; the
    # *capacity* is what differs.  Burst of 10 then 30/hr/IP.
    return float(os.environ.get("SEVIM_RATE_IP_REFILL_PER_SEC", "0.00833"))


def _ip_rate_daily_max() -> int:
    return int(os.environ.get("SEVIM_RATE_IP_DAILY_MAX", "30"))


def _cost_daily_max_usd() -> float:
    return float(os.environ.get("SEVIM_COST_DAILY_MAX_USD", "1.00"))


def is_rate_limited() -> bool:
    return os.environ.get("SEVIM_RATE_LIMIT", "0") not in ("0", "", "false", "no")


def is_cost_guarded() -> bool:
    return os.environ.get("SEVIM_COST_GUARD", "0") not in ("0", "", "false", "no")


# ── Token-bucket rate limiter ───────────────────────────────────────────

@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class RateLimiter:
    """Per-session AND per-IP token buckets + daily counters.

    Thread-safe.  ``check(session_id, ip_hash)`` returns ``None`` if the
    request is allowed, otherwise a human-readable error string
    explaining why it was rejected.

    Two parallel buckets:
      * Session bucket — keyed on the frontend-generated session_id
        (UUID in localStorage).  Trivially bypassed by clearing
        localStorage; useful for honest abuse detection only.
      * IP bucket — keyed on a salted SHA-256 of the client IP.  The
        defence against "open incognito for fresh tokens": an attacker
        has to rotate their IP, not just their cookies.

    A request is allowed only when BOTH buckets pass.  IP cap defaults
    are deliberately lower than session caps because an IP is much
    harder to rotate than a cookie.

    Important: when behind an ALB / CloudFront, the actual client IP
    arrives in the ``X-Forwarded-For`` header.  Pass ``ip_hash`` from
    the resolved client IP — see ``studio/app.py:chat`` for the
    extraction logic.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._daily_starts: dict[str, float] = {}
        self._daily_counts: dict[str, int] = {}
        self._ip_buckets: dict[str, _Bucket] = {}
        self._ip_daily_starts: dict[str, float] = {}
        self._ip_daily_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def check(
        self,
        session_id: str,
        ip_hash: str | None = None,
    ) -> str | None:
        if not is_rate_limited():
            return None
        now = time.time()
        with self._lock:
            if session_id:
                msg = self._check_bucket(
                    key=session_id,
                    buckets=self._buckets,
                    daily_starts=self._daily_starts,
                    daily_counts=self._daily_counts,
                    capacity=_rate_capacity(),
                    refill=_rate_refill_per_sec(),
                    daily_cap=_rate_daily_max(),
                    label="session",
                    now=now,
                )
                if msg is not None:
                    return msg
            if ip_hash:
                msg = self._check_bucket(
                    key=ip_hash,
                    buckets=self._ip_buckets,
                    daily_starts=self._ip_daily_starts,
                    daily_counts=self._ip_daily_counts,
                    capacity=_ip_rate_capacity(),
                    refill=_ip_rate_refill_per_sec(),
                    daily_cap=_ip_rate_daily_max(),
                    label="IP",
                    now=now,
                )
                if msg is not None:
                    return msg
        return None

    @staticmethod
    def _check_bucket(
        *,
        key: str,
        buckets: dict[str, _Bucket],
        daily_starts: dict[str, float],
        daily_counts: dict[str, int],
        capacity: int,
        refill: float,
        daily_cap: int,
        label: str,
        now: float,
    ) -> str | None:
        """Apply a single (token-bucket, daily-cap) pair.

        Caller MUST hold the parent lock; this method mutates the dicts
        passed in.  Mutates atomically: a request that passes the
        token-bucket test debits exactly one token before returning.
        """
        # Daily counter — reset every 24 h.
        day_start = daily_starts.get(key, 0)
        if now - day_start > 86400:
            daily_starts[key] = now
            daily_counts[key] = 0
        count = daily_counts.get(key, 0)
        if count >= daily_cap:
            return (f"Daily {label} request limit reached "
                    f"({count}/{daily_cap}).  Try again in 24 h.")
        # Token bucket — refill, then take one.
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(capacity), last_refill=now)
            buckets[key] = bucket
        elapsed = now - bucket.last_refill
        bucket.tokens = min(float(capacity), bucket.tokens + elapsed * refill)
        bucket.last_refill = now
        if bucket.tokens < 1.0:
            wait_s = (
                max(0.0, (1.0 - bucket.tokens) / refill)
                if refill > 0 else float("inf")
            )
            wait_str = (f"{wait_s:.0f}s" if wait_s != float("inf")
                        else "until your bucket refills")
            return (f"{label.capitalize()} rate limit hit.  Try again in "
                    f"{wait_str} ({capacity} burst / "
                    f"{int(refill * 3600)}/hr steady).")
        bucket.tokens -= 1.0
        daily_counts[key] = count + 1
        return None

    def reset(self) -> None:
        """Test helper: wipe all buckets and counters."""
        with self._lock:
            self._buckets.clear()
            self._daily_starts.clear()
            self._daily_counts.clear()
            self._ip_buckets.clear()
            self._ip_daily_starts.clear()
            self._ip_daily_counts.clear()


_RATE_LIMITER = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _RATE_LIMITER


# ── Cost guard ──────────────────────────────────────────────────────────

def check_cost_guard(session_id: str) -> str | None:
    """Look up the session's last-24h spend in the telemetry DB.  Reject
    if it exceeds the daily cap.  Returns ``None`` when allowed."""
    if not is_cost_guarded():
        return None
    if not session_id:
        return None
    from sevim.telemetry import get_telemetry
    tel = get_telemetry()
    if tel is None:
        return None
    spent = tel.session_cost(session_id, since_s=86400.0)
    cap = _cost_daily_max_usd()
    if spent >= cap:
        return (f"This session has hit the $/day cost cap "
                f"(${spent:.2f} / ${cap:.2f}).  Try again tomorrow.")
    return None


# ── ip_hash helper ──────────────────────────────────────────────────────

def client_ip_from(request) -> str | None:  # type: ignore[no-untyped-def]
    """Real client IP: leftmost X-Forwarded-For when SEVIM_TRUST_PROXY=1,
    else the direct socket peer.  Centralised so the auth + chat paths
    agree on what counts as the client."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and os.environ.get("SEVIM_TRUST_PROXY", "0") == "1":
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def hash_ip(ip: str | None, salt: str | None = None) -> str | None:
    """Salted SHA-256 of an IP address — for telemetry without PII.

    Salt defaults to env ``SEVIM_IP_HASH_SALT``; if unset, returns None
    (we'd rather drop the field than store something rainbow-table-able).
    """
    if not ip:
        return None
    salt = salt or os.environ.get("SEVIM_IP_HASH_SALT")
    if not salt:
        return None
    h = hashlib.sha256()
    h.update(salt.encode("utf-8"))
    h.update(ip.encode("utf-8"))
    return h.hexdigest()[:16]


# ── Cost estimation (very rough; used to feed the cost guard) ───────────

# gpt-4o pricing 2025-05: $2.50 / 1M input + $10.00 / 1M output tokens.
# We approximate per-call cost by tokens-in-the-prompt and tokens-out;
# we don't have those numbers without parsing the response, so we use
# a flat per-call estimate.  Adjust as data accumulates.
_COST_PER_EXPRESS_CALL_USD = float(
    os.environ.get("SEVIM_COST_PER_EXPRESS_USD", "0.05")
)


def estimate_express_cost(retries_used: int = 0) -> float:
    """Per-turn cost estimate.  retries_used adds the cost of additional
    generation + review round-trips."""
    return _COST_PER_EXPRESS_CALL_USD * (1 + retries_used)
