"""Admin-page access control and model-selection contract."""
from __future__ import annotations

import importlib
import os

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _make_request(cookie: str | None = None) -> Request:
    """Build a synthetic Starlette Request with an optional cookie."""
    headers = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("ascii")))
    scope = {
        "type": "http", "method": "GET", "path": "/studio/admin",
        "headers": headers, "query_string": b"",
    }
    return Request(scope)


def _sign_cookie(email: str, monkeypatch) -> str:
    """Sign a magic-link cookie the same way the real auth path does."""
    monkeypatch.setenv("SEVIM_AUTH_SECRET", "test-secret-32-bytes-or-more-aaaa")
    from studio import auth as auth_mod
    importlib.reload(auth_mod)
    token = auth_mod.sign({"sub": email}, ttl_s=3600)
    return f"{auth_mod._COOKIE_NAME}={token}"


def test_admin_returns_404_to_anonymous(monkeypatch):
    monkeypatch.setenv("SEVIM_ADMIN_EMAILS", "ara@example.com")
    from studio import app as app_mod
    importlib.reload(app_mod)
    with pytest.raises(HTTPException) as exc:
        app_mod.require_admin(_make_request())
    assert exc.value.status_code == 404


def test_admin_returns_404_to_non_admin(monkeypatch):
    monkeypatch.setenv("SEVIM_ADMIN_EMAILS", "ara@example.com")
    cookie = _sign_cookie("notara@example.com", monkeypatch)
    from studio import app as app_mod
    importlib.reload(app_mod)
    with pytest.raises(HTTPException) as exc:
        app_mod.require_admin(_make_request(cookie))
    assert exc.value.status_code == 404


def test_admin_lets_admin_in(monkeypatch):
    monkeypatch.setenv("SEVIM_ADMIN_EMAILS", "ara@example.com,other@example.com")
    cookie = _sign_cookie("ara@example.com", monkeypatch)
    from studio import app as app_mod
    importlib.reload(app_mod)
    result = app_mod.require_admin(_make_request(cookie))
    assert result == "ara@example.com"


def test_is_admin_case_insensitive(monkeypatch):
    monkeypatch.setenv("SEVIM_ADMIN_EMAILS", "Ara@Example.com")
    cookie = _sign_cookie("ara@example.com", monkeypatch)
    from studio import app as app_mod
    importlib.reload(app_mod)
    assert app_mod.is_admin(_make_request(cookie)) is True


def test_admin_whitelist_empty_means_nobody(monkeypatch):
    monkeypatch.delenv("SEVIM_ADMIN_EMAILS", raising=False)
    cookie = _sign_cookie("ara@example.com", monkeypatch)
    from studio import app as app_mod
    importlib.reload(app_mod)
    with pytest.raises(HTTPException) as exc:
        app_mod.require_admin(_make_request(cookie))
    assert exc.value.status_code == 404


def test_get_active_model_falls_back_when_setting_missing(monkeypatch):
    """``get_active_model`` should never crash even if telemetry isn't
    up.  Resolution order: setting → marked default (if available) →
    first available → marked default regardless of availability.
    """
    # No telemetry → no setting; no env vars → nothing available.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SEVIM_QWEN_VLLM_URL", raising=False)
    from studio import app as app_mod
    importlib.reload(app_mod)
    chosen = app_mod.get_active_model()
    # Catalog has qwen_lora_v4 marked default; nothing is available
    # in this test env, so we fall through to step 4 and return the
    # marked default string for deterministic logging.
    assert chosen == "qwen_lora_v4"


def test_get_active_model_prefers_first_available_when_default_offline(monkeypatch):
    """Qwen is the *marked* default but unavailable in dev → should
    return the first available backend (gpt-4o) without crashing."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.delenv("SEVIM_QWEN_VLLM_URL", raising=False)
    from studio import app as app_mod
    importlib.reload(app_mod)
    assert app_mod.get_active_model() == "gpt-4o"


def test_get_active_model_picks_qwen_when_available(monkeypatch):
    """Once Qwen vLLM is wired up AND the reachability probe passes,
    the marked default takes over.  We stub the probe — the previous
    test version only set the URL env, which is no longer sufficient
    because catalog availability now also requires an HTTP probe."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("SEVIM_QWEN_VLLM_URL", "http://10.0.0.1:8000/v1")
    from studio import app as app_mod
    importlib.reload(app_mod)
    monkeypatch.setattr(app_mod, "_qwen_lora_vllm_reachable", lambda: True)
    assert app_mod.get_active_model() == "qwen_lora_v4"


def test_get_active_model_falls_back_when_qwen_unreachable(monkeypatch):
    """Even if SEVIM_QWEN_VLLM_URL is set, an unreachable vLLM endpoint
    (bootstrap, spot reclaim, OOM crash) must NOT route traffic to
    Qwen — the catalog flips it to 'unavailable' and traffic flows to
    gpt-4o."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("SEVIM_QWEN_VLLM_URL", "http://10.0.0.1:8000/v1")
    from studio import app as app_mod
    importlib.reload(app_mod)
    monkeypatch.setattr(app_mod, "_qwen_lora_vllm_reachable", lambda: False)
    assert app_mod.get_active_model() == "gpt-4o"
