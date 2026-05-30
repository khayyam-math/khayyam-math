"""Figure Description Language (FDL).

The architectural answer to "we can't hardcode templates for every
problem".  The LLM emits a small set of MATH-MEANINGFUL primitives as
structured JSON; a deterministic renderer turns them into a correct
SVG.  The LLM no longer touches pixel coordinates; it specifies WHAT
to draw in math space, and the renderer handles WHERE.

Where this sits in the pipeline:

    template router  (deterministic, ~20 specific problem classes)
            ↓ miss
    FDL route        ← this module: any function-plot-with-marks problem
            ↓ miss
    LLM-SVG path     (the LLM emits raw SVG; layout regressions live here)

Phase 1 primitives (this commit):

    Plot(f, x_min, x_max, label?)
        Curve of y = f(x).  ``label`` ("f", "g") names this curve so
        later primitives can refer to it.  SymPy-parseable f; accepts
        both ``**`` and ``^`` exponentiation; implicit multiplication
        ("2x" → 2*x).

    AxisMark(x, label, axis="x")
        Labelled tick on the named axis.

    MarkPoint(curve, x, label?)
        Labelled red dot at (x, f(x)) on the named curve.  The
        renderer evaluates f(x) symbolically so the dot lies exactly
        on the curve (no LLM-picked y coordinate).

    TangentAt(curve, x, label?, mode="line")
        True tangent to the curve at x.  Slope is f'(x), computed
        symbolically via SymPy.  By construction the line touches the
        curve at one point with the correct slope, so this cannot be
        drawn as a generic dashed diagonal the way the LLM-SVG path
        does it.
            mode="line"    : extends ±0.5·plot_x_span around (x, f(x))
            mode="to_zero" : Newton-method style; ends at the x-axis
                             crossing x - f(x)/f'(x)

    Caption(text, anchor="right")
        Text caption.  Anchors: "right" (stacked on the right margin,
        the default), "top", "bottom".  Multiple captions stack in the
        order added.

Auto-layout: the renderer derives the plot range from the union of
all primitives' math coordinates plus 15 % padding, then maps to a
920×580 SVG with margins.  Label dedup follows the same pattern as
newton.py — if two marked points are within 60 screen-px, only the
first label is rendered.

Public API:

    Scene                — dataclass holding the primitives
    render_scene(scene)  → (svg, narration)
    SCENE_SCHEMA         — JSON schema for LLM structured output
    llm_extract_scene(prompt, *, api_key, base_url, model)
                         → Scene | None  (None means "no FDL match")
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Tuple


# ----------------------------------------------------------------------
# Primitive dataclasses
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Plot:
    f: str
    x_min: float
    x_max: float
    label: str = "f"   # default name used by later primitives
    color: str = "#2a6fd6"


@dataclass(frozen=True)
class AxisMark:
    x: float
    label: str
    axis: Literal["x", "y"] = "x"


@dataclass(frozen=True)
class MarkPoint:
    curve: str    # references a Plot's label
    x: float
    label: Optional[str] = None  # None → no label, just the dot


@dataclass(frozen=True)
class TangentAt:
    curve: str    # references a Plot's label
    x: float
    label: Optional[str] = None
    mode: Literal["line", "to_zero"] = "line"


@dataclass(frozen=True)
class Caption:
    text: str
    anchor: Literal["right", "top", "bottom"] = "right"


Primitive = Plot | AxisMark | MarkPoint | TangentAt | Caption


@dataclass
class Scene:
    title: str = ""
    primitives: List[Primitive] = field(default_factory=list)
    narration: List[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------


def _esc(s: object) -> str:
    return (str(s).replace("&", "&amp;")
                  .replace("<", "&lt;")
                  .replace(">", "&gt;"))


_SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUB = "₀₁₂₃₄₅₆₇₈₉"


def _pretty_expr(expr_str: str) -> str:
    """x**3 - 2  ->  x³ − 2   (same helper as newton.py)."""
    import re
    s = expr_str
    s = re.sub(r"\*\*\s*(\d)\b",
               lambda m: m.group(1).translate(
                   str.maketrans("0123456789", _SUP)), s)
    s = re.sub(r"\^\s*(\d)\b",
               lambda m: m.group(1).translate(
                   str.maketrans("0123456789", _SUP)), s)
    s = s.replace("*", "·")
    s = re.sub(r"(?<=\S) - (?=\S)", " − ", s)
    return s


def _build_sympy_env():
    """Reuse the math_verifier's tolerant SymPy parser."""
    from studio.templates.math_verifier import _make_env
    return _make_env()


