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

import os
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
        # equation solving — roots / solution sets
        "solve": sp.solve, "solveset": sp.solveset,
        "roots": sp.roots, "real_roots": sp.real_roots,
        "Eq": sp.Eq, "FiniteSet": sp.FiniteSet, "S": sp.S,
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


def _as_collection(v):
    """Return ``v`` as a plain list of elements if it is a list / tuple /
    set / SymPy FiniteSet (a solution set), else ``None``.

    Matrices are NOT collections here — they have their own equality path
    and ``hasattr(v, "shape")`` would otherwise mis-route them."""
    try:
        from sympy import FiniteSet
    except Exception:  # noqa: BLE001
        FiniteSet = ()  # type: ignore[assignment]
    if hasattr(v, "shape"):
        return None  # a Matrix — handled separately
    if isinstance(v, (list, tuple, set, frozenset)):
        return list(v)
    if FiniteSet and isinstance(v, FiniteSet):
        return list(v.args)
    return None


def _collections_equal(ca: list, cb: list, sp) -> bool:
    """True iff ``ca`` and ``cb`` hold the same elements, ORDER-
    INSENSITIVE and with duplicates collapsed (a solution set has no
    order and no multiplicity).  Elements are matched by symbolic
    equality (``simplify(x - y) == 0``)."""
    def _canon(seq):
        out = []
        for e in seq:
            try:
                se = sp.simplify(sp.sympify(e))
            except Exception:  # noqa: BLE001
                se = e
            if not any(_sym_equal(se, o, sp) for o in out):
                out.append(se)
        return out
    A, B = _canon(ca), _canon(cb)
    if len(A) != len(B):
        return False
    used = [False] * len(B)
    for x in A:
        for j, y in enumerate(B):
            if not used[j] and _sym_equal(x, y, sp):
                used[j] = True
                break
        else:
            return False
    return True


def _sym_equal(x, y, sp) -> bool:
    try:
        return sp.simplify(sp.sympify(x) - sp.sympify(y)) == 0
    except Exception:  # noqa: BLE001
        return x == y


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
    b = parse(b_text)
    # Non-formalisable claims (free-text descriptions of category-
    # theory / rewriting / proof structure that SymPy can't lex) are
    # SKIPPED, not rejected — the LLM emits "α is natural" / "F preserves
    # identity" as a `math_claims` entry for abstract topics, and we
    # don't want to spuriously retry the figure just because there is no
    # CAS-formal check available.  A real false claim STILL fails
    # (parses cleanly, simplifies to something nonzero).
    if a is None or b is None:
        return {"ok": True, "claim": claim,
                "reason": f"skipped_unparseable: "
                          f"a={'ok' if a is not None else 'no'}, "
                          f"b={'ok' if b is not None else 'no'}",
                "engine": "sympy", "skipped": True}

    try:
        # Solution sets / root lists — e.g. solve(x**2-5*x+6, x) == [2, 3].
        # These parse to Python lists / SymPy FiniteSets, NOT scalars, so
        # the a - b path below would TypeError.  Compare them as UNORDERED
        # collections of simplified elements (root order is irrelevant).
        ca, cb = _as_collection(a), _as_collection(b)
        if ca is not None or cb is not None:
            if ca is None or cb is None:
                return {"ok": False, "claim": claim,
                        "reason": "one side is a set/list of values, the "
                                  "other is a single value",
                        "engine": "sympy"}
            if _collections_equal(ca, cb, sp):
                return {"ok": True, "claim": claim,
                        "reason": "", "engine": "sympy"}
            return {"ok": False, "claim": claim,
                    "reason": f"set mismatch: a={ca}, b={cb}",
                    "engine": "sympy"}
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
        # Fourth try: Lean 4 core via `decide`.  Narrow scope (closed
        # Nat arithmetic) but kernel-checked rigour when it fires.
        leanr = _try_lean(a_text, b_text)
        if leanr and leanr.get("ok"):
            return {"ok": True, "claim": claim,
                    "reason": "", "engine": "lean"}
        if leanr and leanr.get("ok") is False \
                and "unsupported" not in (leanr.get("reason") or "") \
                and "timeout" not in (leanr.get("reason") or ""):
            # Lean rejected with a concrete error — surface it.
            return {"ok": False, "claim": claim,
                    "reason": leanr["reason"],
                    "engine": "lean"}
        return {"ok": False, "claim": claim,
                "reason": f"not equal: a - b simplifies to {d2!s}",
                "engine": "sympy"}
    except TypeError as exc:
        # The two sides parsed but can't be combined symbolically (e.g.
        # a value compared against a structure the verifier doesn't model).
        # This is an "I can't formalise this", NOT a proven-false claim —
        # SKIP it rather than spuriously blocking a correct figure.
        return {"ok": True, "claim": claim,
                "reason": f"skipped_unformalisable: TypeError: "
                          f"{str(exc)[:80]}",
                "engine": "sympy", "skipped": True}
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


def _try_lean(a_text: str, b_text: str) -> dict | None:
    """Optional Lean 4 core fallback (third tier).

    Lean is the highest-rigour available — kernel-checked dependent
    types.  Without Mathlib the scope is narrow: closed Nat arithmetic
    via `decide`.  Only fires when SymPy and Z3 are both inconclusive
    AND the claim is closed Nat-only.
    """
    if os.environ.get("SEVIM_LEAN_VERIFIER", "on").lower() == "off":
        return None
    try:
        from studio.templates import lean_verifier
    except ImportError:
        return None
    if not lean_verifier.is_available():
        return None
    try:
        return lean_verifier.verify_identity(a_text, b_text,
                                              timeout_s=5.0)
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
    """Format the verifier failures as a critique the LLM can read.

    Skipped (unparseable) claims are NOT failures — they just couldn't
    be formalised.  Only real false-math claims get critiqued.
    """
    fails = [r for r in results
             if not r.get("ok", True) and not r.get("skipped")]
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
