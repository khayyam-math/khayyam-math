"""Backend selection and message construction for service.mailer.

The mailer is the one piece the AWS→self-host migration replaced
wholesale, so these tests pin the behaviour that both deployments
depend on: SMTP wins when configured, SES still works when it isn't,
and an unconfigured box degrades to a log line instead of an exception.
"""
from __future__ import annotations

import sys
import types

import pytest

from service import mailer


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("SEVIM_SMTP_HOST", "SEVIM_SMTP_PORT", "SEVIM_SMTP_USER",
                "SEVIM_SMTP_PASSWORD", "SEVIM_SMTP_SSL", "SEVIM_SMTP_TLS",
                "SEVIM_MAIL_FROM", "SEVIM_MAIL_FROM_NAME",
                "SEVIM_SES_FROM_ADDRESS"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Sender identity
# ---------------------------------------------------------------------------

def test_sender_address_wraps_bare_address(monkeypatch):
    monkeypatch.setenv("SEVIM_MAIL_FROM", "noreply@khayyammath.com")
    assert mailer.sender_address() == "Khayyam Math <noreply@khayyammath.com>"


def test_sender_address_leaves_formatted_address_alone(monkeypatch):
    monkeypatch.setenv("SEVIM_MAIL_FROM", "Tutor <hi@khayyammath.com>")
    assert mailer.sender_address() == "Tutor <hi@khayyammath.com>"


def test_sender_address_falls_back_to_legacy_ses_var(monkeypatch):
    """Existing AWS deployments set SEVIM_SES_FROM_ADDRESS; it must keep
    working so a revert to the AWS stack needs no secret rename."""
    monkeypatch.setenv("SEVIM_SES_FROM_ADDRESS", "noreply@khayyammath.com")
    assert "noreply@khayyammath.com" in mailer.sender_address()


def test_sender_address_empty_when_unconfigured():
    assert mailer.sender_address() == ""


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def test_backend_prefers_smtp_when_host_set(monkeypatch):
    monkeypatch.setenv("SEVIM_SMTP_HOST", "smtp-relay.brevo.com")
    assert mailer.backend_name() == "smtp"


def test_backend_falls_back_to_ses(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", types.ModuleType("boto3"))
    assert mailer.backend_name() == "ses"


def test_backend_none_without_smtp_or_boto3(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)
    # A None entry in sys.modules makes `import boto3` raise ImportError,
    # which is exactly the state of a slim self-host image.
    assert mailer.backend_name() == "none"


# ---------------------------------------------------------------------------
# SMTP send
# ---------------------------------------------------------------------------

class _FakeSMTP:
    """Records the transaction so the test can assert on the envelope."""
    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.login_args = None
        self.sent = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent = (msg, from_addr, to_addrs)


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def test_smtp_send_uses_starttls_and_bare_envelope_sender(monkeypatch, fake_smtp):
    monkeypatch.setenv("SEVIM_SMTP_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("SEVIM_SMTP_USER", "apikey")
    monkeypatch.setenv("SEVIM_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SEVIM_MAIL_FROM", "noreply@khayyammath.com")

    assert mailer.send_email("learner@example.com", "Subj", "body") is True

    conn = fake_smtp.instances[0]
    assert (conn.host, conn.port) == ("smtp-relay.brevo.com", 587)
    assert conn.started_tls is True
    assert conn.login_args == ("apikey", "secret")
    msg, envelope_from, to_addrs = conn.sent
    # Relays reject "Name <addr>" as MAIL FROM — the envelope must be bare
    # even though the visible From header carries the display name.
    assert envelope_from == "noreply@khayyammath.com"
    assert to_addrs == ["learner@example.com"]
    assert msg["From"] == "Khayyam Math <noreply@khayyammath.com>"
    assert msg["Subject"] == "Subj"


def test_smtp_implicit_tls_defaults_to_465(monkeypatch, fake_smtp):
    monkeypatch.setenv("SEVIM_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SEVIM_SMTP_SSL", "1")
    monkeypatch.setenv("SEVIM_MAIL_FROM", "noreply@khayyammath.com")

    assert mailer.send_email("a@example.com", "S", "b") is True
    conn = fake_smtp.instances[0]
    assert conn.port == 465
    # Implicit TLS must not also issue STARTTLS — that is a protocol error.
    assert conn.started_tls is False


def test_smtp_html_becomes_multipart_alternative(monkeypatch, fake_smtp):
    monkeypatch.setenv("SEVIM_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SEVIM_MAIL_FROM", "noreply@khayyammath.com")

    mailer.send_email("a@example.com", "S", "plain", html="<p>rich</p>",
                      reply_to="human@example.com")
    msg, _, _ = fake_smtp.instances[0].sent
    assert msg.get_content_type() == "multipart/alternative"
    assert msg["Reply-To"] == "human@example.com"
    bodies = [p.get_content_type() for p in msg.iter_parts()]
    # Plain part first is what deliverability guides ask for.
    assert bodies == ["text/plain", "text/html"]


def test_smtp_failure_returns_false_not_raises(monkeypatch):
    monkeypatch.setenv("SEVIM_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SEVIM_MAIL_FROM", "noreply@khayyammath.com")

    def _boom(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(mailer.smtplib, "SMTP", _boom)
    assert mailer.send_email("a@example.com", "S", "b") is False


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------

def test_no_sender_configured_is_a_soft_failure(monkeypatch, fake_smtp):
    monkeypatch.setenv("SEVIM_SMTP_HOST", "smtp.example.com")
    assert mailer.send_email("a@example.com", "S", "b") is False
    assert fake_smtp.instances == []


def test_empty_recipient_list_is_a_soft_failure(monkeypatch):
    monkeypatch.setenv("SEVIM_MAIL_FROM", "noreply@khayyammath.com")
    assert mailer.send_email([], "S", "b") is False
    assert mailer.send_email("   ", "S", "b") is False


def test_ses_backend_still_sends(monkeypatch):
    """The AWS path must remain intact so reverting to ECS needs no
    code change — only unsetting SEVIM_SMTP_HOST."""
    captured = {}

    class _Client:
        def send_email(self, **kwargs):
            captured.update(kwargs)

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda name: _Client()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setenv("SEVIM_MAIL_FROM", "noreply@khayyammath.com")

    assert mailer.send_email("a@example.com", "S", "plain",
                             html="<p>x</p>", reply_to="r@example.com") is True
    assert captured["Destination"] == {"ToAddresses": ["a@example.com"]}
    assert captured["Message"]["Body"]["Html"]["Data"] == "<p>x</p>"
    assert captured["ReplyToAddresses"] == ["r@example.com"]