def _safe_eval(parse, sp, f_expr: str, x_val: float) -> Optional[float]:
    e = parse(f_expr)
    if e is None:
        return None
    try:
        from sympy import Symbol
        x = Symbol("x")
        return float(e.subs(x, x_val).evalf())
    except Exception:  # noqa: BLE001
        return None


def _safe_diff_eval(parse, sp, f_expr: str, x_val: float) -> Optional[float]:
    e = parse(f_expr)
    if e is None:
        return None
    try:
        from sympy import Symbol, diff
        x = Symbol("x")
        return float(diff(e, x).subs(x, x_val).evalf())
    except Exception:  # noqa: BLE001
        return None


def _compute_plot_range(
    scene: Scene, parse, sp,
) -> Optional[Tuple[float, float, float, float]]:
    """Derive (x_min, x_max, y_min, y_max) covering every primitive,
    with 15 % padding.  Returns None when there's nothing to plot.
    """
    xs: List[float] = []
    ys: List[float] = []
    plot_by_label: dict[str, Plot] = {}
    for p in scene.primitives:
        if isinstance(p, Plot):
            plot_by_label[p.label] = p
            xs.append(p.x_min)
            xs.append(p.x_max)
        elif isinstance(p, (MarkPoint, TangentAt)):
            xs.append(p.x)
        elif isinstance(p, AxisMark) and p.axis == "x":
            xs.append(p.x)
    if not xs:
        return None
    x_lo, x_hi = min(xs), max(xs)
    span_x = max(x_hi - x_lo, 1.0)
    plot_xmin = x_lo - 0.15 * span_x
    plot_xmax = x_hi + 0.15 * span_x

    # Sample each curve to figure out y-range
    for plot in plot_by_label.values():
        n = 100
        for i in range(n + 1):
            xv = plot_xmin + (plot_xmax - plot_xmin) * i / n
            yv = _safe_eval(parse, sp, plot.f, xv)
            if yv is None or yv != yv:
                continue
            if abs(yv) > 1e6:
                continue
            ys.append(yv)
    # MarkPoint and TangentAt push their y values too
    for p in scene.primitives:
        if isinstance(p, (MarkPoint, TangentAt)):
            plot = plot_by_label.get(p.curve)
            if plot is None:
                continue
            yv = _safe_eval(parse, sp, plot.f, p.x)
            if yv is not None and yv == yv:
                ys.append(yv)
    if not ys:
        ys = [-1.0, 1.0]
    y_lo, y_hi = min(ys), max(ys)
    # always include y=0 so the x-axis is visible
    y_lo = min(y_lo, 0.0)
    y_hi = max(y_hi, 0.0)
    span_y = max(y_hi - y_lo, 1.0)
    plot_ymin = y_lo - 0.15 * span_y
    plot_ymax = y_hi + 0.15 * span_y
    return plot_xmin, plot_xmax, plot_ymin, plot_ymax


