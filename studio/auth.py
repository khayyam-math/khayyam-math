"""Magic-link email authentication for Sevim Studio.

Why magic-link?  No password storage, no OAuth dance, no third-party
identity provider — just an email round-trip.  Suitable for a small
public launch where a transactional mail relay is the entire ops
overhead and we want a real per-user identity in telemetry.

Flow:

  1. User submits an email on ``/studio/auth/login``.
  2. ``POST /studio/auth/request-link`` signs a short-lived (15 min)
     token containing that email and mails
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
  * ``SEVIM_MAIL_FROM``          — verified sender address, e.g.
                                   ``noreply@sevim.example``.  The
                                   domain must be verified (SPF+DKIM)
                                   with the relay before the first
                                   send.  ``SEVIM_SES_FROM_ADDRESS``
                                   is still honoured as a fallback.
                                   Relay selection and the rest of the
                                   mail env vars live in
                                   ``service/mailer.py``.
  * ``SEVIM_AUTH_DOMAIN``        — public hostname used to build the
                                   magic link.  e.g. ``sevim.example``.
                                   Falls back to the request's Host
                                   header when unset, which is correct
                                   in dev but not behind CloudFront.

Also reads:

  * ``SEVIM_AUTH_LINK_TTL_S``    — magic-link validity (default 900 = 15 min).
  * ``SEVIM_AUTH_COOKIE_TTL_S``  — ABSOLUTE login lifetime cap, measured
                                   from first login (default 2592000 =
                                   30 days).  A session can never live
                                   longer than this no matter how active.
  * ``SEVIM_AUTH_IDLE_TTL_S``    — IDLE timeout (default 86400 = 24 h).
                                   The login cookie is a *sliding*
                                   session: each authenticated request
                                   re-issues it with a fresh idle
                                   deadline.  If a user goes idle for
                                   longer than this window the cookie
                                   expires and they must re-authenticate
                                   via a new magic link.  This stops a
                                   single login from staying valid
                                   indefinitely while unattended.
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


def _idle_ttl_s() -> int:
    # Sliding idle window.  Default 24 h: an unattended session dies
    # after a day of inactivity even though the absolute cap is 30 days.
    return int(os.environ.get("SEVIM_AUTH_IDLE_TTL_S", "86400"))


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
    now = int(time.time())
    if body.get("exp", 0) < now:
        return None
    # Absolute session cap: a login cookie carries ``mexp`` (max expiry,
    # set at first login).  Even if the sliding ``exp`` is still in the
    # future, a session past its absolute cap is dead — this bounds the
    # total lifetime of any single login regardless of activity.
    mexp = body.get("mexp")
    if isinstance(mexp, (int, float)) and mexp < now:
        return None
    return body


# ---------------------------------------------------------------------------
# Magic-link delivery (SMTP or SES — see service/mailer.py)
# ---------------------------------------------------------------------------

def _send_magic_link(email: str, link: str) -> None:
    """Best-effort send.  Failures log but don't raise — same posture as
    the rest of Sevim's external-service calls.
    """
    from service.mailer import sender_address, send_email

    if not sender_address():
        _log("no sender configured — skipping send (would have mailed link)")
        _log(f"DEV-ONLY magic link for {email}: {link}")
        return
    # Deliverability note: spam filters penalise "bare URL + boilerplate"
    # bodies and reward a natural text-to-link ratio plus a clear reason the
    # message was sent.  Keep real sentences, one call-to-action, and a
    # context line explaining why the recipient received this.
    body_text = (
        "Hi,\n\n"
        "Here is your sign-in link for Khayyam Math. Open it in the same "
        "browser where you entered your email:\n\n"
        f"{link}\n\n"
        "The link works once and expires in 15 minutes. If it expires, just "
        "request a new one from the sign-in page.\n\n"
        "You're receiving this because someone entered this address to sign "
        "in to Khayyam Math. If that wasn't you, no action is needed and you "
        "can safely ignore this message.\n\n"
        "Khayyam Math\n"
        "https://khayyammath.com\n"
    )
    body_html = (
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,Helvetica,"
        "Arial,sans-serif;font-size:15px;line-height:1.5;color:#1a1a1a;"
        "max-width:520px\">"
        "<p>Hi,</p>"
        "<p>Here is your sign-in link for <strong>Khayyam Math</strong>. "
        "Open it in the same browser where you entered your email:</p>"
        f"<p style=\"margin:24px 0\"><a href=\"{link}\" "
        "style=\"background:#2563eb;color:#ffffff;text-decoration:none;"
        "padding:12px 22px;border-radius:6px;display:inline-block;"
        "font-weight:600\">Open Khayyam Math</a></p>"
        "<p style=\"font-size:13px;color:#555\">Or paste this link into your "
        f"browser:<br><a href=\"{link}\" style=\"color:#2563eb\">{link}</a></p>"
        "<p>The link works once and expires in 15 minutes. If it expires, "
        "just request a new one from the sign-in page.</p>"
        "<hr style=\"border:none;border-top:1px solid #eee;margin:24px 0\">"
        "<p style=\"font-size:12px;color:#888\">You're receiving this because "
        "someone entered this address to sign in to Khayyam Math. If that "
        "wasn't you, no action is needed and you can safely ignore this "
        "message.</p>"
        "<p style=\"font-size:12px;color:#888\">Khayyam Math &middot; "
        "<a href=\"https://khayyammath.com\" style=\"color:#888\">"
        "khayyammath.com</a></p>"
        "</div>"
    )
    send_email(
        to=email,
        subject="Your Khayyam Math sign-in link",
        text=body_text,
        html=body_html,
    )


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


def _set_login_cookie(email: str, response: Response,
                      mexp: int | None = None) -> None:
    """Issue (or re-issue) the sliding login cookie.

    The cookie's own expiry is the IDLE deadline (``now + idle_ttl``),
    clamped so it can never exceed the absolute session cap ``mexp``.
    On a fresh login ``mexp`` is ``None`` and gets set to
    ``now + cookie_ttl``; on a sliding refresh the caller passes the
    existing ``mexp`` through so the absolute cap doesn't move.
    """
    now = int(time.time())
    if mexp is None:
        mexp = now + _cookie_ttl_s()
    # Effective cookie life = idle window, but never beyond the cap.
    eff_ttl = max(0, min(_idle_ttl_s(), mexp - now))
    cookie_token = sign({"sub": email, "mexp": mexp}, ttl_s=eff_ttl)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=cookie_token,
        max_age=eff_ttl,
        httponly=True,
        secure=os.environ.get("SEVIM_AUTH_COOKIE_SECURE", "1") == "1",
        samesite="lax",
        path="/",
    )


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
    _set_login_cookie(email, response)  # fresh login: new absolute cap
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


def require_user(request: Request, response: Response = None) -> str:  # type: ignore[assignment]
    """FastAPI dependency: 401 if SEVIM_AUTH_REQUIRED=1 and no valid cookie.

    When auth is OFF (dev mode), returns ``"anonymous"``; routes can pass
    that straight into telemetry without branching on the auth flag.

    On a valid session this re-issues the cookie with a fresh idle
    deadline (sliding session), so active users stay logged in while
    idle ones time out after ``SEVIM_AUTH_IDLE_TTL_S``.  ``response`` is
    injected by FastAPI; the ``Set-Cookie`` header rides on the route's
    final response.  No middleware is involved (middleware would break
    SSE streaming), so streaming routes are unaffected.
    """
    if not is_required():
        return "anonymous"
    raw = request.cookies.get(_COOKIE_NAME)
    payload = unsign(raw) if raw else None
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="Enter your email at /studio/auth/login to open Khayyam Math",
        )
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(
            status_code=401,
            detail="Enter your email at /studio/auth/login to open Khayyam Math",
        )
    # Slide the idle window forward, preserving the absolute cap (mexp).
    # ``response`` is None only when called directly (e.g. unit tests);
    # in that case there's nothing to re-issue the cookie onto, so skip
    # the slide — the validity decision above is unaffected.
    if response is not None:
        mexp = payload.get("mexp")
        _set_login_cookie(
            sub, response,
            mexp=int(mexp) if isinstance(mexp, (int, float)) else None,
        )
    return sub


def _log(msg: str) -> None:
    print(f"[auth] {msg}", flush=True, file=sys.stderr)
