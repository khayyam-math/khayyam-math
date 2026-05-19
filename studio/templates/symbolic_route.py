"""Symbolic-math route — exact calculus, never LLM arithmetic.

When the prompt asks for a derivative, a Hessian, a gradient, an
integral or a limit, the LLM's only job is to extract the *function*
and the *operation* into a small spec.  SymPy then computes the
result exactly, and matplotlib's mathtext typesets it.  The LLM never
does the algebra and never places pixels — which is why the old
LLM-drawn Hessian came out as garbled, overlapping, wrong text.

``generate_symbolic_svg`` returns ``(svg, narration)`` like the other
template routes, or ``None`` so the caller falls back.
"""
from __future__ import annotations

import io
import json
import re
from typing import Any, Optional

_KEYWORDS = (
    "derivative", "differentiate", "second derivative",
    "partial derivative", "partial derivatives", "gradient",
    "hessian", "jacobian", "integral", "integrate", "antiderivative",
    "limit of", "evaluate the limit",
)


def is_symbolic_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    return any(kw in p for kw in _KEYWORDS)


SYMBOLIC_SPEC_SYSTEM = """\
You extract a symbolic-math task into JSON.  Do NOT compute anything —
just identify the function and the operation.

Return ONLY a JSON object:
{
  "operation": "derivative" | "gradient" | "hessian" | "integral" | "limit",
  "function": "<the function as a Python/SymPy expression>",
  "variables": ["x"] or ["x","y"] ... (all variables, in order),
  "order": 1,            // derivative order (1, 2, ...); omit otherwise
  "wrt": "x",            // single variable to differentiate/integrate by
  "point": {"x": 0, "y": 0},   // OPTIONAL: evaluate the result here
  "limit_to": "0",       // for "limit": the value the variable approaches
  "title": "<short figure title>",
  "intro": "<one sentence: what we are about to do>",
  "takeaway": "<one sentence: what the result means>"
}

Rules:
- Write the function in Python syntax: x**2, sin(x), exp(x), sqrt(x),
  log(x), pi.  Never use ^ for powers.
- "second derivative" / "all second derivatives" of a 2-variable
  function -> operation "hessian".
- "gradient" or "all first partial derivatives" -> "gradient".
- A bare "derivative of f(x)" of a 1-variable function -> "derivative".
- Include "point" only if the prompt names a specific point.
Respond with ONLY the JSON object.
"""


async def llm_emit_symbolic_spec(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    timeout_s: float = 30.0,
) -> Optional[dict]:
    import httpx
    payload = {
        "model": model,
        "max_tokens": 700,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYMBOLIC_SPEC_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload, headers=headers)
        if r.status_code != 200:
            return None
        spec = json.loads(r.json()["choices"][0]["message"]["content"] or "")
    except Exception:  # noqa: BLE001
        return None
    return spec if isinstance(spec, dict) else None


# --------------------------------------------------------------------
# SymPy computation — exact, by construction
# --------------------------------------------------------------------

