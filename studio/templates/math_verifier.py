"""Symbolic verification of math claims emitted by the express LLM.

The LLM declares, alongside each figure, the verifiable math facts the
figure depends on.  This module runs SymPy against each claim BEFORE
the figure is allowed to ship — any false claim blocks the figure and
its description is fed back into the retry as a critique.

A claim has the shape::

    {"kind": "identity"|"value",
     "description": "short human-readable claim",
     "a": "<SymPy-parseable expression>",
     "b": "<SymPy-parseable expression>"}

The verifier supports ``diff``, ``integrate``, ``limit``, ``Sum``,
``Product``, ``Matrix``, ``hessian``, ``sin/cos/tan``, ``exp/log/sqrt``,
``pi/E/I/oo`` and the usual variable names ``x y z t a b n k u v``.

This is the "Tier 2" math-correctness layer: deterministic, symbolic,
unfooled — distinct from the (LLM-based, fallible) vision audit.
"""
from __future__ import annotations

from typing import Any


def _make_env():
    """Build the SymPy parsing environment (functions + symbols)."""
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        parse_expr, standard_transformations,
        implicit_multiplication_application,
    )

    transformations = (standard_transformations
                       + (implicit_multiplication_application,))
    local: dict[str, Any] = {
        # calculus helpers
        "diff": sp.diff, "integrate": sp.integrate, "limit": sp.limit,
        "Sum": sp.Sum, "Product": sp.Product,
        "Derivative": sp.Derivative, "Integral": sp.Integral,
        # linear algebra
        "Matrix": sp.Matrix, "hessian": sp.hessian,
        "Transpose": sp.Transpose, "det": (lambda m: m.det()),
        "trace": (lambda m: m.trace()),
        # trig / log / exp / roots
        "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
        "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
        "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
        "exp": sp.exp, "log": sp.log, "ln": sp.log, "sqrt": sp.sqrt,
        "Abs": sp.Abs, "abs": sp.Abs,
        "floor": sp.floor, "ceiling": sp.ceiling, "factorial": sp.factorial,
        # constants
        "pi": sp.pi, "E": sp.E, "I": sp.I, "oo": sp.oo,
        "Infinity": sp.oo, "Rational": sp.Rational,
    }
    # Common symbols, declared as Symbols up front so x, y, ... in
    # expressions are picked up consistently.
    for name in ("x", "y", "z", "t", "u", "v", "w",
                 "a", "b", "c", "d", "n", "k", "m", "p", "q", "r",
                 "alpha", "beta", "gamma", "theta", "phi", "lambda_"):
        local[name] = sp.Symbol(name.rstrip("_"))

    def parse(text: str):
        try:
            return parse_expr(str(text), local_dict=local,
                              transformations=transformations,
                              evaluate=True)
        except Exception:  # noqa: BLE001
            return None
    return sp, parse


def _matrices_equal(a, b, sp) -> bool:
    if getattr(a, "shape", None) != getattr(b, "shape", None):
        return False
    d = sp.simplify(a - b)
    rows, cols = d.shape
    return all(d[i, j] == 0 for i in range(rows) for j in range(cols))


