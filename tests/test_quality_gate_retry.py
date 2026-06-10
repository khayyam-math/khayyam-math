"""Unit tests for the quality gate's flake-tolerant retry.

These exercise the pure verdict logic in ``evaluate_with_retry`` by
stubbing ``_evaluate_prompt`` (so no server / LLM is needed): a check
should count as failed only when it fails on BOTH attempts; a check that
passes on either attempt is a flake and must not block the deploy.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

qg = importlib.import_module("infra.quality_gate")


def _pr(checks, error=None):
    pr = qg.PromptResult(prompt="p", canvas_id="c", duration_s=1.0,
                         ttfb_s=0.1, raw_svg="", server_log="", error=error)
    for name, passed in checks:
        pr.add(name, "Test", passed)
    return pr


class _Tp:
    key = "stub"
    prompt = "stub prompt"
    focus_only = False


def _run(monkeypatch, attempt1, attempt2=None, env="1"):
    monkeypatch.setenv("SEVIM_GATE_RETRY_FLAKES", env)
    seq = [a for a in (attempt1, attempt2) if a is not None]
    calls = {"i": 0}

    def fake_eval(tp, logbuf):
        pr = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return pr

    monkeypatch.setattr(qg, "_evaluate_prompt", fake_eval)
    return qg.evaluate_with_retry(_Tp(), logbuf=None), calls


def _failed(pr):
    return {c.name for c in pr.checks if not c.passed}


def test_clean_first_attempt_does_not_retry(monkeypatch):
    a1 = _pr([("prompt completed", True), ("total < 90s", True)])
    (pr, healed), calls = _run(monkeypatch, a1)
    assert calls["i"] == 1, "should not retry when attempt 1 is clean"
    assert _failed(pr) == set()
    assert healed == []


def test_transient_flake_self_heals(monkeypatch):
    # Perf failed once, passes on retry → not a regression.
    a1 = _pr([("prompt completed", True), ("total < 90s", False)])
    a2 = _pr([("prompt completed", True), ("total < 90s", True)])
    (pr, healed), calls = _run(monkeypatch, a1, a2)
    assert calls["i"] == 2, "should retry once after a failure"
    assert _failed(pr) == set(), "the flake must be cleared"
    assert healed == []  # passed on retry directly → nothing to flip


def test_check_failing_both_attempts_is_a_real_regression(monkeypatch):
    a1 = _pr([("prompt completed", True), ("text inside viewBox", False)])
    a2 = _pr([("prompt completed", True), ("text inside viewBox", False)])
    (pr, healed), _ = _run(monkeypatch, a1, a2)
    assert "text inside viewBox" in _failed(pr), "persistent fail must block"


def test_new_flake_on_retry_is_suppressed(monkeypatch):
    # A DIFFERENT check fails on attempt 2 than attempt 1 — both are
    # flakes (each passed on the other attempt) and must be suppressed.
    a1 = _pr([("prompt completed", True),
              ("total < 90s", False), ("text inside viewBox", True)])
    a2 = _pr([("prompt completed", True),
              ("total < 90s", True), ("text inside viewBox", False)])
    (pr, healed), _ = _run(monkeypatch, a1, a2)
    assert _failed(pr) == set(), f"both flakes should clear, got {_failed(pr)}"
    assert "text inside viewBox" in healed


def test_hard_error_is_never_suppressed(monkeypatch):
    a1 = _pr([("prompt completed", False)], error="ReadTimeout")
    (pr, healed), calls = _run(monkeypatch, a1)
    assert calls["i"] == 1, "a hard error is not retried as a flake"
    assert "prompt completed" in _failed(pr)


def test_prompt_completed_never_flake_suppressed(monkeypatch):
    # Even if attempt 1 completed, a retry that fails to complete must
    # not be masked away as a flake.
    a1 = _pr([("prompt completed", True), ("total < 90s", False)])
    a2 = _pr([("prompt completed", False)], error="ConnectError")
    (pr, healed), _ = _run(monkeypatch, a1, a2)
    # Retry errored → we fall back to attempt 1's verdict (its perf fail
    # stands rather than being silently dropped).
    assert "total < 90s" in _failed(pr)


def test_retry_disabled_by_env(monkeypatch):
    a1 = _pr([("prompt completed", True), ("total < 90s", False)])
    a2 = _pr([("prompt completed", True), ("total < 90s", True)])
    (pr, healed), calls = _run(monkeypatch, a1, a2, env="0")
    assert calls["i"] == 1, "retry must be off when SEVIM_GATE_RETRY_FLAKES=0"
    assert "total < 90s" in _failed(pr)