def _compute(spec: dict) -> Optional[dict]:
    """Run the operation with SymPy.  Returns a structured result:

      {"kind": "matrix"|"steps", "title": str,
       "given": (lhs_latex, rhs_latex),
       "matrix": [[latex,...],...],  "row_labels"/"col_labels": [...],
       "steps": [(lhs_latex, rhs_latex), ...],
       "at_point": (label_latex, value_latex) or None}
    """
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import (
            parse_expr, standard_transformations,
            implicit_multiplication_application,
        )
    except Exception:  # noqa: BLE001
        return None

    op = (spec.get("operation") or "").lower()
    fn_str = str(spec.get("function") or "").strip()
    if not fn_str:
        return None
    var_names = [str(v) for v in (spec.get("variables") or []) if str(v)]
    if not var_names:
        var_names = ["x"]
    syms = {v: sp.Symbol(v) for v in var_names}
    transformations = (standard_transformations
                       + (implicit_multiplication_application,))
    try:
        expr = parse_expr(fn_str, local_dict=syms,
                          transformations=transformations, evaluate=True)
    except Exception:  # noqa: BLE001
        return None

    L = sp.latex
    out: dict[str, Any] = {
        "title": str(spec.get("title") or "Symbolic result"),
        "given": (f"f({', '.join(var_names)})", L(expr)),
        "at_point": None,
    }
    point = spec.get("point") or {}
    subs = {}
    for v in var_names:
        if v in point:
            try:
                subs[syms[v]] = sp.nsimplify(point[v])
            except Exception:  # noqa: BLE001
                pass

    try:
        if op == "hessian":
            vs = [syms[v] for v in var_names]
            H = sp.hessian(expr, vs)
            out["kind"] = "matrix"
            out["matrix"] = [[L(sp.simplify(H[i, j])) for j in range(len(vs))]
                             for i in range(len(vs))]
            out["row_labels"] = var_names
            out["col_labels"] = var_names
            out["caption"] = "Hessian — second-order partial derivatives."
            if subs:
                Hp = H.subs(subs)
                pt = ", ".join(L(subs[syms[v]]) for v in var_names)
                out["at_point_matrix"] = {
                    "label": f"H({pt})",
                    "matrix": [[L(sp.simplify(Hp[i, j]))
                                for j in range(len(vs))]
                               for i in range(len(vs))],
                }
        elif op == "gradient":
            vs = [syms[v] for v in var_names]
            grad = [sp.simplify(sp.diff(expr, v)) for v in vs]
            out["kind"] = "steps"
            out["steps"] = [
                (f"\\partial f/\\partial {var_names[i]}", L(grad[i]))
                for i in range(len(vs))]
            out["caption"] = "Gradient — the first partial derivatives."
            if subs:
                gp = [g.subs(subs) for g in grad]
                out["at_point"] = (
                    "\\nabla f",
                    "(" + ",\\ ".join(L(sp.simplify(g)) for g in gp) + ")")
        elif op == "integral":
            wrt = syms.get(str(spec.get("wrt") or var_names[0]),
                           syms[var_names[0]])
            anti = sp.integrate(expr, wrt)
            out["kind"] = "steps"
            out["steps"] = [
                (f"\\int f\\,d{wrt}", L(anti) + " + C")]
            out["caption"] = "Indefinite integral (antiderivative)."
        elif op == "limit":
            wrt = syms.get(str(spec.get("wrt") or var_names[0]),
                           syms[var_names[0]])
            to = spec.get("limit_to", 0)
            try:
                to_v = sp.nsimplify(to)
            except Exception:  # noqa: BLE001
                to_v = sp.Integer(0)
            lim = sp.limit(expr, wrt, to_v)
            out["kind"] = "steps"
            out["steps"] = [
                (f"\\lim_{{{wrt} \\to {L(to_v)}}} f", L(lim))]
            out["caption"] = "Limit."
        else:  # derivative (default)
            wrt = syms.get(str(spec.get("wrt") or var_names[0]),
                           syms[var_names[0]])
            order = int(spec.get("order") or 1)
            order = max(1, min(order, 6))
            out["kind"] = "steps"
            steps = []
            cur = expr
            for k in range(1, order + 1):
                cur = sp.simplify(sp.diff(cur, wrt))
                prime = "'" * k if k <= 3 else f"^{{({k})}}"
                steps.append((f"f{prime}({wrt})", L(cur)))
            out["steps"] = steps
            out["caption"] = (
                f"Derivative with respect to {wrt}.")
            if subs:
                out["at_point"] = (
                    f"f{'′' * min(order, 3)}",
                    L(sp.simplify(cur.subs(subs))))
    except Exception:  # noqa: BLE001
        return None
    return out


# --------------------------------------------------------------------
# Deterministic renderer — matplotlib mathtext
# --------------------------------------------------------------------