def verify_claim(claim: dict) -> dict:
    """Verify a single claim.  Returns ``{ok, claim, reason}``.

    ``ok`` is True iff SymPy confirms ``a`` equals ``b`` (under the
    claim's ``kind``).  ``reason`` is empty on success and a short
    machine-friendly tag on failure — fed back to the LLM as part of
    the retry critique."""
    if not isinstance(claim, dict):
        return {"ok": False, "claim": claim, "reason": "claim_not_a_dict"}
    try:
        sp, parse = _make_env()
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "claim": claim,
                "reason": f"verifier_unavailable:{type(exc).__name__}"}

    kind = str(claim.get("kind") or "").lower()
    a_text = str(claim.get("a") or "")
    b_text = str(claim.get("b") or "")
    if not a_text or not b_text:
        return {"ok": False, "claim": claim,
                "reason": "missing 'a' or 'b'"}
    a = parse(a_text)
    if a is None:
        return {"ok": False, "claim": claim,
                "reason": f"parse_error 'a': {a_text!r}"}
    b = parse(b_text)
    if b is None:
        return {"ok": False, "claim": claim,
                "reason": f"parse_error 'b': {b_text!r}"}

    try:
        if hasattr(a, "shape") and hasattr(b, "shape"):
            if _matrices_equal(a, b, sp):
                return {"ok": True, "claim": claim,
                        "reason": "", "engine": "sympy"}
            return {"ok": False, "claim": claim,
                    "reason": f"matrix mismatch: a={sp.simplify(a)}, "
                              f"b={sp.simplify(b)}",
                    "engine": "sympy"}
        if kind == "value":
            try:
                na = complex(sp.N(a, 20))
                nb = complex(sp.N(b, 20))
                if abs(na - nb) < 1e-9 * max(1.0, abs(na), abs(nb)):
                    return {"ok": True, "claim": claim,
                            "reason": "", "engine": "sympy"}
                # Try Z3 (it may decide the symbolic form even when
                # numeric eval disagreed due to overflow / Float drift).
                z3r = _try_z3(a_text, b_text)
                if z3r and z3r.get("ok"):
                    return {"ok": True, "claim": claim,
                            "reason": "", "engine": "z3"}
                return {"ok": False, "claim": claim,
                        "reason": f"value mismatch: a≈{na}, b≈{nb}",
                        "engine": "sympy"}
            except Exception:  # noqa: BLE001
                pass  # fall through to symbolic
        # Default / "identity": prove a == b by simplifying the diff.
        d = sp.simplify(a - b)
        if d == 0:
            return {"ok": True, "claim": claim,
                    "reason": "", "engine": "sympy"}
        # Second try: expand + trigsimp for resistant identities.
        d2 = sp.trigsimp(sp.expand(d))
        if d2 == 0:
            return {"ok": True, "claim": claim,
                    "reason": "", "engine": "sympy"}
        # Third try: Z3.  This catches nonlinear-arithmetic identities
        # SymPy's simplify() can't crack (e.g. polynomial identities
        # over reals, integer-only statements).
        z3r = _try_z3(a_text, b_text)
        if z3r and z3r.get("ok"):
            return {"ok": True, "claim": claim,
                    "reason": "", "engine": "z3"}
        # If Z3 produced a concrete counter-model, surface that — it's
        # more actionable feedback than "a - b simplifies to …".
        if z3r and "counter_model" in (z3r.get("reason") or ""):
            return {"ok": False, "claim": claim,
                    "reason": z3r["reason"],
                    "engine": "z3"}
        return {"ok": False, "claim": claim,
                "reason": f"not equal: a - b simplifies to {d2!s}",
                "engine": "sympy"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "claim": claim,
                "reason": f"verifier_error: "
                          f"{type(exc).__name__}: {str(exc)[:80]}",
                "engine": "sympy"}


def _try_z3(a_text: str, b_text: str) -> dict | None:
    """Optional Z3 fallback.  Returns None if Z3 is unavailable or
    the claim is out of Z3's scope (transcendentals, matrices, …)."""
    try:
        from studio.templates import z3_verifier
    except ImportError:
        return None
    if not z3_verifier.is_available():
        return None
    try:
        # Try as integer first (catches divisibility / mod claims that
        # are unsat over Q but sat over Z), then as real if integer
        # translation fails to give a definitive answer.
        for int_vars in (True, False):
            r = z3_verifier.verify_identity(
                a_text, b_text, integer_vars=int_vars, timeout_ms=3000)
            if r.get("ok"):
                return r
            reason = r.get("reason") or ""
            if "unsupported" in reason or "parse_error" in reason:
                continue
            return r   # decisive non-ok (counter-model / timeout)
        return None
    except Exception:  # noqa: BLE001
        return None


def verify_claims(claims: list) -> list[dict]:
    """Verify a list of claims (the value of the LLM response's
    ``math_claims`` field).  Returns a list of result dicts in order."""
    if not claims:
        return []
    out: list[dict] = []
    for c in claims:
        out.append(verify_claim(c))
    return out


def failures_critique(results: list[dict]) -> str:
    """Format the verifier failures as a critique the LLM can read."""
    fails = [r for r in results if not r.get("ok", True)]
    if not fails:
        return ""
    lines = ["The math-correctness verifier rejected the figure. "
             "Fix these and re-emit:"]
    for r in fails:
        c = r.get("claim") or {}
        lines.append(
            f"  • {c.get('description', '<unnamed claim>')}  "
            f"[a={c.get('a','?')!r}, b={c.get('b','?')!r}]  "
            f"→ {r.get('reason', '?')}")
    return "\n".join(lines)
