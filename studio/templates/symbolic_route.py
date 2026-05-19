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
    "critical point", "critical points", "extrema",
    "local maximum", "local minimum", "saddle point",
    "maxima and minima",
)


def is_symbolic_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    return any(kw in p for kw in _KEYWORDS)


SYMBOLIC_SPEC_SYSTEM = """\
You extract a symbolic-math task into JSON.  Do NOT compute anything —
just identify the function and the operation.

Return ONLY a JSON object:
{
  "operation": "derivative" | "gradient" | "hessian" | "integral"
              | "limit" | "critical_points",
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
- "find / classify critical points", "find the extrema / maxima /
  minima / saddle points", "where is f maximised" -> operation
  "critical_points" (this SOLVES the system and classifies — do not
  use "gradient" for these).
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

def _fmt_num(v) -> str:
    """Format a numeric value: integer when it is one, else ~4 dp."""
    try:
        f = float(v)
    except Exception:  # noqa: BLE001
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:.4f}"


def _solve_critical(grad, vs, sp):
    """Real solutions of grad == 0.  Symbolic solve first, then a
    numeric grid search so transcendental systems are still solved."""
    found: list[dict] = []

    def _add(raw: dict) -> None:
        try:
            comps = [complex(raw[v]) for v in vs]
        except Exception:  # noqa: BLE001
            return
        if any(abs(c.imag) > 1e-6 for c in comps):
            return
        real = {vs[i]: sp.Float(comps[i].real) for i in range(len(vs))}
        for q in found:
            if all(abs(float(real[v] - q[v])) < 1e-4 for v in vs):
                return
        found.append(real)

    try:
        for s in sp.solve(grad, vs, dict=True):
            if all(v in s for v in vs):
                _add({v: s[v] for v in vs})
    except Exception:  # noqa: BLE001
        pass
    import itertools
    grid = (-3.0, -1.5, -0.7, -0.2, 0.0, 0.2, 0.7, 1.5, 3.0)
    for start in itertools.product(grid, repeat=len(vs)):
        try:
            root = sp.nsolve(grad, vs, start, prec=14)
        except Exception:  # noqa: BLE001
            continue
        try:
            _add({vs[i]: root[i] for i in range(len(vs))})
        except Exception:  # noqa: BLE001
            continue
    return found


def _classify(Hp, n: int, sp) -> tuple[str, str]:
    """Second-derivative test.  Returns (classification, reasoning)."""
    try:
        if n == 1:
            v = float(Hp[0, 0])
            if v > 1e-9:
                return "local minimum", f"f'' = {_fmt_num(v)} > 0"
            if v < -1e-9:
                return "local maximum", f"f'' = {_fmt_num(v)} < 0"
            return "inconclusive", "f'' = 0"
        if n == 2:
            D = float(Hp.det())
            fxx = float(Hp[0, 0])
            if D < -1e-9:
                return "saddle point", f"D = {_fmt_num(D)} < 0"
            if D > 1e-9:
                if fxx > 0:
                    return ("local minimum",
                            f"D = {_fmt_num(D)} > 0,  f_xx > 0")
                return ("local maximum",
                        f"D = {_fmt_num(D)} > 0,  f_xx < 0")
            return "inconclusive", "D = 0"
        eigs = [complex(e).real for e in Hp.eigenvals()]
        if all(e > 1e-9 for e in eigs):
            return "local minimum", "all eigenvalues > 0"
        if all(e < -1e-9 for e in eigs):
            return "local maximum", "all eigenvalues < 0"
        if any(e > 1e-9 for e in eigs) and any(e < -1e-9 for e in eigs):
            return "saddle point", "eigenvalues of mixed sign"
        return "inconclusive", "a zero eigenvalue"
    except Exception:  # noqa: BLE001
        return "inconclusive", ""


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
        "operation": op,
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
        elif op == "critical_points":
            vs = [syms[v] for v in var_names]
            grad = [sp.simplify(sp.diff(expr, v)) for v in vs]
            H = sp.hessian(expr, vs)
            out["kind"] = "critical"
            out["system"] = [
                (f"f_{{{var_names[i]}}}", L(grad[i]))
                for i in range(len(vs))]
            out["hessian"] = [[L(sp.simplify(H[i, j]))
                               for j in range(len(vs))]
                              for i in range(len(vs))]
            pts = []
            for pt in _solve_critical(grad, vs, sp):
                Hp = H.subs({v: pt[v] for v in vs})
                kind, reason = _classify(Hp, len(vs), sp)
                coords = ",\\ ".join(_fmt_num(pt[v]) for v in vs)
                pts.append({"coords": coords, "kind": kind,
                            "reason": reason})
            out["points"] = pts
            out["caption"] = (
                "Solve grad f = 0 for the critical points, then the "
                "second-derivative test classifies each one."
                if pts else "No real critical points were found.")
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
                 color: str = "#222", gid: Optional[str] = None) -> float:
    """Draw a bracketed grid of mathtext cells; return the bottom y.

    When ``gid`` is given, every cell and bracket carries that SVG id
    so the canvas narration highlighter can light the matrix up."""
    ncol = len(mat[0]) if mat else 0
    nrow = len(mat)
    x0 = 0.5 - (ncol * cell_w) / 2
    for i, row in enumerate(mat):
        for j, cell in enumerate(row):
            t = ax.text(x0 + cell_w * (j + 0.5), top - row_h * (i + 0.5),
                        f"${cell}$", ha="center", va="center",
                        fontsize=fontsize, color=color)
            if gid:
                t.set_gid(gid)
    by0, by1 = top - row_h * nrow, top
    bx0, bx1 = x0 - 0.03, x0 + cell_w * ncol + 0.03
    for bx, dx in ((bx0, 0.022), (bx1, -0.022)):
        for xs, ys in (([bx, bx], [by0, by1]),
                       ([bx, bx + dx], [by1, by1]),
                       ([bx, bx + dx], [by0, by0])):
            ln, = ax.plot(xs, ys, color=color, lw=2)
            if gid:
                ln.set_gid(gid)
    return by0


def _narrate(result: dict, spec: dict) -> list[dict]:
    """Walk EVERY element of the figure, one phrase per element, each
    carrying the matching SVG ``gid`` so the canvas highlights the
    part being spoken about."""
    kind = result.get("kind")
    op = result.get("operation", "")
    phr: list[dict] = []
    intro = str(spec.get("intro") or "").strip()
    phr.append({"speak": intro or "Let's work through this step by step.",
                "highlight": ["fig_title"]})
    phr.append({"speak": "This is the function we are working with.",
                "highlight": ["given"]})

    if kind == "critical":
        n_sys = len(result.get("system") or [])
        if n_sys:
            phr.append({
                "speak": "First, we set every first partial derivative "
                         "equal to zero. Solving this system gives the "
                         "critical points.",
                "highlight": [f"sys_{i}" for i in range(n_sys)]})
        if result.get("hessian"):
            phr.append({
                "speak": "The Hessian gathers the second derivatives. "
                         "Its sign at each point tells us what kind of "
                         "point we have.",
                "highlight": ["hessian"]})
        for i, p in enumerate(result.get("points") or []):
            coords = (p.get("coords", "")
                      .replace("\\ ", " ").replace("\\,", " "))
            k = p.get("kind", "")
            if "saddle" in k:
                why = ("the Hessian determinant is negative, so the "
                       "surface rises one way and falls another — a "
                       "saddle point")
            elif "minimum" in k:
                why = ("the Hessian determinant is positive and the "
                       "curvature points upward — a local minimum")
            elif "maximum" in k:
                why = ("the Hessian determinant is positive and the "
                       "curvature points downward — a local maximum")
            else:
                why = "the second-derivative test is inconclusive here"
            phr.append({"speak": f"At the point ({coords}), {why}.",
                        "highlight": [f"pt_{i}"]})
    elif kind == "matrix":
        phr.append({
            "speak": str(result.get("caption")
                         or "Each entry is a second-order partial "
                            "derivative."),
            "highlight": ["result"]})
        if result.get("at_point_matrix"):
            phr.append({
                "speak": "Evaluated at the given point, the matrix "
                         "becomes these concrete numbers.",
                "highlight": ["atpoint"]})
    else:  # steps
        steps = result.get("steps") or []
        ordinals = ["first", "second", "third", "fourth", "fifth",
                    "sixth"]
        for i in range(len(steps[:6])):
            if op == "derivative":
                s = f"This is the {ordinals[i]} derivative."
            elif op == "gradient":
                s = ("This is the partial derivative with respect to "
                     f"the {ordinals[i]} variable.")
            elif op == "integral":
                s = ("This is the antiderivative — the integral of the "
                     "function, plus a constant.")
            elif op == "limit":
                s = "This is the value the function approaches."
            else:
                s = "Here is the result."
            phr.append({"speak": s, "highlight": [f"step_{i}"]})

    takeaway = str(spec.get("takeaway") or "").strip()
    if takeaway:
        phr.append({"speak": takeaway, "highlight": ["fig_title"]})
    return phr


def _render(result: dict, spec: dict) -> Optional[tuple[str, list[dict]]]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # Keep text as <text> (not glyph paths) so each gid'd element
        # is a real SVG node the narration highlighter can target.
        plt.rcParams["svg.fonttype"] = "none"
    except Exception:  # noqa: BLE001
        return None

    title = result.get("title") or "Symbolic result"
    given_l, given_r = result.get("given") or ("f", "")
    kind = result.get("kind")

    fig_h = 9.2 if kind == "critical" else 6.4
    fig = plt.figure(figsize=(8.4, fig_h), dpi=100)
    try:
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.94, title, ha="center", va="center",
                fontsize=19, fontweight="bold", color="#1a3a5c",
                family="serif").set_gid("fig_title")
        ax.text(0.5, 0.82, fr"$\mathrm{{Given:}}\quad {given_l} = {given_r}$",
                ha="center", va="center", fontsize=16,
                color="#222").set_gid("given")

        y = 0.66
        if kind == "matrix":
            mat = result.get("matrix") or []
            if not mat:
                return None
            y = _draw_matrix(ax, mat, top=0.70, gid="result") - 0.06
        elif kind == "critical":
            y = 0.74
            ax.text(0.5, y, r"$\mathrm{Set}\ \nabla f = 0:$",
                    ha="center", va="center", fontsize=13, color="#666",
                    family="serif")
            y -= 0.045
            for i, (lhs, rhs) in enumerate(result.get("system", [])):
                ax.text(0.5, y, fr"${lhs} = {rhs} = 0$", ha="center",
                        va="center", fontsize=15,
                        color="#1a3a5c").set_gid(f"sys_{i}")
                y -= 0.045
            hess = result.get("hessian") or []
            if hess:
                y -= 0.015
                ax.text(0.5, y, r"$\mathrm{Hessian:}$", ha="center",
                        va="center", fontsize=13, color="#666",
                        family="serif")
                y -= 0.03
                y = _draw_matrix(ax, hess, top=y, fontsize=13,
                                 cell_w=0.27, row_h=0.06,
                                 gid="hessian") - 0.03
            pts = result.get("points") or []
            ax.text(0.5, y, r"$\mathrm{Critical\ points:}$", ha="center",
                    va="center", fontsize=13, color="#666",
                    family="serif")
            y -= 0.05
            _cmap = {"saddle point": "#cc4125",
                     "local minimum": "#2e7d32",
                     "local maximum": "#e69138",
                     "inconclusive": "#888888"}
            for i, p in enumerate(pts):
                col = _cmap.get(p.get("kind"), "#222")
                ax.text(0.5, y, fr"$({p['coords']})$ :  {p['kind']}",
                        ha="center", va="center", fontsize=15,
                        color=col).set_gid(f"pt_{i}")
                y -= 0.034
                if p.get("reason"):
                    ax.text(0.5, y, p["reason"], ha="center",
                            va="center", fontsize=10.5, color="#999",
                            family="serif").set_gid(f"pt_{i}")
                    y -= 0.045
                else:
                    y -= 0.012
        else:  # steps
            steps = result.get("steps") or []
            if not steps:
                return None
            y = 0.66
            for i, (lhs, rhs) in enumerate(steps[:6]):
                ax.text(0.5, y, fr"${lhs} = {rhs}$", ha="center",
                        va="center", fontsize=17,
                        color="#1a3a5c").set_gid(f"step_{i}")
                y -= 0.12

        cap = result.get("caption") or ""
        if cap:
            ax.text(0.5, max(y, 0.20), cap, ha="center", va="center",
                    fontsize=12, color="#666", style="italic",
                    family="serif")
            y = max(y, 0.20) - 0.08

        apm = result.get("at_point_matrix")
        at = result.get("at_point")
        if apm and apm.get("matrix"):
            ax.text(0.5, y, f"$\\mathrm{{At\\ the\\ point:}}\\ "
                    f"{apm.get('label','')}=$", ha="center", va="center",
                    fontsize=14, color="#cc4125").set_gid("atpoint")
            _draw_matrix(ax, apm["matrix"], top=y - 0.05, fontsize=15,
                         cell_w=0.18, row_h=0.10, color="#cc4125",
                         gid="atpoint")
        elif at:
            ax.text(0.5, max(y, 0.07), f"${at[0]} = {at[1]}$",
                    ha="center", va="center", fontsize=15,
                    color="#cc4125").set_gid("atpoint")

        buf = io.StringIO()
        fig.savefig(buf, format="svg", bbox_inches="tight")
        svg = buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
    finally:
        plt.close(fig)

    if "<svg" not in svg:
        return None
    m = re.search(r"<svg\b[^>]*>", svg)
    if m:
        tag = re.sub(r'\s(?:width|height)="[^"]*"', "", m.group(0))
        tag = tag.replace(
            "<svg",
            '<svg width="100%" style="max-width:100%;height:auto;'
            'display:block;margin:0 auto;"', 1)
        svg = svg[:m.start()] + tag + svg[m.end():]

    return svg, _narrate(result, spec)


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