def render_scene(scene: Scene) -> Tuple[str, List[dict]]:
    """Turn a Scene into (svg, narration).  Raises ValueError on a
    malformed Scene (e.g. TangentAt referencing a curve that wasn't
    declared); the caller catches and falls back to LLM-SVG.
    """
    sp, parse = _build_sympy_env()
    rng = _compute_plot_range(scene, parse, sp)
    if rng is None:
        raise ValueError("Scene has no plottable primitives")
    plot_xmin, plot_xmax, plot_ymin, plot_ymax = rng

    W, H = 920.0, 580.0
    title_h = 56.0 if scene.title else 24.0
    top = title_h + 8.0
    bot = 60.0
    left = 80.0
    # right margin grows to fit caption text
    right_captions = [p for p in scene.primitives
                      if isinstance(p, Caption) and p.anchor == "right"]
    right = 360.0 if right_captions else 60.0
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
    if scene.title:
        out.append(
            f'<text id="title" x="{(left + (W-right))/2:.0f}" '
            f'y="{title_h-14:.0f}" font-size="22" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(scene.title)}</text>'
        )

    # axes: x-axis always at y=0, y-axis at x=0 (always present after
    # the plot-range clamp above)
    out.append(
        f'<line id="x_axis" x1="{left:.1f}" y1="{sy(0):.1f}" '
        f'x2="{W-right:.1f}" y2="{sy(0):.1f}" stroke="#333" '
        f'stroke-width="1.6"/>'
    )
    out.append(
        f'<polygon points="{W-right:.1f},{sy(0):.1f} '
        f'{W-right-8:.1f},{sy(0)-5:.1f} {W-right-8:.1f},{sy(0)+5:.1f}" '
        f'fill="#333"/>'
    )
    out.append(
        f'<text x="{W-right+6:.1f}" y="{sy(0)+5:.1f}" font-size="14" '
        f'font-family="serif" fill="#333">x</text>'
    )
    if plot_xmin <= 0 <= plot_xmax:
        out.append(
            f'<line id="y_axis" x1="{sx(0):.1f}" y1="{top:.1f}" '
            f'x2="{sx(0):.1f}" y2="{H-bot:.1f}" stroke="#333" '
            f'stroke-width="1.6"/>'
        )
    out.append(
        f'<text x="{left-10:.1f}" y="{top+4:.1f}" font-size="14" '
        f'font-family="serif" text-anchor="end" fill="#333">y</text>'
    )

    # x-axis tick marks at integer positions
    tick_step = 1 if (plot_xmax - plot_xmin) < 8 else 2
    tv = math.ceil(plot_xmin / tick_step) * tick_step
    plot_by_label: dict[str, Plot] = {}
    while tv <= plot_xmax + 1e-9:
        if abs(tv) > 1e-9:
            out.append(
                f'<line x1="{sx(tv):.1f}" y1="{sy(0)-4:.1f}" '
                f'x2="{sx(tv):.1f}" y2="{sy(0)+4:.1f}" '
                f'stroke="#666" stroke-width="1"/>'
            )
            out.append(
                f'<text x="{sx(tv):.1f}" y="{sy(0)+18:.1f}" '
                f'font-size="11" font-family="serif" '
                f'text-anchor="middle" fill="#666">{tv:g}</text>'
            )
        tv += tick_step

    # Pass 1: draw all Plots (curves first, behind everything)
    for p in scene.primitives:
        if not isinstance(p, Plot):
            continue
        plot_by_label[p.label] = p
        n = 240
        pts: List[str] = []
        for i in range(n + 1):
            xv = plot_xmin + (plot_xmax - plot_xmin) * i / n
            yv = _safe_eval(parse, sp, p.f, xv)
            if yv is None or yv != yv or abs(yv) > 1e6:
                continue
            if yv < plot_ymin or yv > plot_ymax:
                continue
            pts.append(f"{sx(xv):.1f},{sy(yv):.1f}")
        out.append(
            f'<polyline id="curve_{_esc(p.label)}" '
            f'points="{" ".join(pts)}" '
            f'fill="none" stroke="{p.color}" stroke-width="2.6"/>'
        )

    # Pass 2: tangent lines (before dots so dots sit on top)
    for i, p in enumerate(scene.primitives):
        if not isinstance(p, TangentAt):
            continue
        plot = plot_by_label.get(p.curve)
        if plot is None:
            raise ValueError(
                f"TangentAt references curve {p.curve!r} which has no Plot"
            )
        y_val = _safe_eval(parse, sp, plot.f, p.x)
        slope = _safe_diff_eval(parse, sp, plot.f, p.x)
        if y_val is None or slope is None:
            continue
        if p.mode == "to_zero":
            # Newton-style: end at the x-axis crossing
            if abs(slope) < 1e-12:
                end_x = p.x  # vertical-ish; clamp
            else:
                end_x = p.x - y_val / slope
            end_y = 0.0
            start_x, start_y = p.x, y_val
        else:
            # "line" mode: extend ±0.5·plot_x_span around the point
            span = (plot_xmax - plot_xmin) * 0.45
            start_x = p.x - span * 0.5
            start_y = y_val - slope * span * 0.5
            end_x = p.x + span * 0.5
            end_y = y_val + slope * span * 0.5
        out.append(
            f'<line id="tangent_{i}" x1="{sx(start_x):.1f}" '
            f'y1="{sy(start_y):.1f}" x2="{sx(end_x):.1f}" '
            f'y2="{sy(end_y):.1f}" stroke="#c0392b" '
            f'stroke-width="2.0" stroke-dasharray="7,3"/>'
        )
        if p.label:
            mx, my = (start_x + end_x) / 2, (start_y + end_y) / 2
            out.append(
                f'<text x="{sx(mx):.1f}" y="{sy(my) - 8:.1f}" '
                f'font-size="13" font-family="serif" fill="#c0392b" '
                f'font-style="italic">{_esc(p.label)}</text>'
            )

    # Pass 3: AxisMarks (ticks with labels)
    for p in scene.primitives:
        if not isinstance(p, AxisMark):
            continue
        if p.axis == "x":
            out.append(
                f'<line x1="{sx(p.x):.1f}" y1="{sy(0)-6:.1f}" '
                f'x2="{sx(p.x):.1f}" y2="{sy(0)+6:.1f}" '
                f'stroke="#1f6b1f" stroke-width="2"/>'
            )
            out.append(
                f'<text x="{sx(p.x):.1f}" y="{sy(0)+24:.1f}" '
                f'font-size="13" font-family="serif" '
                f'text-anchor="middle" fill="#1f6b1f" '
                f'font-weight="bold">{_esc(p.label)}</text>'
            )

    # Pass 4: MarkPoint dots + labels (with screen-dedup like newton.py).
    # Skip a MarkPoint's label when a TangentAt at the same curve and
    # x already carries a label — those two labels would otherwise pile
    # up on the same dot and become unreadable.
    tangent_labelled_xs = {
        (p.curve, round(p.x, 4))
        for p in scene.primitives
        if isinstance(p, TangentAt) and p.label
    }
    last_label_sx = float("-inf")
    for p in scene.primitives:
        if not isinstance(p, MarkPoint):
            continue
        plot = plot_by_label.get(p.curve)
        if plot is None:
            raise ValueError(
                f"MarkPoint references curve {p.curve!r} which has no Plot"
            )
        y_val = _safe_eval(parse, sp, plot.f, p.x)
        if y_val is None:
            continue
        # dot on the curve
        out.append(
            f'<circle cx="{sx(p.x):.1f}" cy="{sy(y_val):.1f}" r="6" '
            f'fill="#c0392b" stroke="#7a2010" stroke-width="1.5"/>'
        )
        suppress = (p.curve, round(p.x, 4)) in tangent_labelled_xs
        if (p.label and not suppress
                and abs(sx(p.x) - last_label_sx) >= 60):
            out.append(
                f'<text x="{sx(p.x) + 10:.1f}" '
                f'y="{sy(y_val) - 10:.1f}" font-size="14" '
                f'font-family="serif" fill="#7a2010" '
                f'font-weight="bold">{_esc(p.label)}</text>'
            )
            last_label_sx = sx(p.x)

    # Pass 5: Captions, wrapped to the right-margin width.
    def _wrap_words(text: str, max_chars: int = 36) -> List[str]:
        """Greedy word-wrap so the caption fits in the right margin.
        ~36 chars at 14pt serif sits comfortably in a 340-px column."""
        lines: List[str] = []
        for raw in (text.splitlines() or [text]):
            words = raw.split()
            cur = ""
            for w in words:
                trial = (cur + " " + w).strip()
                if len(trial) <= max_chars:
                    cur = trial
                else:
                    if cur:
                        lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
        return lines or [""]

    cap_y = top + 50  # leave room above for the legend
    for p in scene.primitives:
        if not isinstance(p, Caption):
            continue
        if p.anchor == "right":
            for line in _wrap_words(p.text, max_chars=36):
                out.append(
                    f'<text x="{W - right + 16:.1f}" y="{cap_y:.1f}" '
                    f'font-size="14" font-family="serif" fill="#222">'
                    f'{_esc(_pretty_expr(line))}</text>'
                )
                cap_y += 22
            cap_y += 8  # paragraph break

    # Auto-label every Plot in the upper-right corner so the reader
    # knows which curve is which.
    legend_y = top + 18
    for plot in plot_by_label.values():
        out.append(
            f'<text x="{W - right - 12:.1f}" y="{legend_y:.1f}" '
            f'font-size="15" font-family="serif" text-anchor="end" '
            f'fill="{plot.color}">'
            f'{_esc(plot.label)}(x) = {_esc(_pretty_expr(plot.f))}'
            f'</text>'
        )
        legend_y += 22

    out.append('</svg>')
    svg = "\n".join(out)

    # Narration: use explicit narration strings if the LLM supplied any;
    # otherwise synthesise a generic intro from the primitives.
    narration: List[dict] = []
    if scene.narration:
        for s in scene.narration:
            narration.append({"speak": s, "highlight": ["title"]
                              if scene.title else []})
    else:
        narration.append({
            "speak": (f"Figure: {scene.title}." if scene.title
                      else "A mathematical figure."),
            "highlight": ["title"] if scene.title else [],
        })
    return svg, narration


