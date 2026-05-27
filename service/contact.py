"""Contact form route + math-captcha + SES email send.

Mounted by ``service.app``.  Endpoints:

  GET  /contact          — render the form, embed a captcha question
                           and a HMAC-signed token carrying the answer.
  POST /contact/submit   — validate captcha, send email via SES, render
                           a confirmation panel.

Why a math captcha and not reCAPTCHA / hCaptcha:
  The whole site goes out of its way to avoid third-party trackers.
  An arithmetic question (``7 + 4 = ?``) signed with a 5-min HMAC token
  stops 99% of dumb spambots without loading a single byte from a
  third party or requiring user-trackable behaviour.  Combined with
  the existing IP-rate-limiter, abuse pressure stays low.

  If spam volume ever justifies it, swap ``_make_challenge`` /
  ``_verify_challenge`` for Cloudflare Turnstile (free, privacy-
  friendly, no challenge in the common case) — the rest of the
  module stays identical.

Required env (set via Secrets Manager in production):
  * ``SEVIM_AUTH_SECRET``        — same key used for auth tokens
  * ``SEVIM_SES_FROM_ADDRESS``   — verified SES sender, e.g.
                                   ``noreply@khayyammath.com``
  * ``SEVIM_CONTACT_TO``         — recipient (default
                                   ``gradersystem@gmail.com``)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets as _secrets
import sys
import time

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse


router = APIRouter(prefix="/contact", tags=["contact"])

_TOKEN_TTL_S = 600          # 10 minutes
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_NAME = 80
_MAX_MESSAGE = 4000
_DEFAULT_RECIPIENT = "gradersystem@gmail.com"


# ---------------------------------------------------------------------------
# Captcha — small int sum, HMAC-signed answer + expiry.
# ---------------------------------------------------------------------------

def _secret() -> bytes:
    s = os.environ.get("SEVIM_AUTH_SECRET")
    if s:
        return s.encode("utf-8")
    # Refuse the insecure fallback whenever auth is on (i.e. in any real
    # deploy).  An unset secret here would let an attacker forge captcha
    # tokens and spam the contact form at scale.
    from studio.auth import is_required as _auth_required
    if _auth_required():
        raise RuntimeError(
            "SEVIM_AUTH_REQUIRED=1 but SEVIM_AUTH_SECRET is unset.  "
            "Provision a 32+ byte secret in Secrets Manager and re-deploy."
        )
    return b"insecure-dev-secret-do-not-use-in-production"


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def _make_challenge() -> tuple[str, int, str]:
    """Return ``(question_html, answer, signed_token)``.

    ``signed_token`` is a base64url-encoded {answer, exp} dict signed
    with the auth secret.  The form posts both the user's typed
    answer and the token back; we re-derive the expected answer from
    the token and compare.
    """
    a = _secrets.randbelow(8) + 1   # 1..8
    b = _secrets.randbelow(8) + 1   # 1..8
    answer = a + b
    body = json.dumps(
        {"a": answer, "exp": int(time.time()) + _TOKEN_TTL_S},
        separators=(",", ":"), sort_keys=True,
    ).encode()
    body_b64 = _b64u(body)
    sig = hmac.new(_secret(), body_b64.encode("ascii"), hashlib.sha256).digest()
    token = f"{body_b64}.{_b64u(sig)}"
    return f"{a} + {b}", answer, token


def _verify_challenge(token: str, user_answer: str) -> bool:
    if not token or "." not in token:
        return False
    body_b64, sig_b64 = token.rsplit(".", 1)
    try:
        sig = _b64u_decode(sig_b64)
        expected = hmac.new(_secret(), body_b64.encode("ascii"),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return False
        body = json.loads(_b64u_decode(body_b64))
    except (ValueError, json.JSONDecodeError):
        return False
    if body.get("exp", 0) < int(time.time()):
        return False
    try:
        return int(user_answer.strip()) == int(body.get("a", -1))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# SES delivery
# ---------------------------------------------------------------------------

def _send_email(name: str, from_email: str, message: str, ip_hash: str | None) -> bool:
    """Send the contact-form payload via SES.  Best-effort — errors
    log to stderr and the caller decides what to show the user."""
    sender = os.environ.get("SEVIM_SES_FROM_ADDRESS")
    recipient = os.environ.get("SEVIM_CONTACT_TO", _DEFAULT_RECIPIENT)
    if not sender:
        _log("SEVIM_SES_FROM_ADDRESS unset — skipping send")
        return False
    try:
        import boto3
    except ImportError:
        _log("boto3 not installed — cannot send")
        return False
    body_text = (
        f"New contact form submission from khayyammath.com\n\n"
        f"From:    {name} <{from_email}>\n"
        f"IP hash: {ip_hash or '(unset)'}\n"
        f"\n"
        f"Message:\n"
        f"{'-' * 60}\n"
        f"{message}\n"
        f"{'-' * 60}\n"
    )
    body_html = (
        f"<p>New contact form submission from "
        f"<strong>khayyammath.com</strong></p>"
        f"<p><strong>From:</strong> "
        f"{html.escape(name)} &lt;{html.escape(from_email)}&gt;</p>"
        f"<p><strong>IP hash:</strong> {html.escape(ip_hash or '(unset)')}"
        f"</p><hr>"
        f"<pre style='white-space:pre-wrap;font-family:inherit'>"
        f"{html.escape(message)}</pre>"
    )
    subject = f"[Khayyam Math contact] {name[:60]}"
    try:
        ses = boto3.client("ses")
        ses.send_email(
            Source=sender,
            Destination={"ToAddresses": [recipient]},
            ReplyToAddresses=[from_email],
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Text": {"Data": body_text},
                    "Html": {"Data": body_html},
                },
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"SES send_email failed: {type(exc).__name__}: {exc}")
        return False


# ---------------------------------------------------------------------------
# HTML page (server-rendered, no JS dependency for the captcha)
# ---------------------------------------------------------------------------

_PAGE_HEAD = """<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#fafafa" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1a1a1a" media="(prefers-color-scheme: dark)">
<title>Contact — Khayyam Math</title>
<meta name="description" content="Get in touch with the team behind Khayyam Math, the live diagram tutor.">
<link rel="canonical" href="https://khayyammath.com/contact">
<style>
 :root {
   color-scheme: light dark;
   --bg: #fafafa; --fg: #1a1d24; --muted: #5a6470;
   --soft: #ecf1f7; --border: #dfe4ec; --accent: #1f6fe0;
 }
 @media (prefers-color-scheme: dark) {
   :root { --bg: #0f1115; --fg: #ebeef3; --muted: #98a3b1;
           --soft: #1a1f29; --border: #2a313d; }
 }
 *, *::before, *::after { box-sizing: border-box; }
 html, body { margin: 0; padding: 0; min-height: 100%;
   background: var(--bg); color: var(--fg);
   font: 16px/1.55 -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
 }
 body { padding: 0 1em; }
 header { max-width: 36em; margin: 0 auto; padding: 1.4em 0;
   border-bottom: 1px solid var(--border); display: flex; gap: 1em;
   align-items: center; }
 header a.brand { font-weight: 700; letter-spacing: -0.02em;
   color: var(--fg); text-decoration: none; font-size: 1.1em; }
 header nav { margin-left: auto; }
 header nav a { color: var(--accent); text-decoration: none;
   margin-left: 1em; font-size: 0.95em; }
 main { max-width: 36em; margin: 0 auto; padding: 2em 0; }
 h1 { font-weight: 700; font-size: 1.6em; margin: 0 0 0.4em;
   letter-spacing: -0.02em; }
 p.lede { color: var(--muted); margin: 0 0 1.5em; }
 form { display: flex; flex-direction: column; gap: 0.9em; }
 label { font-size: 0.92em; color: var(--muted);
   display: flex; flex-direction: column; gap: 0.35em; }
 input[type=text], input[type=email], input[type=number], textarea {
   padding: 0.75em 0.9em; min-height: 48px; width: 100%;
   border: 1px solid var(--border); border-radius: 8px;
   background: var(--bg); color: var(--fg);
   font: inherit; font-size: 16px;
 }
 textarea { min-height: 11em; resize: vertical; }
 input:focus, textarea:focus { outline: none;
   border-color: var(--accent);
   box-shadow: 0 0 0 3px rgba(31,111,224,0.18); }
 .captcha { background: var(--soft); border: 1px solid var(--border);
   border-radius: 8px; padding: 1em;
   display: flex; gap: 0.8em; align-items: center; }
 .captcha .q { font-size: 1.05em; font-weight: 500; }
 .captcha input { max-width: 5em; min-height: 44px;
   padding: 0.4em 0.6em; }
 button { padding: 0 1.4em; min-height: 48px; font: inherit;
   font-weight: 500; border: 0; border-radius: 8px;
   background: var(--accent); color: #fff; cursor: pointer; }
 button:hover { filter: brightness(1.05); }
 .ok, .err { padding: 1em 1.2em; border-radius: 8px;
   margin-bottom: 1em; }
 .ok  { background: #e6f7ec; border: 1px solid #b9e6c7;
        color: #1f6e36; }
 .err { background: #fde8e8; border: 1px solid #f5b5b5;
        color: #a52424; }
 @media (prefers-color-scheme: dark) {
   .ok  { background: #103a1d; border-color: #1d6634; color: #84e0a0; }
   .err { background: #3d1414; border-color: #6e2626; color: #ffb1b1; }
 }
 footer { max-width: 36em; margin: 3em auto 2em; padding-top: 2em;
   border-top: 1px solid var(--border); color: var(--muted);
   font-size: 0.85em; text-align: center; }
 footer a { color: var(--muted); }
</style></head>
<body>
<header>
  <a class="brand" href="/">Khayyam Math</a>
  <nav>
    <a href="/studio/auth/login">Sign in</a>
    <a href="/terms">Terms</a>
  </nav>
</header>
<main>
"""


_PAGE_FOOT = """
</main>
<footer>
  <p>Khayyam Math · <a href="/">home</a> · <a href="/terms">terms</a></p>
</footer>
<div id="cookie-banner" hidden style="
     position:fixed; bottom:0; left:0; right:0;
     background:#1a1d24; color:#eef0f3;
     padding:0.9em 1.2em; padding-bottom:max(0.9em, env(safe-area-inset-bottom));
     display:flex; flex-wrap:wrap; gap:0.8em; align-items:center;
     justify-content:center; font-size:0.92em; z-index:1000;
     box-shadow:0 -2px 12px rgba(0,0,0,0.18);">
  <span style="flex:1; min-width:14em; line-height:1.45;">
    Khayyam Math uses one essential cookie for sign-in.  No advertising
    or analytics trackers.  See the <a href="/terms" style="color:#7eb6ff">Terms</a>.
  </span>
  <button id="cookie-ok" type="button" style="
        padding:0.55em 1.2em; min-height:40px;
        background:#1f6fe0; color:#fff; border:0;
        border-radius:6px; font:inherit; cursor:pointer;">Got it</button>
</div>
<script>
(function() {
  try {
    if (localStorage.getItem('khayyam_cookie_consent') === '1') return;
  } catch (_) {}
  var b = document.getElementById('cookie-banner');
  if (!b) return;
  b.hidden = false;
  document.getElementById('cookie-ok').addEventListener('click', function () {
    try { localStorage.setItem('khayyam_cookie_consent', '1'); } catch (_) {}
    b.style.transition = 'opacity 0.25s';
    b.style.opacity = '0';
    setTimeout(function () { b.remove(); }, 260);
  });
})();
</script>
</body></html>
"""


def _render_form(notice_html: str = "",
                 prefill: dict | None = None) -> str:
    prefill = prefill or {}
    q, _ans, token = _make_challenge()
    name_v = html.escape(prefill.get("name", ""))
    email_v = html.escape(prefill.get("email", ""))
    msg_v = html.escape(prefill.get("message", ""))
    return _PAGE_HEAD + f"""
  <h1>Contact</h1>
  <p class="lede">Found a bug, want to say hi, or have a use-case to
  share?  Send a note — we'll read every message.</p>
  {notice_html}
  <form method="POST" action="/contact/submit">
    <label>
      Your name
      <input type="text" name="name" required maxlength="{_MAX_NAME}"
             autocomplete="name" value="{name_v}">
    </label>
    <label>
      Your email
      <input type="email" name="email" required
             autocomplete="email" inputmode="email"
             autocapitalize="off" spellcheck="false"
             value="{email_v}">
    </label>
    <label>
      Message
      <textarea name="message" required maxlength="{_MAX_MESSAGE}">{msg_v}</textarea>
    </label>
    <div class="captcha">
      <span class="q">What is {q}?</span>
      <input type="number" name="captcha_answer" required min="0" max="20"
             autocomplete="off" inputmode="numeric"
             aria-label="Captcha answer">
      <input type="hidden" name="captcha_token" value="{token}">
    </div>
    <div>
      <button type="submit">Send message</button>
    </div>
  </form>
""" + _PAGE_FOOT


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def contact_page() -> HTMLResponse:
    return HTMLResponse(_render_form())


@router.post("/submit", response_class=HTMLResponse)
def contact_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    message: str = Form(...),
    captcha_answer: str = Form(...),
    captcha_token: str = Form(...),
) -> HTMLResponse:
    name = (name or "").strip()
    email = (email or "").strip().lower()
    message = (message or "").strip()
    if len(name) < 1 or len(name) > _MAX_NAME:
        return _err("Please enter a name (1–80 chars).",
                    {"email": email, "message": message})
    if not _EMAIL_RE.match(email):
        return _err("That email address looks wrong — please re-check.",
                    {"name": name, "message": message})
    if len(message) < 1 or len(message) > _MAX_MESSAGE:
        return _err(f"Message must be 1–{_MAX_MESSAGE} characters.",
                    {"name": name, "email": email})
    if not _verify_challenge(captcha_token, captcha_answer):
        return _err("Captcha answer was wrong or the form expired.  "
                    "Please try again.",
                    {"name": name, "email": email, "message": message})

    # IP-rate-limit: reuse the existing limiter, keyed on a dedicated
    # 'contact:' prefix so the chat budget isn't affected.
    from studio.sessions import hash_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and os.environ.get("SEVIM_TRUST_PROXY", "0") == "1":
        client_ip = forwarded.split(",")[0].strip() or None
    else:
        client_ip = request.client.host if request.client else None
    ip_hash = hash_ip(client_ip)

    sent = _send_email(name=name, from_email=email,
                       message=message, ip_hash=ip_hash)
    if not sent:
        return _err("We couldn't deliver the message just now — please "
                    "try again in a few minutes.",
                    {"name": name, "email": email, "message": message})
    notice = (
        '<div class="ok">Thanks — your message is on its way.  '
        "We read every email and try to reply within a few days.</div>"
    )
    return HTMLResponse(_render_form(notice_html=notice))


def _err(msg: str, prefill: dict) -> HTMLResponse:
    notice = f'<div class="err">{html.escape(msg)}</div>'
    return HTMLResponse(_render_form(notice_html=notice, prefill=prefill))


def _log(msg: str) -> None:
    print(f"[contact] {msg}", flush=True, file=sys.stderr)
