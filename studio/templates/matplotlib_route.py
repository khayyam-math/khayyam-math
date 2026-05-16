"""Matplotlib-based render route.

For prompts that ask for a PLOT-shaped figure — regression lines,
classified scatter with a decision boundary, function curves (sigmoid,
Gaussian, ReLU…), 3-D surfaces, contour plots — we have the LLM emit a
structured *plot spec* (a closed-vocabulary JSON object, never
executable code) and a deterministic matplotlib backend renders it to
SVG.

Why this route:
- matplotlib owns plotting the way Graphviz owns graph layout: decades
  of tuned axes, scaling, 3-D projection, contouring.
- The LLM only decides WHAT to plot; matplotlib decides every pixel,
  so figures are in-bounds and correctly scaled by construction (kills
  the oversized-blob failure mode).
- True 3-D projection for surfaces — no faked 2.5-D.
- Security: the route accepts ONLY a structured spec. No LLM-emitted
  code is ever executed.

Public API:
    is_matplotlib_prompt(prompt) -> bool
    generate_matplotlib_svg(prompt, *, api_key, base_url, model)
        -> (svg, narration) | None
"""
from __future__ import annotations

import io
import json
import re
from typing import Any, Optional

# --------------------------------------------------------------------
# Routing heuristic
# --------------------------------------------------------------------

_MATPLOTLIB_KEYWORDS: tuple[str, ...] = (
    # regression / fitting
    "regression", "best-fit", "best fit", "least squares",
    "line of best fit", "fitted curve", "fitted line", "residual",
    "overfitting", "underfitting", "bias-variance", "bias variance",
    "learning curve",
    # classification / SVM
    "scatter plot", "scatter", "svm", "support vector",
    "decision boundary", "maximum margin", "maximum-margin",
    "separating hyperplane", "kernel trick", "k-nearest", "knn",
    "classifier", "roc curve", "roc and",
    # curves / functions
    "logistic regression", "sigmoid", "activation function",
    "relu", "tanh", "gaussian", "normal distribution", "bell curve",
    "bell-shaped", "bell shaped", "probability density",
    "exponential decay", "exponential growth", "decay curve",
    "radial basis function", "function plot", "plot of the function",
    "plot the function", "graph of y", "graph the function",
    "plot y =", "plot of y", "sine wave", "sigmoid curve",
    # 3-D / surfaces / optimisation landscapes
    "3d surface", "3-d surface", "surface plot", "surface z",
    "z = f(x", "saddle point", "paraboloid", "contour plot",
    "contour lines", "level curves", "level set",
    "gradient descent", "loss landscape", "loss surface",
    "error surface", "optimization landscape",
    "optimisation landscape", "manifold",
)


def is_matplotlib_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    return any(kw in p for kw in _MATPLOTLIB_KEYWORDS)


# --------------------------------------------------------------------
# Named forms (closed vocabulary — no eval, ever)
# --------------------------------------------------------------------

def _curve_form(name: str, params: dict, xs):
    import numpy as np
    p = params or {}

    def g(k, d):
        try:
            return float(p.get(k, d))
        except (TypeError, ValueError):
            return d

    name = (name or "").lower()
    if name in ("sigmoid", "logistic"):
        k, x0 = g("k", 1.0), g("x0", 0.0)
        return 1.0 / (1.0 + np.exp(-k * (xs - x0)))
    if name == "gaussian":
        a, mu, sg = g("a", 1.0), g("mu", 0.0), g("sigma", 1.0) or 1.0
        return a * np.exp(-((xs - mu) ** 2) / (2.0 * sg * sg))
    if name in ("line", "linear"):
        return g("m", 1.0) * xs + g("b", 0.0)
    if name == "polynomial":
        coeffs = p.get("coeffs") or [0.0, 1.0]
        try:
            coeffs = [float(c) for c in coeffs]
        except (TypeError, ValueError):
            coeffs = [0.0, 1.0]
        return np.polyval(list(reversed(coeffs)), xs)
    if name in ("exp", "exponential"):
        return g("a", 1.0) * np.exp(g("k", 1.0) * xs)
    if name == "relu":
        return np.maximum(0.0, xs)
    if name == "tanh":
        return np.tanh(xs)
    return None