# ----------------------------------------------------------------------
# LLM extraction: gpt-4o-mini emits the Scene as structured JSON
# ----------------------------------------------------------------------


SCENE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "primitives": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["plot", "axis_mark", "mark_point",
                                 "tangent_at", "caption"],
                    },
                    # Plot
                    "f": {"type": ["string", "null"]},
                    "x_min": {"type": ["number", "null"]},
                    "x_max": {"type": ["number", "null"]},
                    "label": {"type": ["string", "null"]},
                    # AxisMark
                    "x": {"type": ["number", "null"]},
                    "axis": {"type": ["string", "null"],
                             "enum": ["x", "y", None]},
                    # MarkPoint / TangentAt
                    "curve": {"type": ["string", "null"]},
                    "mode": {"type": ["string", "null"],
                             "enum": ["line", "to_zero", None]},
                    # Caption
                    "text": {"type": ["string", "null"]},
                    "anchor": {"type": ["string", "null"],
                               "enum": ["right", "top", "bottom", None]},
                },
                "required": ["kind", "f", "x_min", "x_max", "label", "x",
                             "axis", "curve", "mode", "text", "anchor"],
            },
            "maxItems": 16,
        },
        "narration": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
    },
    "required": ["title", "primitives", "narration"],
}


_EXTRACTOR_SYSTEM = (
    "You are an FDL (Figure Description Language) extractor.  Given a "
    "math prompt, compose a small figure as a list of MATH-MEANINGFUL "
    "primitives.  A deterministic renderer turns your primitives into "
    "SVG; you NEVER pick pixel coordinates, only math coordinates.\n"
    "\n"
    "PRIMITIVES (each must have kind == one of these):\n"
    "  plot:        a curve y = f(x).  Fields: f (SymPy-parseable, "
    "               '**' and '^' both work, implicit '*' ok), x_min, "
    "               x_max, label (a SHORT plain name like 'f' or 'g' — "
    "               NO JSON characters, NO punctuation, just letters).\n"
    "  axis_mark:   a labelled tick on x or y.  Fields: x, label, axis.\n"
    "  mark_point:  a red dot AT (x, f(x)) on the named curve.  You "
    "               do NOT supply y — the renderer evaluates f(x).  "
    "               Fields: curve (the plot's label), x, label.\n"
    "  tangent_at:  the TRUE tangent line at point x on the named "
    "               curve.  Slope = f'(x), computed by SymPy.  Fields: "
    "               curve, x, label, mode ('line' = symmetric extension, "
    "               'to_zero' = Newton-method style ending at x-axis "
    "               crossing).\n"
    "  caption:     right-side text.  Fields: text, anchor='right'.\n"
    "\n"
    "COMPOSITION RULES — follow these literally:\n"
    "  1. ALWAYS emit at least one plot when the prompt mentions a "
    "     function or a curve.  Without a plot, the figure is empty.\n"
    "  2. If the prompt says 'tangent at x = N' or 'derivative at "
    "     x = N graphically', you MUST emit BOTH a mark_point at "
    "     curve=<plot's label>, x=N AND a tangent_at at the same "
    "     curve and x.  Two primitives, not one.\n"
    "  3. If the prompt says 'Newton's method' / 'find the root by "
    "     iterating', you MUST emit the curve plus a mark_point and "
    "     tangent_at (mode='to_zero') for EACH iterate the prompt "
    "     mentions or the typical first 3 iterates.\n"
    "  4. If the prompt says 'where f and g intersect', emit BOTH "
    "     plots, name them 'f' and 'g', then emit a mark_point on "
    "     whichever curve at the intersection x.\n"
    "  5. Add one short caption summarising the figure's punchline "
    "     (e.g. 'Slope at x=3 is 6.', 'The tangent at x_0 hits "
    "     the x-axis at x_1 = 1.5.').\n"
    "  6. Plot range: pick x_min and x_max so EVERY mark_point and "
    "     tangent_at x value is inside [x_min, x_max] with at least "
    "     ~30 % padding on each side.\n"
    "  7. label fields are PLAIN identifiers ('f', 'g', 'h'), NOT "
    "     JSON, NOT punctuated, NOT with braces / commas.\n"
    "  8. Numeric fields you don't use MUST be null (not omitted).\n"
    "  9. Return an EMPTY primitives list ONLY when the prompt is "
    "     genuinely non-graphable (Venn diagram, flowchart, matrix "
    "     operation, abstract proof with no curve).\n"
    "\n"
    "WORKED EXAMPLES (study these — they show the expected density):\n"
    "\n"
    "Prompt: 'Show the tangent line to f(x) = x^2 at x = 3.'\n"
    "Output: {\n"
    "  title: 'Tangent to x² at x = 3',\n"
    "  primitives: [\n"
    "    {kind:'plot', f:'x**2', x_min:-1, x_max:5, label:'f', ...},\n"
    "    {kind:'mark_point', curve:'f', x:3, label:'(3, 9)', ...},\n"
    "    {kind:'tangent_at', curve:'f', x:3, label:'slope = 6',\n"
    "                                                 mode:'line'},\n"
    "    {kind:'caption', text:'f′(3) = 6, so the tangent has "
    "                          slope 6.', anchor:'right'},\n"
    "  ],\n"
    "  narration: ['At x equals 3, the tangent line has slope six.']\n"
    "}\n"
    "\n"
    "Prompt: 'Explain Newton's method visually on x^3 - 2 from x = 2.'\n"
    "Output: plot of f, mark_point at x=2 (label 'x₀'), tangent_at at "
    "x=2 mode='to_zero', mark_point at x=1.5 (label 'x₁'), tangent_at "
    "at x=1.5 mode='to_zero', mark_point at x=1.296 (label 'x₂'), "
    "caption with the iteration formula.\n"
    "\n"
    "Prompt: 'Draw a Venn diagram of A and B.'\n"
    "Output: empty primitives list (non-graphable as a function plot).\n"
    "\n"
    "Return JSON conforming to the supplied schema."
)


