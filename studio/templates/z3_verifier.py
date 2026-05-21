"""Z3-backed math claim verifier (second-tier after SymPy).

SymPy is great at symbolic identities it can simplify.  Z3 is great at
quantifier-free first-order theories — linear/nonlinear real arithmetic,
integer arithmetic, equality, booleans.  When SymPy can't simplify a
claim to ``0``, we translate it into Z3 and ask the solver whether
``a != b`` has any model.  If UNSAT, the identity is proved over the
declared domain.

What this catches that SymPy misses:
  * Nonlinear inequalities over reals.
  * Statements that hold under integer assumptions but not over Q.
  * Boolean / arithmetic mixes that defeat simplify().

What it can NOT do:
  * Transcendental functions over reals (Z3's nra logic is undecidable
    + incomplete on sin/cos/log).  We fall through to SymPy.
  * Matrix equations beyond element-wise scalar predicates.
"""
from __future__ import annotations

from typing import Any, Optional


def is_available() -> bool:
    try:
        import z3  # noqa: F401
        return True
    except ImportError:
        return False


# ──────────────────────────────────────────────────────────────────────
# SymPy → Z3 translation
# ──────────────────────────────────────────────────────────────────────
def _translate(expr, sp_mod, z3_mod, var_map: dict[str, Any]):
    """Translate a SymPy expression into a Z3 expression.

    Returns the Z3 term or raises ValueError if the SymPy expression
    contains constructs Z3 can't handle (sin/cos/log/integrate/etc.).
    """
    sp = sp_mod
    z3 = z3_mod

    if expr.is_Integer:
        return z3.IntVal(int(expr))
    if expr.is_Rational:
        return z3.RealVal(f"{expr.p}/{expr.q}")
    if expr.is_Float:
        return z3.RealVal(float(expr))
    if expr is sp.true:
        return z3.BoolVal(True)
    if expr is sp.false:
        return z3.BoolVal(False)
    if expr.is_Symbol:
        name = expr.name
        if name in var_map:
            return var_map[name]
        # Default symbols to Real.  Caller can override via var_map.
        z = z3.Real(name)
        var_map[name] = z
        return z
    if expr.is_Add:
        args = [_translate(a, sp, z3, var_map) for a in expr.args]
        out = args[0]
        for a in args[1:]:
            out = out + a
        return out
    if expr.is_Mul:
        args = [_translate(a, sp, z3, var_map) for a in expr.args]
        out = args[0]
        for a in args[1:]:
            out = out * a
        return out
    if expr.is_Pow:
        base, exp = expr.args
        # Z3 nonlinear arithmetic handles integer exponents.
        if exp.is_Integer:
            tb = _translate(base, sp, z3, var_map)
            e = int(exp)
            if e == 0:
                return z3.RealVal(1)
            out = tb
            for _ in range(abs(e) - 1):
                out = out * tb
            if e < 0:
                out = 1 / out
            return out
        # Rational exponent → Power with declared variables.  Z3 can
        # still reason as long as we keep it nonlinear-real.
        if exp.is_Rational:
            tb = _translate(base, sp, z3, var_map)
            te = _translate(exp, sp, z3, var_map)
            return tb ** te
        raise ValueError(f"non-integer power: {expr}")
    if expr.func == sp.Abs:
        inner = _translate(expr.args[0], sp, z3, var_map)
        return z3.If(inner >= 0, inner, -inner)
    if expr.func == sp.Min:
        args = [_translate(a, sp, z3, var_map) for a in expr.args]
        out = args[0]
        for a in args[1:]:
            out = z3.If(a < out, a, out)
        return out
    if expr.func == sp.Max:
        args = [_translate(a, sp, z3, var_map) for a in expr.args]
        out = args[0]
        for a in args[1:]:
            out = z3.If(a > out, a, out)
        return out
    if expr is sp.pi:
        # Z3 has no symbolic pi.  Use a fresh variable + the bounds
        # SymPy would use for nsimplify; but in practice pi-bearing
        # claims need transcendental reasoning, which Z3 can't do.
        # Signal "out of scope" by raising.
        raise ValueError("pi (transcendental constant) not supported")
    if expr is sp.E:
        raise ValueError("E (transcendental constant) not supported")
    if expr is sp.I:
        raise ValueError("I (imaginary unit) not supported")

    # Function call we don't handle (sin/cos/log/exp/sqrt/factorial…).
    name = type(expr).__name__
    raise ValueError(f"unsupported expression: {name}({expr})")


# ──────────────────────────────────────────────────────────────────────
# Top-level verifier
# ──────────────────────────────────────────────────────────────────────
def verify_identity(a_text: str, b_text: str,
                    *, integer_vars: bool = False,
                    timeout_ms: int = 4000) -> dict:
    """Try to prove a == b via Z3.

    Returns ``{ok, reason}``:
      * ok=True            — Z3 proved unsat(a ≠ b), i.e. identity holds.
      * ok=False, reason   — Z3 found a counter-model, or translation
                              not supported (caller should fall back).
    """
    try:
        import z3
        import sympy as sp
        from sympy.parsing.sympy_parser import (
            parse_expr, standard_transformations,
            implicit_multiplication_application,
        )
    except ImportError as exc:
        return {"ok": False, "reason": f"z3_unavailable:{exc}"}

    transformations = (standard_transformations
                       + (implicit_multiplication_application,))
    try:
        a = parse_expr(a_text, transformations=transformations)
        b = parse_expr(b_text, transformations=transformations)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False,
                "reason": f"z3_parse_error:{type(exc).__name__}"}

    # Build var map.  Default Real; if the caller asked for Int, use Int.
    syms = a.free_symbols | b.free_symbols
    var_map: dict[str, Any] = {}
    for s in syms:
        var_map[s.name] = (z3.Int(s.name) if integer_vars
                           else z3.Real(s.name))

    try:
        za = _translate(a, sp, z3, var_map)
        zb = _translate(b, sp, z3, var_map)
    except ValueError as exc:
        return {"ok": False, "reason": f"z3_unsupported:{exc}"}

    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    solver.add(za != zb)
    result = solver.check()
    if result == z3.unsat:
        return {"ok": True, "reason": ""}
    if result == z3.sat:
        try:
            m = solver.model()
            return {"ok": False,
                    "reason": f"z3_counter_model:{m}"}
        except Exception:  # noqa: BLE001
            return {"ok": False, "reason": "z3_counter_model"}
    return {"ok": False, "reason": "z3_timeout_or_unknown"}


def verify_value(a_text: str, b_text: str,
                 *, timeout_ms: int = 4000) -> dict:
    """Try to prove a numerically equals b via Z3 (no symbols)."""
    return verify_identity(a_text, b_text, timeout_ms=timeout_ms)
