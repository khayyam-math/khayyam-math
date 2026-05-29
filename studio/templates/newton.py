"""Deterministic Newton's-method figure.

Plots a function f(x) and the first few Newton-Raphson iterates with
their actual tangent lines.  Replaces the general LLM-SVG path for
prompts of the form "Newton's method ... f(x) = ... starting from
x_0 = N", where the LLM consistently drew iterates on the wrong side
and "tangent" lines that didn't actually touch the curve.

Math is exact:

    x_{n+1} = x_n − f(x_n) / f'(x_n)

f and x_0 are parsed with SymPy; iterates are computed numerically
from a SymPy-derived derivative; every point and line in the SVG
sits at the exact coordinate the math says it should.

Public API:

    newton_method(f, x0, n_iter=4, title="") -> (svg, narration)
"""
from __future__ import annotations

from typing import List, Tuple


_SUB = "₀₁₂₃₄₅₆₇₈₉"


def _esc(s: object) -> str:
    return (str(s).replace("&", "&amp;")
                  .replace("<", "&lt;")
                  .replace(">", "&gt;"))


def _sub(i: int) -> str:
    if 0 <= i < 10:
        return _SUB[i]
    return str(i)


def newton_method(
    f: str,
    x0,
    n_iter: int = 4,
    title: str = "",
) -> Tuple[str, List[dict]]:
    """Render Newton's method on f(x) starting from x_0.

    f      SymPy-parseable expression of one variable (default 'x'),
           e.g. "x**3 - 2", "cos(x) - x", "x*exp(x) - 1".  Accepts
           both Python "**" and math-style "^" for exponentiation
           (implicit-multiplication transform is on, so "2x" works).
    x0     starting value, parsed by SymPy (so "2", "1.5", "pi/4"
           are all accepted).
    n_iter number of iterates to draw (default 4).  Stops earlier on
           convergence (|delta| < 1e-7) or on a zero derivative.
    title  optional figure title rendered above the plot.

    Returns ``(svg, narration)``.  Raises ValueError on a malformed
    f or x0, or when the iteration can't make any progress; the
    router catches the exception and falls back to the LLM path.
    """
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        parse_expr, standard_transformations,
        implicit_multiplication_application, convert_xor,
    )

    # ── parse f, x0 ─────────────────────────────────────────────────
    x_sym = sp.Symbol('x')
    transformations = (
        standard_transformations
        + (implicit_multiplication_application, convert_xor)
    )
    try:
        fexpr = parse_expr(str(f), local_dict={'x': x_sym},
                           transformations=transformations, evaluate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"cannot parse f={f!r}: {exc}")
    if x_sym not in fexpr.free_symbols and not fexpr.is_constant():
        raise ValueError(f"f={f!r} has no variable x")
    try:
        x0_val = float(sp.N(sp.sympify(str(x0))))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"cannot parse x0={x0!r}: {exc}")

    try:
        fexpr_d = sp.diff(fexpr, x_sym)
        f_fn = sp.lambdify(x_sym, fexpr, modules=['math'])
        fp_fn = sp.lambdify(x_sym, fexpr_d, modules=['math'])
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"cannot differentiate f: {exc}")

    # ── compute iterates ────────────────────────────────────────────
    n_iter = max(1, min(int(n_iter), 10))
    iterates: List[float] = [x0_val]
    cur = x0_val
    for _ in range(n_iter):
        try:
            fv = float(f_fn(cur))
            fpv = float(fp_fn(cur))
        except Exception:  # noqa: BLE001
            break
        if abs(fpv) < 1e-12:
            break
        nxt = cur - fv / fpv
        iterates.append(nxt)
        if abs(nxt - cur) < 1e-7:
            break
        cur = nxt
    if len(iterates) < 2:
        raise ValueError(
            f"Newton's method made no progress from x_0={x0_val} "
            f"(zero derivative or f undefined here)."
        )
    converged = iterates[-1]

    # ── plot window ────────────────────────────────────────────────
    xs_set = sorted(set(iterates))
    x_lo, x_hi = xs_set[0], xs_set[-1]
    span = max(x_hi - x_lo, 1.0)
    # generous padding so the labels at the edge iterates don't get
    # clipped, plus extra room to one side for the convergence note.
    plot_xmin = x_lo - 0.30 * span
    plot_xmax = x_hi + 0.30 * span

    # y-range: sample f across the plot window and clip aggressively
    # so a steep cubic doesn't blow the vertical out of usable range.
    n_probe = 200
    y_samples: List[float] = []
    for i in range(n_probe + 1):
        xv = plot_xmin + i * (plot_xmax - plot_xmin) / n_probe
        try:
            yv = float(f_fn(xv))
        except Exception:  # noqa: BLE001
            continue
        if yv != yv or abs(yv) > 1e6:
            continue
        y_samples.append(yv)
    if not y_samples:
        raise ValueError("f produced no finite samples in the plot window")
    # include the iterate y-values too so the curve sample doesn't
    # accidentally clip the f(x_0) tangent root point.
    for xi in iterates:
        try:
            y_samples.append(float(f_fn(xi)))
        except Exception:  # noqa: BLE001
            pass
    y_lo, y_hi = min(y_samples), max(y_samples)
    # always include y=0 in the range (the x-axis is the whole point).
    y_lo = min(y_lo, 0.0)
    y_hi = max(y_hi, 0.0)
    y_span = max(y_hi - y_lo, 1.0)
    plot_ymin = y_lo - 0.15 * y_span
    plot_ymax = y_hi + 0.15 * y_span

    # ── SVG layout ─────────────────────────────────────────────────
    W, H = 920.0, 560.0
    title_h = 56.0 if title else 24.0
    top = title_h + 8.0
    bot = 92.0
    left = 90.0
    right = 60.0
    plot_w = W - left - right
    plot_h = H - top - bot

    def sx(xv: float) -> float:
        return left + (xv - plot_xmin) / (plot_xmax - plot_xmin) * plot_w

    def sy(yv: float) -> float:
        return top + (plot_ymax - yv) / (plot_ymax - plot_ymin) * plot_h

    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
    ]
    if title:
        out.append(
            f'<text id="title" x="{W/2:.0f}" y="{title_h-12:.0f}" '
            f'font-size="22" text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(title)}</text>'
        )

    # ── axes ───────────────────────────────────────────────────────
    # x-axis (always shown — must hit y=0 because the iteration's
    # geometry depends on tangents crossing zero).
    out.append(
        f'<line id="x_axis" x1="{left:.1f}" y1="{sy(0):.1f}" '
        f'x2="{W-right:.1f}" y2="{sy(0):.1f}" '
        f'stroke="#333" stroke-width="1.6"/>'
    )
    # arrow head for x-axis (pointing right)
    out.append(
        f'<polygon points="{W-right:.1f},{sy(0):.1f} '
        f'{W-right-8:.1f},{sy(0)-5:.1f} {W-right-8:.1f},{sy(0)+5:.1f}" '
        f'fill="#333"/>'
    )
    out.append(
        f'<text x="{W-right+6:.1f}" y="{sy(0)+5:.1f}" '
        f'font-size="14" font-family="serif" fill="#333">x</text>'
    )
    # y-axis if 0 is in the plot's x-range
    if plot_xmin <= 0 <= plot_xmax:
        out.append(
            f'<line id="y_axis" x1="{sx(0):.1f}" y1="{top:.1f}" '
            f'x2="{sx(0):.1f}" y2="{H-bot:.1f}" '
            f'stroke="#333" stroke-width="1.6"/>'
        )
    # y-axis label (place along the top-left even if y-axis not drawn)
    out.append(
        f'<text x="{left-12:.1f}" y="{top+4:.1f}" font-size="14" '
        f'font-family="serif" text-anchor="end" fill="#333">y</text>'
    )

    # x-axis tick marks at integer positions inside the plot window
    import math
    tick_step = 1 if (plot_xmax - plot_xmin) < 8 else 2
    t_first = math.ceil(plot_xmin / tick_step) * tick_step
    tv = t_first
    while tv <= plot_xmax + 1e-9:
        if abs(tv) > 1e-9:  # skip 0 (y-axis handles it visually)
            out.append(
                f'<line x1="{sx(tv):.1f}" y1="{sy(0)-4:.1f}" '
                f'x2="{sx(tv):.1f}" y2="{sy(0)+4:.1f}" '
                f'stroke="#666" stroke-width="1"/>'
            )
            # label only when not too close to an iterate label below
            label_close_to_iter = any(
                abs(tv - xi) < 0.18 * span for xi in iterates
            )
            if not label_close_to_iter:
                out.append(
                    f'<text x="{sx(tv):.1f}" y="{sy(0)+18:.1f}" '
                    f'font-size="11" font-family="serif" '
                    f'text-anchor="middle" fill="#666">{tv:g}</text>'
                )
        tv += tick_step

    # ── function curve ─────────────────────────────────────────────
    n_samples = 280
    pts: List[str] = []
    for i in range(n_samples + 1):
        xv = plot_xmin + i * (plot_xmax - plot_xmin) / n_samples
        try:
            yv = float(f_fn(xv))
        except Exception:  # noqa: BLE001
            continue
        if yv != yv or abs(yv) > 1e6:
            continue
        if yv < plot_ymin - 0.05 * y_span or yv > plot_ymax + 0.05 * y_span:
            continue
        pts.append(f"{sx(xv):.1f},{sy(yv):.1f}")
    out.append(
        f'<polyline id="curve" points="{" ".join(pts)}" '
        f'fill="none" stroke="#2a6fd6" stroke-width="2.8"/>'
    )
    # curve label, placed near a sampled mid-point of the curve
    if pts:
        mid = pts[len(pts) // 2].split(',')
        lx, ly = float(mid[0]), float(mid[1])
        out.append(
            f'<text id="curve_label" x="{lx + 12:.1f}" '
            f'y="{ly - 14:.1f}" font-size="15" font-family="serif" '
            f'fill="#2a6fd6">f(x) = {_esc(sp.sstr(fexpr))}</text>'
        )

    # ── tangent lines (drawn before dots so dots sit on top) ──────
    n_steps = len(iterates) - 1
    for i in range(n_steps):
        xi = iterates[i]
        xn = iterates[i + 1]
        try:
            yi = float(f_fn(xi))
        except Exception:  # noqa: BLE001
            continue
        if not (plot_ymin <= yi <= plot_ymax):
            continue
        # the actual tangent line: from (x_i, f(x_i)) to (x_{i+1}, 0)
        out.append(
            f'<line id="tangent_{i}" x1="{sx(xi):.1f}" '
            f'y1="{sy(yi):.1f}" x2="{sx(xn):.1f}" y2="{sy(0):.1f}" '
            f'stroke="#c0392b" stroke-width="2.0" '
            f'stroke-dasharray="7,3"/>'
        )
        # subtle dashed vertical drop from the curve point to the
        # x-axis (so the reader can see "this iterate's f-value sits
        # over the next iterate's x-value")
        out.append(
            f'<line x1="{sx(xi):.1f}" y1="{sy(yi):.1f}" '
            f'x2="{sx(xi):.1f}" y2="{sy(0):.1f}" '
            f'stroke="#aaa" stroke-width="0.9" stroke-dasharray="2,3"/>'
        )

    # ── iterate markers + labels ──────────────────────────────────
    # Decide which iterates get a visible label.  When Newton converges
    # quickly the last few iterates pile up at the root, and stacking
    # "x_3 = 1.261" / "x_4 = 1.260" labels makes them unreadable.
    # Rule: always label x_0 and the last iterate; label intermediate
    # ones only when their screen-x position is at least 60 px away
    # from the previously-labelled iterate.
    # Walk twice: first pass forward labelling whenever we're at
    # least 60 screen-px from the last labelled iterate (in absolute
    # terms — Newton can move iterates either direction); second pass
    # forces the LAST iterate to be labelled even if it would have
    # collided with the previous label (the last label gets priority,
    # the colliding earlier label loses).
    label_visible: list[bool] = [False] * len(iterates)
    last_label_sx = float('-inf')
    for i, xi in enumerate(iterates):
        my_sx = sx(xi)
        if i == 0 or abs(my_sx - last_label_sx) >= 60.0:
            label_visible[i] = True
            last_label_sx = my_sx
    # Always show the converged (last) iterate's label; if it
    # collides with the previously-labelled iterate, drop that one.
    last_i = len(iterates) - 1
    if not label_visible[last_i]:
        for j in range(last_i - 1, -1, -1):
            if label_visible[j]:
                if abs(sx(iterates[j]) - sx(iterates[last_i])) < 60.0:
                    label_visible[j] = False
                break
        label_visible[last_i] = True

    for i, xi in enumerate(iterates):
        # dot on the x-axis (this IS x_i)
        out.append(
            f'<circle id="xn_{i}" cx="{sx(xi):.1f}" cy="{sy(0):.1f}" '
            f'r="6" fill="#1f6b1f" stroke="#0a3a0a" stroke-width="1.5"/>'
        )
        if label_visible[i]:
            out.append(
                f'<text id="xn_{i}_label" x="{sx(xi):.1f}" '
                f'y="{sy(0)+40:.1f}" font-size="14" '
                f'font-family="serif" text-anchor="middle" '
                f'fill="#0a3a0a" font-weight="bold">'
                f'x{_sub(i)} = {xi:.4g}</text>'
            )
        # dot on the curve at (x_i, f(x_i)), if visible
        try:
            yi = float(f_fn(xi))
            if plot_ymin <= yi <= plot_ymax:
                out.append(
                    f'<circle cx="{sx(xi):.1f}" cy="{sy(yi):.1f}" '
                    f'r="5" fill="#c0392b" stroke="#7a2010" '
                    f'stroke-width="1.5"/>'
                )
        except Exception:  # noqa: BLE001
            pass

    # ── convergence note + iterate sequence caption ────────────────
    seq = "  →  ".join(f"x{_sub(i)} = {xi:.4g}"
                       for i, xi in enumerate(iterates))
    out.append(
        f'<text id="sequence" x="{W/2:.0f}" y="{H-44:.1f}" '
        f'font-size="14" font-family="serif" text-anchor="middle" '
        f'fill="#222">{_esc(seq)}</text>'
    )
    out.append(
        f'<text x="{W/2:.0f}" y="{H-24:.1f}" font-size="13" '
        f'font-family="serif" text-anchor="middle" fill="#666">'
        f'Newton iteration:  xₙ₊₁ = xₙ − f(xₙ) / f′(xₙ)</text>'
    )
    out.append(
        f'<text id="converged" x="{W/2:.0f}" y="{H-7:.1f}" '
        f'font-size="13" font-family="serif" text-anchor="middle" '
        f'fill="#1f6b1f">Converges to ≈ {converged:.4g} '
        f'after {len(iterates)-1} step(s)</text>'
    )
    out.append('</svg>')
    svg = "\n".join(out)

    # ── narration ──────────────────────────────────────────────────
    # Avoid feeding sp.sstr(fexpr) directly to TTS: "x**3 - 2" reads
    # as "x star star three minus two".  The figure already displays
    # the formula visually; the narration refers to it as "the
    # function" or by its name.
    narration: List[dict] = []
    narration.append({
        "speak": (
            "Newton's method finds where a function crosses zero by "
            "following tangent lines down to the x-axis."
        ),
        "highlight": ["title"] if title else ["curve"],
    })
    narration.append({
        "speak": (
            f"We want to find where the curve crosses the x-axis, "
            f"starting from the initial guess x naught equals "
            f"{iterates[0]:.4g}."
        ),
        "highlight": ["curve_label", "xn_0_label"],
    })
    # one narration phrase per drawn tangent, up to 3 explicit + a
    # summary on convergence
    for i in range(min(n_steps, 3)):
        xi = iterates[i]
        xn = iterates[i + 1]
        try:
            yi = float(f_fn(xi))
        except Exception:  # noqa: BLE001
            yi = float('nan')
        narration.append({
            "speak": (
                f"At x_{i} equals {xi:.4g}, the value of f is "
                f"{yi:.4g}. We draw the tangent line at that point "
                f"and follow it down to where it crosses the x-axis. "
                f"That crossing is x_{i+1} equals {xn:.4g}."
            ),
            "highlight": [f"tangent_{i}", f"xn_{i+1}_label"],
        })
    narration.append({
        "speak": (
            f"After {len(iterates)-1} steps the iterates have "
            f"converged to about {converged:.4g}, which is the "
            f"root of f."
        ),
        "highlight": ["converged", "sequence"],
    })

    return svg, narration


__all__ = ["newton_method"]
