"""The cost guard's per-IP ceiling, used where sign-in is off.

The session-keyed cap is keyed on a localStorage UUID.  With magic-link
auth in front of it that is fine, because a session belongs to a verified
person.  The self-hosted deployment serves anonymously, where clearing
site data hands out a fresh budget and the cap stops meaning anything —
so it also caps per IP, which costs real effort to rotate.

The flag defaults OFF so the AWS deployment, which still requires
sign-in, behaves exactly as it did before.
"""
from __future__ import annotations

import pytest

from studio import sessions


class _FakeTelemetry:
    """Stands in for the telemetry DB; records which lookups happened."""

    def __init__(self, session_spend: float = 0.0, ip_spend: float = 0.0,
                 ip_raises: bool = False) -> None:
        self._session_spend = session_spend
        self._ip_spend = ip_spend
        self._ip_raises = ip_raises
        self.ip_lookups: list[str] = []

    def session_cost(self, session_id: str, since_s: float = 86400.0) -> float:
        return self._session_spend

    def ip_cost(self, ip_hash: str, since_s: float = 86400.0) -> float:
        self.ip_lookups.append(ip_hash)
        if self._ip_raises:
            raise RuntimeError("telemetry down")
        return self._ip_spend


@pytest.fixture
def guard_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEVIM_COST_GUARD", "1")
    monkeypatch.setenv("SEVIM_COST_DAILY_MAX_USD", "10.00")

    def _install(tel: _FakeTelemetry):
        monkeypatch.setattr("sevim.telemetry.get_telemetry", lambda: tel)
        return tel
    return _install


def test_off_by_default_ip_is_never_consulted(guard_on, monkeypatch):
    """AWS keeps sign-in, so nothing about its behaviour may change."""
    monkeypatch.delenv("SEVIM_COST_GUARD_BY_IP", raising=False)
    tel = guard_on(_FakeTelemetry(session_spend=0.0, ip_spend=999.0))
    assert sessions.check_cost_guard("s1", ip_hash="deadbeef") is None
    assert tel.ip_lookups == []          # no per-IP query at all


def test_ip_over_cap_blocks_even_with_a_fresh_session(guard_on, monkeypatch):
    """The whole point: a brand-new session_id must not reset the budget."""
    monkeypatch.setenv("SEVIM_COST_GUARD_BY_IP", "1")
    guard_on(_FakeTelemetry(session_spend=0.0, ip_spend=12.50))
    msg = sessions.check_cost_guard("brand-new-session", ip_hash="deadbeef")
    assert msg is not None
    assert "network" in msg.lower()
    assert "12.50" in msg and "10.00" in msg


def test_ip_under_cap_allows(guard_on, monkeypatch):
    monkeypatch.setenv("SEVIM_COST_GUARD_BY_IP", "1")
    guard_on(_FakeTelemetry(session_spend=0.0, ip_spend=9.99))
    assert sessions.check_cost_guard("s1", ip_hash="deadbeef") is None


def test_session_cap_still_applies_when_ip_is_clean(guard_on, monkeypatch):
    """Either ceiling rejects on its own."""
    monkeypatch.setenv("SEVIM_COST_GUARD_BY_IP", "1")
    guard_on(_FakeTelemetry(session_spend=11.0, ip_spend=0.0))
    msg = sessions.check_cost_guard("s1", ip_hash="deadbeef")
    assert msg is not None and "session" in msg.lower()


def test_missing_ip_hash_falls_back_to_session_only(guard_on, monkeypatch):
    monkeypatch.setenv("SEVIM_COST_GUARD_BY_IP", "1")
    tel = guard_on(_FakeTelemetry(session_spend=0.0, ip_spend=999.0))
    assert sessions.check_cost_guard("s1", ip_hash=None) is None
    assert tel.ip_lookups == []


def test_telemetry_failure_fails_open(guard_on, monkeypatch):
    """A DB hiccup must never 500 the chat endpoint."""
    monkeypatch.setenv("SEVIM_COST_GUARD_BY_IP", "1")
    guard_on(_FakeTelemetry(session_spend=0.0, ip_raises=True))
    assert sessions.check_cost_guard("s1", ip_hash="deadbeef") is None


def test_guard_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("SEVIM_COST_GUARD", "0")
    monkeypatch.setenv("SEVIM_COST_GUARD_BY_IP", "1")
    assert sessions.check_cost_guard("s1", ip_hash="deadbeef") is None
