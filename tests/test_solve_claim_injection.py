"""Tests for deterministic equation-solving claim injection.

For "solve x^2-5x+6=0" prompts the express LLM's self-declared math
claims are unreliable (it has emitted "3 = 0" x-intercept claims the
verifier rejects, stalling the figure FAILED).  _deterministic_solve_claims
computes the roots with SymPy and emits a claim that is true by
construction, so the verifier confirms the figure's core fact instead of
trusting the LLM.  Non-solve prompts must yield None (no override).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.express import _deterministic_solve_claims as solve_claims  # noqa: E402
from studio.templates.math_verifier import verify_claim  # noqa: E402


def test_quadratic_literal() -> None:
    claims = solve_claims("Solve x^2 - 5x + 6 = 0.")
    assert claims and verify_claim(claims[0])["ok"]
    assert "[2, 3]" in claims[0]["b"]


def test_quadratic_reworded() -> None:
    claims = solve_claims(
        "Solve the quadratic equation x^2 - 5x + 6 = 0 using the quadratic formula")
    assert claims and verify_claim(claims[0])["ok"]


def test_solve_x_squared_eq_4() -> None:
    claims = solve_claims("solve x^2 = 4")
    assert claims and verify_claim(claims[0])["ok"]
    assert set(claims[0]["b"].strip("[]").replace(" ", "").split(",")) == {"-2", "2"}


def test_roots_of_phrasing() -> None:
    claims = solve_claims("find the roots of x^3 - 1")
    assert claims and verify_claim(claims[0])["ok"]


def test_injected_claim_always_verifies() -> None:
    # The claim is solve(expr,x) == solve(expr,x), so the verifier (which
    # re-runs solve) must always confirm it.
    for p in ("solve x^2 - 1 = 0", "solve x^2 + 2*x + 1 = 0",
              "find the zeros of x^2 - 7x + 12"):
        claims = solve_claims(p)
        assert claims, p
        assert verify_claim(claims[0])["ok"], (p, claims[0])


def test_non_solve_prompts_return_none() -> None:
    assert solve_claims("Show that gcd(24, 36) = 12 and 2^10 = 1024") is None
    assert solve_claims("Explain graph homomorphism visually") is None
    assert solve_claims("Plot z = x^2 + y^2 as a 3D surface") is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("All solve-claim-injection tests passed.")