def _surface_form(name: str, params: dict, X, Y):
    import numpy as np
    p = params or {}

    def g(k, d):
        try:
            return float(p.get(k, d))
        except (TypeError, ValueError):
            return d

    name = (name or "").lower()
    if name == "paraboloid":
        return g("a", 1.0) * X ** 2 + g("b", 1.0) * Y ** 2
    if name == "saddle":
        return g("a", 1.0) * X ** 2 - g("b", 1.0) * Y ** 2
    if name in ("gaussian_bump", "gaussian", "bump"):
        s = g("s", 4.0) or 4.0
        return -g("a", 1.0) * np.exp(-(X ** 2 + Y ** 2) / s)
    if name == "ripple":
        r = np.sqrt(X ** 2 + Y ** 2)
        return g("a", 1.0) * np.sin(r)
    if name == "plane":
        return g("a", 1.0) * X + g("b", 1.0) * Y + g("c", 0.0)
    # Default to a simple bowl so a surface always renders.
    return X ** 2 + Y ** 2


# --------------------------------------------------------------------
# LLM spec emission
# --------------------------------------------------------------------

MATPLOTLIB_SPEC_SYSTEM = """\
You convert a math/ML figure request into a STRUCTURED PLOT SPEC for a
matplotlib renderer.  Respond with ONLY a JSON object — no prose, no
code.

Choose one "kind":

  "plot2d"  — function curves / regression lines on 2-D axes.
  "scatter" — labelled point clouds, optionally a decision boundary.
  "surface3d" — a 3-D surface z = f(x,y).
  "contour" — a 2-D contour map of z = f(x,y).

Common fields: "title", "xlabel", "ylabel" (and "zlabel" for 3-D),
"intro" (one spoken sentence introducing the figure) and "takeaway"
(one spoken sentence with the lesson).

For "plot2d" — "series": a list of curves.  Each curve is either
  {"label","note","points":[[x,y],...]}            (explicit points)
or
  {"label","note","form":"<form>","params":{...},"xrange":[lo,hi]}
where <form> is one of: sigmoid, gaussian, line, polynomial, exp,
relu, tanh.  Optional "lines": straight reference lines
  {"label","note","p1":[x,y],"p2":[x,y]}.

For "scatter" — "classes": list of
  {"label","note","color","points":[[x,y],...]}.
Optional "boundary": {"label","note","p1":[x,y],"p2":[x,y]}.

For "surface3d" / "contour" — "surface":
  {"form":"<sform>","params":{...},"xrange":[lo,hi],"yrange":[lo,hi]}
where <sform> is one of: paraboloid, saddle, gaussian_bump, ripple,
plane.  Optional "path": {"label","note","points":[[x,y],...]} (e.g.
a gradient-descent trajectory — z is computed for you).  Optional
"markers": list of {"label","note","x","y","color"}.

Rules:
  1. Give EVERY series / class / line / marker / path a short "note":
     one spoken sentence describing it (no symbols — spoken words).
  2. Prefer explicit "points" for 2-D data (10-30 points).  Pick
     concrete, sensible numbers.  Use a "form" only for clean
     textbook curves.
  3. For a regression figure: put the data in "series" or "classes"
     as points and the fit in "lines".
  4. For gradient descent: "surface" is the landscape, "path" is the
     descent trajectory toward the minimum, "markers" mark start and
     minimum.
  5. Keep it focused — at most ~6 series/classes.

Respond with ONLY the JSON object.
"""


async def llm_emit_plot_spec(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    timeout_s: float = 30.0,
) -> Optional[dict]:
    """Ask the LLM for a plot spec.  Returns the parsed dict or None."""
    import httpx
    payload = {
        "model": model,
        "max_tokens": 2200,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": MATPLOTLIB_SPEC_SYSTEM},
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
                json=payload, headers=headers,
            )
        if r.status_code != 200:
            return None
        content = r.json()["choices"][0]["message"]["content"] or ""
        spec = json.loads(content)
    except Exception:  # noqa: BLE001
        return None
    return spec if isinstance(spec, dict) else None


# --------------------------------------------------------------------
# Deterministic renderer
# --------------------------------------------------------------------

_PALETTE = ("#3d6fb4", "#cc4125", "#6aa84f", "#e69138",
            "#8e7cc3", "#45818e")


def _pts(raw: Any) -> tuple[list[float], list[float]]:
    """Coerce a list of [x,y] pairs into clean x/y arrays."""
    xs: list[float] = []
    ys: list[float] = []
    if not isinstance(raw, list):
        return xs, ys
    for it in raw:
        if isinstance(it, (list, tuple)) and len(it) >= 2:
            try:
                xs.append(float(it[0]))
                ys.append(float(it[1]))
            except (TypeError, ValueError):
                continue
    return xs, ys


