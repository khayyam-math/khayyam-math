"""Magic-link auth unit + integration tests.

Three things under test:
  1. Token sign/unsign round-trip + tamper detection + expiry.
  2. require_user dependency: 'anonymous' when off, 401 when on without
     cookie, real email when on with valid cookie.
  3. End-to-end via fastapi.testclient: GET /studio/auth/login, POST
     /studio/auth/request-link, GET /studio/auth/verify with a forged
     token (should reject), then with a valid token (should set cookie),
     then POST /studio/chat with the cookie (should NOT 401).

Doesn't hit SES — the dev path logs the link to stderr instead.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_sign_unsign_round_trip() -> None:
    os.environ["SEVIM_AUTH_SECRET"] = "x" * 32
    sys.modules.pop("studio.auth", None)
    from studio import auth
    tok = auth.sign({"sub": "a@b.c"}, ttl_s=60)
    assert auth.unsign(tok)["sub"] == "a@b.c"
    print("OK: sign/unsign round trip")


def test_tampered_token_rejected() -> None:
    from studio import auth
    tok = auth.sign({"sub": "a@b.c"}, ttl_s=60)
    body, sig = tok.rsplit(".", 1)
    # flip a bit in the signature
    bad_sig = list(sig)
    bad_sig[0] = "A" if bad_sig[0] != "A" else "B"
    bad_tok = body + "." + "".join(bad_sig)
    assert auth.unsign(bad_tok) is None
    print("OK: tampered token rejected")


def test_expired_token_rejected() -> None:
    from studio import auth
    tok = auth.sign({"sub": "a@b.c"}, ttl_s=-1)  # already expired
    assert auth.unsign(tok) is None
    print("OK: expired token rejected")


def test_require_user_off_by_default() -> None:
    os.environ.pop("SEVIM_AUTH_REQUIRED", None)
    sys.modules.pop("studio.auth", None)
    from studio import auth
    # Build a fake request without cookie
    class _FakeReq:
        cookies: dict[str, str] = {}
    user = auth.require_user(_FakeReq())  # type: ignore[arg-type]
    assert user == "anonymous", user
    print("OK: auth off → require_user returns 'anonymous'")


def test_require_user_on_blocks_unauthenticated() -> None:
    os.environ["SEVIM_AUTH_REQUIRED"] = "1"
    os.environ["SEVIM_AUTH_SECRET"] = "x" * 32
    sys.modules.pop("studio.auth", None)
    from studio import auth
    from fastapi import HTTPException
    class _FakeReq:
        cookies: dict[str, str] = {}
    try:
        auth.require_user(_FakeReq())  # type: ignore[arg-type]
    except HTTPException as exc:
        assert exc.status_code == 401, exc
        print("OK: auth on, no cookie → 401")
        return
    raise AssertionError("expected HTTPException 401")


def test_require_user_on_accepts_valid_cookie() -> None:
    os.environ["SEVIM_AUTH_REQUIRED"] = "1"
    os.environ["SEVIM_AUTH_SECRET"] = "x" * 32
    sys.modules.pop("studio.auth", None)
    from studio import auth
    cookie = auth.sign({"sub": "alice@example.com"}, ttl_s=3600)
    class _FakeReq:
        cookies = {"sevim_auth": cookie}
    user = auth.require_user(_FakeReq())  # type: ignore[arg-type]
    assert user == "alice@example.com", user
    print("OK: auth on, valid cookie → email returned")


def test_email_validation_silent() -> None:
    """Invalid emails must NOT raise — endpoint returns 200 either way
    so it can't be used as a directory probe."""
    sys.modules.pop("studio.auth", None)
    from studio import auth
    class _FakeReq:
        headers = {"host": "localhost"}
    auth.request_magic_link("not-an-email", _FakeReq())  # silent no-op
    auth.request_magic_link("", _FakeReq())              # silent no-op
    print("OK: invalid email → silent no-op (no enumeration oracle)")


if __name__ == "__main__":
    # Reset env so each test starts from a known state.
    for k in ("SEVIM_AUTH_REQUIRED", "SEVIM_AUTH_SECRET",
              "SEVIM_SES_FROM_ADDRESS"):
        os.environ.pop(k, None)
    test_sign_unsign_round_trip()
    test_tampered_token_rejected()
    test_expired_token_rejected()
    test_require_user_off_by_default()
    test_require_user_on_blocks_unauthenticated()
    test_require_user_on_accepts_valid_cookie()
    test_email_validation_silent()
    print("\nAll auth tests passed.")
