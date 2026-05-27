"""Magic-link email authentication for Sevim Studio.

Why magic-link?  No password storage, no OAuth dance, no third-party
identity provider — just an email round-trip.  Suitable for a small
public launch where the cost of SES (≈$0.10 / 1000 emails) is the
entire ops overhead and we want a real per-user identity in telemetry.

Flow:

  1. User submits an email on ``/studio/auth/login``.
  2. ``POST /studio/auth/request-link`` signs a short-lived (15 min)
     token containing that email and asks SES to mail
     ``https://<host>/studio/auth/verify?t=<token>``.
     The endpoint always returns 200 — never reveals whether an email
     is known to the system, so it can't be used as a directory probe.
  3. ``GET /studio/auth/verify?t=<token>`` validates the signature +
     expiry, sets an HttpOnly signed cookie naming the user, and
     302-redirects to ``/studio``.
  4. ``/studio/chat`` (and any other gated route) runs through the
     :func:`current_user` dependency; missing or invalid cookie → 401.

Disabled by default in dev.  Turn on with ``SEVIM_AUTH_REQUIRED=1``.

Required env vars when enabled:

  * ``SEVIM_AUTH_SECRET``        — 32+ random bytes, used to sign tokens
                                   and cookies.  Pulled from Secrets
                                   Manager in production (see
                                   ``service/secrets.py``).
  * ``SEVIM_SES_FROM_ADDRESS``   — verified SES sender, e.g.
                                   ``Sevim <noreply@sevim.example>``.
                                   Domain must be SES-verified before
                                   the first send (out of sandbox).
  * ``SEVIM_AUTH_DOMAIN``        — public hostname used to build the
                                   magic link.  e.g. ``sevim.example``.
                                   Falls back to the request's Host
                                   header when unset, which is correct
                                   in dev but not behind CloudFront.

Also reads:

  * ``SEVIM_AUTH_LINK_TTL_S``    — magic-link validity (default 900 = 15 min).
  * ``SEVIM_AUTH_COOKIE_TTL_S``  — login cookie validity
                                   (default 2592000 = 30 days).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time

from fastapi import HTTPException, Request, Response

_COOKIE_NAME = "sevim_auth"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Toggles + config helpers
# ---------------------------------------------------------------------------

def is_required() -> bool:
    return os.environ.get("SEVIM_AUTH_REQUIRED", "0") not in ("0", "", "false", "no")


def _secret() -> bytes:
    s = os.environ.get("SEVIM_AUTH_SECRET")
    if not s:
        # Dev-only fallback so local imports don't crash; insecure but
        # the auth gate is off by default so this only matters if you
        # turn it on without provisioning a secret first.
        if is_required():
            raise RuntimeError(
                "SEVIM_AUTH_REQUIRED=1 but SEVIM_AUTH_SECRET is unset.  "
                "Provision a 32+ byte secret in Secrets Manager and re-deploy."
            )
        return b"insecure-dev-secret-do-not-use-in-production"
    return s.encode("utf-8")


def _link_ttl_s() -> int:
    return int(os.environ.get("SEVIM_AUTH_LINK_TTL_S", "900"))


def _cookie_ttl_s() -> int:
    return int(os.environ.get("SEVIM_AUTH_COOKIE_TTL_S", "2592000"))


def _ses_from() -> str:
    return os.environ.get("SEVIM_SES_FROM_ADDRESS", "")


def _public_domain(request: Request) -> str:
    return os.environ.get("SEVIM_AUTH_DOMAIN") or request.headers.get("host") or "localhost"


# ---------------------------------------------------------------------------
# Signed tokens — tiny HMAC-SHA256 envelope, no external dep.
# Format: base64url(payload_json) + "." + base64url(hmac_sha256(payload, secret))
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def sign(payload: dict, ttl_s: int) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_s
    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    body_b64 = _b64url_encode(body_bytes)
    sig = hmac.new(_secret(), body_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{body_b64}.{_b64url_encode(sig)}"


def unsign(token: str) -> dict | None:
    """Verify signature + expiry; return payload dict or None."""
    if not token or "." not in token:
        return None
    body_b64, sig_b64 = token.rsplit(".", 1)
    try:
        sig = _b64url_decode(sig_b64)
        expected = hmac.new(_secret(), body_b64.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        body = json.loads(_b64url_decode(body_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if body.get("exp", 0) < int(time.time()):
        return None
    return body


# ---------------------------------------------------------------------------
# Magic-link delivery via SES
# ---------------------------------------------------------------------------

def _send_magic_link(email: str, link: str) -> None:
    """Best-effort SES send.  Failures log but don't raise — same
    posture as the rest of Sevim's external-service calls.
    """
    sender = _ses_from()
    if not sender:
        _log("SEVIM_SES_FROM_ADDRESS unset — skipping send (would have mailed link)")
        _log(f"DEV-ONLY magic link for {email}: {link}")
        return
    try:
        import boto3
    except ImportError:
        _log("boto3 not installed — skipping SES send")
        return
    body_text = (
        "Click the link below to open Khayyam Math.\n\n"
        f"{link}\n\n"
        "This link is valid for 15 minutes.  If you didn't request it, "
        "you can ignore this email."
    )
    body_html = (
        "<p>Click the link below to open Khayyam Math.</p>"
        f"<p><a href=\"{link}\">{link}</a></p>"
        "<p>This link is valid for 15 minutes.  If you didn't request "
        "it, you can ignore this email.</p>"
    )
    try:
        ses = boto3.client("ses")
        ses.send_email(
            Source=sender,
            Destination={"ToAddresses": [email]},
            Message={
                "Subject": {"Data": "Your Khayyam Math link"},
                "Body": {
                    "Text": {"Data": body_text},
                    "Html": {"Data": body_html},
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"SES send_email failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Public API used by studio/app.py
# ---------------------------------------------------------------------------

def request_magic_link(email: str, request: Request) -> None:
    """Validate email + send the magic link.  Always returns silently —
    response code 200 regardless, so the endpoint can't be used to
    enumerate users.
    """
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return  # silent on bad input — no error oracle
    token = sign({"sub": email}, ttl_s=_link_ttl_s())
    domain = _public_domain(request)
    scheme = "https" if domain not in ("localhost", "127.0.0.1") else "http"
    link = f"{scheme}://{domain}/studio/auth/verify?t={token}"
    _send_magic_link(email, link)


def verify_link_and_set_cookie(token: str, response: Response) -> str | None:
    """Validate magic-link token → set HttpOnly signed login cookie.

    Returns the verified email on success, None on failure.  Caller is
    responsible for the post-verify redirect.
    """
    payload = unsign(token)
    if not payload:
        return None
    email = payload.get("sub")
    if not isinstance(email, str) or not _EMAIL_RE.match(email):
        return None
    cookie_token = sign({"sub": email}, ttl_s=_cookie_ttl_s())
    response.set_cookie(
        key=_COOKIE_NAME,
        value=cookie_token,
        max_age=_cookie_ttl_s(),
        httponly=True,
        secure=os.environ.get("SEVIM_AUTH_COOKIE_SECURE", "1") == "1",
        samesite="lax",
        path="/",
    )
    return email


def clear_cookie(response: Response) -> None:
    response.delete_cookie(_COOKIE_NAME, path="/")


def current_user(request: Request) -> str | None:
    """Return the logged-in user's email, or None if no valid cookie."""
    raw = request.cookies.get(_COOKIE_NAME)
    if not raw:
        return None
    payload = unsign(raw)
    if not payload:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None


def require_user(request: Request) -> str:
    """FastAPI dependency: 401 if SEVIM_AUTH_REQUIRED=1 and no valid cookie.

    When auth is OFF (dev mode), returns ``"anonymous"``; routes can pass
    that straight into telemetry without branching on the auth flag.
    """
    if not is_required():
        return "anonymous"
    user = current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Enter your email at /studio/auth/login to open Khayyam Math",
        )
    return user


def _log(msg: str) -> None:
    print(f"[auth] {msg}", flush=True, file=sys.stderr)
