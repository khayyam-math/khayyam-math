"""Two-bucket rate-limit unit test (session + IP).

Confirms:
  * Session-only check still works as before (when ip_hash is None).
  * IP cap rejects independently of session cap — clearing localStorage
    (fresh session_id) doesn't bypass an exhausted IP bucket.
  * Disabling SEVIM_RATE_LIMIT short-circuits both buckets.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_session_only_check_still_works() -> None:
    os.environ["SEVIM_RATE_LIMIT"] = "1"
    os.environ["SEVIM_RATE_CAPACITY"] = "3"  # tight bucket for a fast test
    # Force a fresh module-level RateLimiter so envs apply
    sys.modules.pop("studio.sessions", None)
    from studio.sessions import RateLimiter
    rl = RateLimiter()
    for _ in range(3):
        assert rl.check("sess1") is None
    msg = rl.check("sess1")
    assert msg is not None and "session" in msg.lower(), msg
    print("OK: session bucket caps after capacity exceeded")


def test_ip_bucket_blocks_session_id_rotation() -> None:
    os.environ["SEVIM_RATE_LIMIT"] = "1"
    os.environ["SEVIM_RATE_CAPACITY"] = "100"     # session is generous
    os.environ["SEVIM_RATE_IP_CAPACITY"] = "2"    # IP is tight
    sys.modules.pop("studio.sessions", None)
    from studio.sessions import RateLimiter
    rl = RateLimiter()
    # Two requests from same IP across two different sessions — fine.
    assert rl.check("sess_a", ip_hash="ip1") is None
    assert rl.check("sess_b", ip_hash="ip1") is None
    # Third request from same IP, even with a brand-new session, MUST fail.
    msg = rl.check("sess_c_new", ip_hash="ip1")
    assert msg is not None and "ip" in msg.lower(), f"expected IP rejection, got {msg!r}"
    # Different IP, same first-time session — passes.
    assert rl.check("sess_other", ip_hash="ip2") is None
    print("OK: IP bucket blocks session_id rotation")


def test_disabled_rate_limit_lets_everything_through() -> None:
    os.environ["SEVIM_RATE_LIMIT"] = "0"
    os.environ["SEVIM_RATE_CAPACITY"] = "1"
    os.environ["SEVIM_RATE_IP_CAPACITY"] = "1"
    sys.modules.pop("studio.sessions", None)
    from studio.sessions import RateLimiter
    rl = RateLimiter()
    for _ in range(20):
        assert rl.check("sess1", ip_hash="ip1") is None
    print("OK: SEVIM_RATE_LIMIT=0 disables both buckets")


if __name__ == "__main__":
    test_session_only_check_still_works()
    test_ip_bucket_blocks_session_id_rotation()
    test_disabled_rate_limit_lets_everything_through()
    print("\nAll rate-limit tests passed.")
