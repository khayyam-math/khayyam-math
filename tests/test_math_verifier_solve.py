"""Regression tests for the math verifier's handling of equation-SOLVING
claims (solution sets / root lists).

Bug history: the express LLM emits, alongside a "solve x^2-5x+6=0"
figure, a claim like::

    {"kind": "identity", "a": "solve(x**2 - 5*x + 6, x)", "b": "[2, 3]"}

This is CORRECT, but the verifier had no `solve` in its SymPy env and
only knew how to compare scalars/matrices via ``a - b`` — so the claim
crashed with ``TypeError: unsupported operand type(s) for -: 'list' and
'list'`` and was marked FAILED.  Every equation-solving figure therefore
tripped the math-correctness gate (solve_quad in infra/quality_gate.py).

The fix: add solve/roots/FiniteSet to the env and compare solution sets
as UNORDERED collections.  A genuinely wrong root set must still FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.templates.math_verifier import verify_claim  # noqa: E402


def _ok(a: str, b: str, kind: str = "identity") -> dict:
    return verify_claim({"kind": kind, "a": a, "b": b})


def test_solve_roots_list_verifies() -> None:
    r = _ok("solve(x**2 - 5*x + 6, x)", "[2, 3]")
    assert r["ok"] and not r.get("skipped"), r


def test_solve_roots_order_insensitive() -> None:
    r = _ok("solve(x**2 - 5*x + 6, x)", "[3, 2]")
    assert r["ok"] and not r.get("skipped"), r


def test_solve_roots_set_form() -> None:
    r = _ok("solve(x**2 - 5*x + 6, x)", "{2, 3}")
    assert r["ok"] and not r.get("skipped"), r


def test_solve_negative_roots() -> None:
    r = _ok("solve(x**2 - 4, x)", "[-2, 2]")
    assert r["ok"] and not r.get("skipped"), r


def test_wrong_root_set_fails() -> None:
    # A genuinely wrong solution set must NOT pass — guards against the
    # fix being so lenient it accepts anything.
    r = _ok("solve(x**2 - 5*x + 6, x)", "[2, 4]")
    assert r["ok"] is False and not r.get("skipped"), r


def test_scalar_identity_unaffected() -> None:
    assert _ok("diff(x**3, x)", "3*x**2")["ok"]


def test_scalar_value_unaffected() -> None:
    assert _ok("2**10", "1024", kind="value")["ok"]


if __name__ == "__main__":
    test_solve_roots_list_verifies()
    test_solve_roots_order_insensitive()
    test_solve_roots_set_form()
    test_solve_negative_roots()
    test_wrong_root_set_fails()
    test_scalar_identity_unaffected()
    test_scalar_value_unaffected()
    print("All math-verifier solve-claim tests passed.")