def _draw_matrix(ax, mat: list, top: float, *, fontsize: int = 16,
                 cell_w: float = 0.26, row_h: float = 0.12,
                 color: str = "#222") -> float:
    """Draw a bracketed grid of mathtext cells; return the bottom y."""
    ncol = len(mat[0]) if mat else 0
    nrow = len(mat)
    x0 = 0.5 - (ncol * cell_w) / 2
    for i, row in enumerate(mat):
        for j, cell in enumerate(row):
            ax.text(x0 + cell_w * (j + 0.5), top - row_h * (i + 0.5),
                    f"${cell}$", ha="center", va="center",
                    fontsize=fontsize, color=color)
    by0, by1 = top - row_h * nrow, top
    bx0, bx1 = x0 - 0.03, x0 + cell_w * ncol + 0.03
    for bx, dx in ((bx0, 0.022), (bx1, -0.022)):
        ax.plot([bx, bx], [by0, by1], color=color, lw=2)
        ax.plot([bx, bx + dx], [by1, by1], color=color, lw=2)
        ax.plot([bx, bx + dx], [by0, by0], color=color, lw=2)
    return by0


def _render(result: dict, spec: dict) -> Optional[tuple[str, list[dict]]]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001
        return None

    title = result.get("title") or "Symbolic result"
    given_l, given_r = result.get("given") or ("f", "")
    kind = result.get("kind")

    fig = plt.figure(figsize=(8.4, 6.4), dpi=100)
    try:
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.94, title, ha="center", va="center",
                fontsize=19, fontweight="bold", color="#1a3a5c",
                family="serif")
        ax.text(0.5, 0.82, fr"$\mathrm{{Given:}}\quad {given_l} = {given_r}$",
                ha="center", va="center", fontsize=16, color="#222")

        y = 0.66
        if kind == "matrix":
            mat = result.get("matrix") or []
            if not mat:
                return None
            y = _draw_matrix(ax, mat, top=0.70) - 0.06
        else:  # steps
            steps = result.get("steps") or []
            if not steps:
                return None
            y = 0.66
            for lhs, rhs in steps[:6]:
                ax.text(0.5, y, fr"${lhs} = {rhs}$", ha="center",
                        va="center", fontsize=17, color="#1a3a5c")
                y -= 0.12

        cap = result.get("caption") or ""
        if cap:
            ax.text(0.5, max(y, 0.20), cap, ha="center", va="center",
                    fontsize=12, color="#666", style="italic", family="serif")
            y = max(y, 0.20) - 0.08

        # Evaluated-at-a-point result.
        apm = result.get("at_point_matrix")
        at = result.get("at_point")
        if apm and apm.get("matrix"):
            ax.text(0.5, y, f"$\\mathrm{{At\\ the\\ point:}}\\ "
                    f"{apm.get('label','')}=$", ha="center", va="center",
                    fontsize=14, color="#cc4125")
            _draw_matrix(ax, apm["matrix"], top=y - 0.05, fontsize=15,
                         cell_w=0.18, row_h=0.10, color="#cc4125")
        elif at:
            ax.text(0.5, max(y, 0.07), f"${at[0]} = {at[1]}$", ha="center",
                    va="center", fontsize=15, color="#cc4125")

        buf = io.StringIO()
        fig.savefig(buf, format="svg", bbox_inches="tight")
        svg = buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
    finally:
        plt.close(fig)

    if "<svg" not in svg:
        return None
    # Make the SVG scale to the canvas.
    m = re.search(r"<svg\b[^>]*>", svg)
    if m:
        tag = re.sub(r'\s(?:width|height)="[^"]*"', "", m.group(0))
        tag = tag.replace(
            "<svg",
            '<svg width="100%" style="max-width:100%;height:auto;'
            'display:block;margin:0 auto;"', 1)
        svg = svg[:m.start()] + tag + svg[m.end():]

    narration: list[dict] = []
    intro = str(spec.get("intro") or "").strip()
    if intro:
        narration.append({"speak": intro, "highlight": []})
    narration.append({
        "speak": str(result.get("caption") or "Here is the result."),
        "highlight": []})
    takeaway = str(spec.get("takeaway") or "").strip()
    if takeaway:
        narration.append({"speak": takeaway, "highlight": []})
    return svg, narration


async def generate_symbolic_svg(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
) -> Optional[tuple[str, list[dict]]]:
    """prompt -> LLM spec -> SymPy compute -> matplotlib SVG."""
    spec = await llm_emit_symbolic_spec(
        user_prompt, api_key=api_key, base_url=base_url, model=model)
    if not spec:
        return None
    result = _compute(spec)
    if not result:
        return None
    return _render(result, spec)