async def llm_extract_scene(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    timeout_s: float = 25.0,
) -> Optional[Scene]:
    """Ask the FDL extractor LLM to produce a Scene from the prompt.
    Returns None on any error or empty primitives list.
    """
    import httpx, json
    if os.environ.get("SEVIM_FDL_ROUTE", "on").lower() == "off":
        return None
    payload = {
        "model": model,
        "max_tokens": 1200,
        "temperature": 0.0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "fdl_scene",
                "schema": SCENE_SCHEMA,
                "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": _EXTRACTOR_SYSTEM},
            {"role": "user", "content": user_prompt.strip()},
        ],
    }
    headers = {"content-type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers, json=payload,
            )
        if r.status_code != 200:
            return None
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
    except Exception:  # noqa: BLE001
        return None
    raw_prims = data.get("primitives") or []
    if not raw_prims:
        return None
    # The LLM occasionally hallucinates JSON-syntax characters into
    # 'label' fields ("f},{..." instead of just "f").  Strip non-
    # identifier characters defensively so the SVG <text> output and
    # the curve-id matching downstream stay clean.
    import re
    def _clean_label(s: object) -> str:
        s2 = re.sub(r"[^A-Za-z0-9_₀₁₂₃₄₅₆₇₈₉]+", "", str(s or ""))
        return s2 or "f"
    prims: List[Primitive] = []
    for d in raw_prims:
        try:
            kind = d.get("kind")
            if kind == "plot":
                prims.append(Plot(
                    f=str(d["f"]),
                    x_min=float(d["x_min"]),
                    x_max=float(d["x_max"]),
                    label=_clean_label(d.get("label")),
                ))
            elif kind == "axis_mark":
                prims.append(AxisMark(
                    x=float(d["x"]),
                    label=str(d.get("label") or ""),
                    axis=(d.get("axis") or "x"),
                ))
            elif kind == "mark_point":
                prims.append(MarkPoint(
                    curve=_clean_label(d.get("curve")),
                    x=float(d["x"]),
                    label=(d.get("label") or None),
                ))
            elif kind == "tangent_at":
                prims.append(TangentAt(
                    curve=_clean_label(d.get("curve")),
                    x=float(d["x"]),
                    label=(d.get("label") or None),
                    mode=(d.get("mode") or "line"),
                ))
            elif kind == "caption":
                prims.append(Caption(
                    text=str(d["text"]),
                    anchor=(d.get("anchor") or "right"),
                ))
        except (KeyError, TypeError, ValueError):
            continue
    if not prims:
        return None
    # require at least one Plot — without it nothing maps to math space
    if not any(isinstance(p, Plot) for p in prims):
        return None
    return Scene(
        title=str(data.get("title") or ""),
        primitives=prims,
        narration=list(data.get("narration") or []),
    )


__all__ = [
    "Plot", "AxisMark", "MarkPoint", "TangentAt", "Caption",
    "Scene", "render_scene", "SCENE_SCHEMA", "llm_extract_scene",
]
