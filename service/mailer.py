"""Provider-agnostic outbound e-mail.

Every message Khayyam Math sends — magic-link sign-in, the contact form,
quality-probe alerts, the daily feedback digest — goes through
:func:`send_email`.  The backend is chosen at call time from the
environment, so the same code runs on a self-hosted box (SMTP) and on
AWS (SES) without a branch at the call sites.

Backend selection, in order:

  1. **SMTP** — when ``SEVIM_SMTP_HOST`` is set.  This is the
     self-hosting path (Brevo, Resend, Fastmail, Postmark, or any
     other relay that speaks SMTP).  Credentials come from
     ``SEVIM_SMTP_USER`` / ``SEVIM_SMTP_PASSWORD``; port defaults to
     587 with STARTTLS.  Set ``SEVIM_SMTP_SSL=1`` for implicit TLS
     (port 465), or ``SEVIM_SMTP_TLS=0`` to disable TLS entirely
     (only sensible for a relay on localhost).

  2. **SES** — when no SMTP host is configured but boto3 is importable.
     This is the legacy AWS path; it keeps working unchanged for
     deployments that still run on ECS.

  3. **Log-only** — when neither is available.  The message is printed
     to stderr and the send reports failure.  Sevim must never crash
     because e-mail is unconfigured.

The sender address comes from ``SEVIM_MAIL_FROM``, falling back to
``SEVIM_SES_FROM_ADDRESS`` so existing deployments and secrets keep
working without a rename.  A bare address is wrapped with the
``SEVIM_MAIL_FROM_NAME`` display name (default "Khayyam Math"), because
spam filters and humans both read a bare address as machine-generated.

All sends are best-effort: :func:`send_email` returns ``True`` on
success and ``False`` on any failure, and never raises.  That matches
the posture of the rest of Sevim's external-service calls — a dead mail
relay degrades sign-in, it doesn't take the site down.
"""
from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

__all__ = ["send_email", "sender_address", "backend_name"]

_DEFAULT_FROM_NAME = "Khayyam Math"


def _log(msg: str) -> None:
    print(f"[mailer] {msg}", file=sys.stderr, flush=True)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _truthy(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Sender identity
# ---------------------------------------------------------------------------

def sender_address(display_name: str | None = None) -> str:
    """Return the configured From header, or "" when unconfigured.

    Reads ``SEVIM_MAIL_FROM`` first, then ``SEVIM_SES_FROM_ADDRESS`` so
    an existing AWS secret keeps working after the migration.  A bare
    address gets wrapped with a display name; an address the operator
    already formatted as ``Name <addr>`` is left alone.
    """
    raw = _env("SEVIM_MAIL_FROM") or _env("SEVIM_SES_FROM_ADDRESS")
    if not raw:
        return ""
    if "<" in raw:
        return raw
    name = display_name or _env("SEVIM_MAIL_FROM_NAME") or _DEFAULT_FROM_NAME
    return formataddr((name, raw))


def backend_name() -> str:
    """Which backend a send would use right now: smtp / ses / none.

    Exposed for /health and the admin diagnostics page so an operator
    can see the mail path without sending a test message.
    """
    if _env("SEVIM_SMTP_HOST"):
        return "smtp"
    try:
        import boto3  # noqa: F401
    except ImportError:
        return "none"
    return "ses"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_email(
    to: str | list[str],
    subject: str,
    text: str,
    html: str | None = None,
    reply_to: str | None = None,
    sender: str | None = None,
) -> bool:
    """Send one message.  Returns True on success, never raises.

    ``to`` accepts a single address or a list.  ``html`` is optional —
    when given, the message goes out as multipart/alternative with the
    plain-text part first, which is what every deliverability guide
    asks for.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [r.strip() for r in recipients if r and r.strip()]
    if not recipients:
        _log("no recipients — nothing to send")
        return False

    from_header = sender or sender_address()
    if not from_header:
        _log("no sender configured (SEVIM_MAIL_FROM / SEVIM_SES_FROM_ADDRESS) "
             f"— skipping send of {subject!r}")
        return False

    backend = backend_name()
    if backend == "smtp":
        return _send_smtp(from_header, recipients, subject, text, html, reply_to)
    if backend == "ses":
        return _send_ses(from_header, recipients, subject, text, html, reply_to)

    _log(f"no mail backend configured — would have sent {subject!r} "
         f"to {', '.join(recipients)}")
    return False


# ---------------------------------------------------------------------------
# SMTP backend
# ---------------------------------------------------------------------------

def _build_message(from_header: str, recipients: list[str], subject: str,
                   text: str, html: str | None,
                   reply_to: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_header
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _send_smtp(from_header: str, recipients: list[str], subject: str,
               text: str, html: str | None, reply_to: str | None) -> bool:
    host = _env("SEVIM_SMTP_HOST")
    use_ssl = _truthy(_env("SEVIM_SMTP_SSL"), False)
    # Implicit TLS is 465; STARTTLS and plaintext both default to 587,
    # which is what every managed relay (Brevo, Resend, Postmark) uses.
    port = int(_env("SEVIM_SMTP_PORT") or (465 if use_ssl else 587))
    user = _env("SEVIM_SMTP_USER")
    password = os.environ.get("SEVIM_SMTP_PASSWORD", "")
    starttls = _truthy(_env("SEVIM_SMTP_TLS"), True) and not use_ssl
    timeout = float(_env("SEVIM_SMTP_TIMEOUT") or "20")

    msg = _build_message(from_header, recipients, subject, text, html, reply_to)
    # The envelope sender must be the bare address — a relay that is
    # handed "Name <addr>" as MAIL FROM rejects the transaction.
    envelope_from = parseaddr(from_header)[1] or from_header

    try:
        if use_ssl:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            smtp = smtplib.SMTP(host, port, timeout=timeout)
        with smtp:
            smtp.ehlo()
            if starttls:
                smtp.starttls()
                smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg, from_addr=envelope_from, to_addrs=recipients)
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"SMTP send to {host}:{port} failed: {type(exc).__name__}: {exc}")
        return False


# ---------------------------------------------------------------------------
# SES backend (legacy AWS path — unchanged behaviour)
# ---------------------------------------------------------------------------

def _send_ses(from_header: str, recipients: list[str], subject: str,
              text: str, html: str | None, reply_to: str | None) -> bool:
    try:
        import boto3
    except ImportError:
        _log("boto3 not installed — cannot send via SES")
        return False
    body: dict[str, dict[str, str]] = {"Text": {"Data": text}}
    if html:
        body["Html"] = {"Data": html}
    kwargs = {
        "Source": from_header,
        "Destination": {"ToAddresses": recipients},
        "Message": {"Subject": {"Data": subject}, "Body": body},
    }
    if reply_to:
        kwargs["ReplyToAddresses"] = [reply_to]
    try:
        boto3.client("ses").send_email(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"SES send_email failed: {type(exc).__name__}: {exc}")
        return False