def _make_responsive(svg: str) -> str:
    """Strip matplotlib's fixed pt width/height so the SVG scales."""
    m = re.search(r"<svg\b[^>]*>", svg)
    if not m:
        return svg
    tag = m.group(0)
    new = re.sub(r'\s(?:width|height)="[^"]*"', "", tag)
    if "preserveAspectRatio" not in new:
        new = new.replace("<svg", '<svg preserveAspectRatio="xMidYMid meet"', 1)
    new = new.replace(
        "<svg",
        '<svg width="100%" style="max-width:100%;max-height:90vh;'
        'height:auto;display:block;margin:0 auto;"', 1)
    return svg[:m.start()] + new + svg[m.end():]


def render_plot_spec(spec: dict) -> Optional[tuple[str, list[dict]]]:
    """Render a plot spec to (svg, narration).  Returns None on any
    failure so the caller can fall back to the LLM-SVG path."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        plt.rcParams["svg.fonttype"] = "none"
    except Exception:  # noqa: BLE001
        return None

    kind = (spec.get("kind") or "plot2d").lower()
    title = str(spec.get("title") or "")
    xlabel = str(spec.get("xlabel") or "")
    ylabel = str(spec.get("ylabel") or "")
    # narration accumulates (gid, spoken-sentence) as we draw.
    notes: list[tuple[str, str]] = []

    fig = plt.figure(figsize=(9.0, 6.2), dpi=100)
    try:
        if kind == "surface3d":
            ax = fig.add_subplot(111, projection="3d")
        else:
            ax = fig.add_subplot(111)

        if kind in ("surface3d", "contour"):
            surf = spec.get("surface") or {}
            xr = surf.get("xrange") or [-5, 5]
            yr = surf.get("yrange") or [-5, 5]
            try:
                x0, x1 = float(xr[0]), float(xr[1])
                y0, y1 = float(yr[0]), float(yr[1])
            except (TypeError, ValueError, IndexError):
                x0, x1, y0, y1 = -5, 5, -5, 5
            gx = np.linspace(x0, x1, 60)
            gy = np.linspace(y0, y1, 60)
            X, Y = np.meshgrid(gx, gy)
            Z = _surface_form(surf.get("form"), surf.get("params"), X, Y)
            if kind == "surface3d":
                col = ax.plot_surface(X, Y, Z, cmap="viridis",
                                      alpha=0.85, linewidth=0)
                col.set_gid("surface")
                ax.set_zlabel(str(spec.get("zlabel") or "z"))
            else:
                cf = ax.contourf(X, Y, Z, levels=18, cmap="viridis")
                cf.set_gid("surface")
                ax.contour(X, Y, Z, levels=18, colors="white",
                           linewidths=0.4)
            notes.append(("surface", str(surf.get("note")
                          or "This is the surface we are studying.")))
            # Descent path.
            path = spec.get("path") or {}
            px, py = _pts(path.get("points"))
            if px:
                pz = _surface_form(surf.get("form"), surf.get("params"),
                                   np.array(px), np.array(py))
                if kind == "surface3d":
                    ln, = ax.plot(px, py, pz, "o-", color="#cc4125",
                                  markersize=5, linewidth=2)
                else:
                    ln, = ax.plot(px, py, "o-", color="#cc4125",
                                  markersize=5, linewidth=2)
                ln.set_gid("path")
                if path.get("note"):
                    notes.append(("path", str(path["note"])))
            # Markers.
            for i, mk in enumerate(spec.get("markers") or []):
                try:
                    mx, my = float(mk["x"]), float(mk["y"])
                except (TypeError, ValueError, KeyError):
                    continue
                mz = float(_surface_form(surf.get("form"),
                                         surf.get("params"),
                                         np.array([mx]), np.array([my]))[0])
                gid = f"marker_{i}"
                if kind == "surface3d":
                    sc = ax.scatter([mx], [my], [mz],
                                    color=mk.get("color") or "#e69138",
                                    s=70, depthshade=False)
                else:
                    sc = ax.scatter([mx], [my],
                                    color=mk.get("color") or "#e69138",
                                    s=70, zorder=5)
                sc.set_gid(gid)
                if mk.get("note"):
                    notes.append((gid, str(mk["note"])))

        elif kind == "scatter":
            for i, cls in enumerate(spec.get("classes") or []):
                cx, cy = _pts(cls.get("points"))
                if not cx:
                    continue
                gid = f"class_{i}"
                sc = ax.scatter(cx, cy,
                                color=cls.get("color")
                                or _PALETTE[i % len(_PALETTE)],
                                label=str(cls.get("label") or f"class {i}"),
                                s=55, alpha=0.9, edgecolors="white")
                sc.set_gid(gid)
                if cls.get("note"):
                    notes.append((gid, str(cls["note"])))
            bnd = spec.get("boundary") or {}
            if bnd.get("p1") and bnd.get("p2"):
                try:
                    p1, p2 = bnd["p1"], bnd["p2"]
                    ln, = ax.plot([float(p1[0]), float(p2[0])],
                                  [float(p1[1]), float(p2[1])],
                                  "-", color="#222", linewidth=2.2,
                                  label=str(bnd.get("label")
                                            or "decision boundary"))
                    ln.set_gid("boundary")
                    if bnd.get("note"):
                        notes.append(("boundary", str(bnd["note"])))
                except (TypeError, ValueError, IndexError):
                    pass

        else:  # plot2d
            for i, s in enumerate(spec.get("series") or []):
                gid = f"series_{i}"
                color = _PALETTE[i % len(_PALETTE)]
                sx, sy = _pts(s.get("points"))
                if not sx and s.get("form"):
                    xr = s.get("xrange") or [-6, 6]
                    try:
                        lo, hi = float(xr[0]), float(xr[1])
                    except (TypeError, ValueError, IndexError):
                        lo, hi = -6, 6
                    xs = np.linspace(lo, hi, 200)
                    ys = _curve_form(s.get("form"), s.get("params"), xs)
                    if ys is None:
                        continue
                    sx, sy = list(xs), list(ys)
                if not sx:
                    continue
                marker = "o" if len(sx) <= 30 else None
                ln, = ax.plot(sx, sy, marker=marker, color=color,
                              linewidth=2,
                              label=str(s.get("label") or f"series {i}"))
                ln.set_gid(gid)
                if s.get("note"):
                    notes.append((gid, str(s["note"])))
            for j, ln_spec in enumerate(spec.get("lines") or []):
                if not (ln_spec.get("p1") and ln_spec.get("p2")):
                    continue
                try:
                    p1, p2 = ln_spec["p1"], ln_spec["p2"]
                    gid = f"line_{j}"
                    ln, = ax.plot([float(p1[0]), float(p2[0])],
                                  [float(p1[1]), float(p2[1])],
                                  "--", color="#cc4125", linewidth=2,
                                  label=str(ln_spec.get("label")
                                            or "fit"))
                    ln.set_gid(gid)
                    if ln_spec.get("note"):
                        notes.append((gid, str(ln_spec["note"])))
                except (TypeError, ValueError, IndexError):
                    pass

        if title:
            t = ax.set_title(title, fontsize=15)
            t.set_gid("fig_title")
        if xlabel:
            ax.set_xlabel(xlabel)
        if ylabel:
            ax.set_ylabel(ylabel)
        handles = ax.get_legend_handles_labels()[0]
        if handles:
            ax.legend(loc="best", fontsize=9)
        ax.grid(True, alpha=0.25)
        fig.tight_layout()

        buf = io.StringIO()
        fig.savefig(buf, format="svg", bbox_inches="tight")
        svg = buf.getvalue()
    finally:
        plt.close(fig)

    if "<svg" not in svg:
        return None
    svg = _make_responsive(svg)

    # Build narration: intro -> per-element notes -> takeaway.
    narration: list[dict] = []
    intro = str(spec.get("intro") or "").strip()
    if intro:
        narration.append({
            "speak": intro,
            "highlight": ["fig_title"] if title else [],
        })
    for gid, note in notes[:9]:
        note = note.strip()
        if note:
            narration.append({"speak": note, "highlight": [gid]})
    takeaway = str(spec.get("takeaway") or "").strip()
    if takeaway:
        last_gid = notes[-1][0] if notes else (
            "fig_title" if title else "")
        narration.append({
            "speak": takeaway,
            "highlight": [last_gid] if last_gid else [],
        })
    return svg, narration


async def generate_matplotlib_svg(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
) -> Optional[tuple[str, list[dict]]]:
    """End-to-end: prompt -> LLM plot spec -> matplotlib SVG.

    Returns (svg, narration) on success, None on any failure (caller
    falls back to the LLM-SVG path)."""
    spec = await llm_emit_plot_spec(
        user_prompt, api_key=api_key, base_url=base_url, model=model)
    if not spec:
        return None
    return render_plot_spec(spec)
