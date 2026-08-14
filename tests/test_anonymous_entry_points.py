"""With sign-in off, nothing may route a visitor to an email form.

The landing page's CTAs all point at /studio/auth/login, which is right
only where sign-in exists.  On the self-hosted stack
(SEVIM_AUTH_REQUIRED=0) that sends a visitor to a form asking for an
address they never needed — the exact friction anonymous access removed.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_anon(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEVIM_AUTH_REQUIRED", "0")
    from service.app import app
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client_auth(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEVIM_AUTH_REQUIRED", "1")
    monkeypatch.setenv("SEVIM_AUTH_SECRET", "test-secret-for-signing-cookies")
    from service.app import app
    return TestClient(app, follow_redirects=False)


def test_landing_ctas_point_at_studio_when_anonymous(client_anon):
    r = client_anon.get("/")
    assert r.status_code == 200
    assert "/studio/auth/login" not in r.text
    assert 'href="/studio"' in r.text


def test_login_page_forwards_to_studio_when_anonymous(client_anon):
    """Bookmarks and cached landing pages still point at the old URL."""
    r = client_anon.get("/studio/auth/login")
    assert r.status_code == 302
    assert r.headers["location"] == "/studio"


def test_landing_keeps_sign_in_ctas_when_auth_required(client_auth):
    """AWS still requires sign-in; its landing page must not change."""
    r = client_auth.get("/")
    assert r.status_code == 200
    assert "/studio/auth/login" in r.text


def test_login_page_still_renders_when_auth_required(client_auth):
    r = client_auth.get("/studio/auth/login")
    assert r.status_code == 200
    assert "Email me a link" in r.text
