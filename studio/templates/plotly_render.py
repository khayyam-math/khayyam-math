"""Plotly renderer for the shared plot-spec format.

Consumes the SAME spec dict ``matplotlib_route.llm_emit_plot_spec``
produces, but renders it with Plotly.  Plotly's 3-D surfaces are far
cleaner than matplotlib's ``mplot3d`` (proper depth shading, lighting,
contour projection), and its 2-D plots are crisp and consistent.

Plotly rasterises 3-D scenes on export, so the output is a PNG.  It is
wrapped in a minimal ``<svg><image></svg>`` so it drops straight into
the canvas viewer's existing SVG-injection path — no viewer change.

Returns ``(svg, narration)`` in the exact shape ``render_plot_spec``
returns, or ``None`` on any failure so the caller falls back to the
matplotlib renderer.
"""
from __future__ import annotations

import base64
from typing import Optional

from studio.templates.matplotlib_route import (
    _curve_form, _pts, _safe_eval, _surface_form,
)

_W, _H = 840, 600
_PALETTE = ("#3d6fb4", "#cc4125", "#6aa84f", "#e69138",
            "#8e7cc3", "#45818e")
# Kinds Plotly renders better than matplotlib.  Everything else
# (vector fields, histograms, phase portraits, …) stays on matplotlib.
PLOTLY_KINDS = ("surface3d", "contour", "plot2d")


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _png_to_svg(png: bytes, w: int, h: int, title: str) -> str:
    """Wrap a rendered PNG in an SVG, with an optional title bar."""
    b64 = base64.b64encode(png).decode("ascii")
    th = 56 if title else 0
    total_h = h + th
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {w} {total_h}" width="{w}" height="{total_h}">',
        f'<rect width="{w}" height="{total_h}" fill="#ffffff"/>',
    ]
    if title:
        parts.append(
            f'<text x="{w / 2:.0f}" y="36" font-size="25" '
            f'text-anchor="middle" font-family="Georgia, serif" '
            f'font-weight="bold" fill="#1a3a5c">{_esc(title)}</text>')
    parts.append(
        f'<image x="0" y="{th}" width="{w}" height="{h}" '
        f'xlink:href="data:image/png;base64,{b64}"/>')
    parts.append('</svg>')
    return "".join(parts)


def _narration(spec: dict, notes: list[tuple[str, str]]) -> list[dict]:
    """Build the narration list — same shape render_plot_spec returns.

    The figure is a PNG so there are no per-element highlight targets;
    every phrase carries an empty highlight list (the voice still
    speaks; there is just no highlight box)."""
    narration: list[dict] = []
    intro = str(spec.get("intro") or "").strip()
    if intro:
        narration.append({"speak": intro, "highlight": []})
    for _gid, note in notes[:9]:
        note = (note or "").strip()
        if note:
            narration.append({"speak": note, "highlight": []})
    takeaway = str(spec.get("takeaway") or "").strip()
    if takeaway:
        narration.append({"speak": takeaway, "highlight": []})
    if not narration:
        narration.append({"speak": "Here is the figure.", "highlight": []})
    return narration


def render_plotly(spec: dict) -> Optional[tuple[str, list[dict]]]:
    """Render a plot spec with Plotly.  None on any failure."""
    kind = (spec.get("kind") or "plot2d").lower()
    if kind not in PLOTLY_KINDS:
        return None
    try:
        import numpy as np
        import plotly.graph_objects as go
    except Exception:  # noqa: BLE001
        return None

    title = str(spec.get("title") or "")
    xlabel = str(spec.get("xlabel") or "x")
    ylabel = str(spec.get("ylabel") or "y")
    notes: list[tuple[str, str]] = []

    try:
        fig = go.Figure()

        if kind in ("surface3d", "contour"):
            surf = spec.get("surface") or {}
            xr = surf.get("xrange") or [-5, 5]
            yr = surf.get("yrange") or [-5, 5]
            try:
                x0, x1 = float(xr[0]), float(xr[1])
                y0, y1 = float(yr[0]), float(yr[1])
            except (TypeError, ValueError, IndexError):
                x0, x1, y0, y1 = -5.0, 5.0, -5.0, 5.0
            gx = np.linspace(x0, x1, 90)
            gy = np.linspace(y0, y1, 90)
            X, Y = np.meshgrid(gx, gy)
            Z = _surface_form(surf.get("form"), surf.get("params"), X, Y)
            if Z is None:
                return None
            if kind == "surface3d":
                fig.add_trace(go.Surface(
                    x=gx, y=gy, z=Z, colorscale="Viridis", showscale=False,
                    lighting=dict(ambient=0.6, diffuse=0.8, roughness=0.5),
                    contours={"z": {"show": True, "usecolormap": True,
                                    "project": {"z": True}}}))
            else:
                fig.add_trace(go.Contour(
                    x=gx, y=gy, z=Z, colorscale="Viridis",
                    contours={"showlabels": True},
                    line=dict(width=0.5)))
            notes.append(("surface", str(
                surf.get("note") or "This is the surface we are studying.")))

            # Descent / trajectory path.
            path = spec.get("path") or {}
            px, py = _pts(path.get("points"))
            if px:
                if kind == "surface3d":
                    pz = _surface_form(surf.get("form"), surf.get("params"),
                                       np.array(px), np.array(py))
                    fig.add_trace(go.Scatter3d(
                        x=px, y=py, z=list(pz), mode="lines+markers",
                        line=dict(color="#cc4125", width=5),
                        marker=dict(size=4, color="#cc4125")))
                else:
                    fig.add_trace(go.Scatter(
                        x=px, y=py, mode="lines+markers",
                        line=dict(color="#cc4125", width=3),
                        marker=dict(size=7, color="#cc4125")))
                if path.get("note"):
                    notes.append(("path", str(path["note"])))

            # Markers (critical points, minima, saddles…).
            for i, mk in enumerate(spec.get("markers") or []):
                try:
                    mx, my = float(mk["x"]), float(mk["y"])
                except (TypeError, ValueError, KeyError):
                    continue
                col = mk.get("color") or "#e69138"
                label = str(mk.get("label") or "")
                if kind == "surface3d":
                    mz = float(_surface_form(
                        surf.get("form"), surf.get("params"),
                        np.array([mx]), np.array([my]))[0])
                    fig.add_trace(go.Scatter3d(
                        x=[mx], y=[my], z=[mz], mode="markers+text",
                        marker=dict(size=6, color=col),
                        text=[label], textposition="top center"))
                else:
                    fig.add_trace(go.Scatter(
                        x=[mx], y=[my], mode="markers+text",
                        marker=dict(size=12, color=col),
                        text=[label], textposition="top center"))
                if mk.get("note"):
                    notes.append((f"marker_{i}", str(mk["note"])))

            if kind == "surface3d":
                fig.update_layout(scene=dict(
                    xaxis_title=xlabel, yaxis_title=ylabel,
                    zaxis_title=str(spec.get("zlabel") or "z"),
                    # Balanced cube — without this the box is stretched
                    # to the data ranges and a tall surface looks like
                    # a spike instead of its true shape.
                    aspectmode="cube",
                    camera=dict(eye=dict(x=1.7, y=1.7, z=1.0))))
            else:
                fig.update_xaxes(title_text=xlabel)
                fig.update_yaxes(title_text=ylabel)

        else:  # plot2d
            series = spec.get("series") or []
            if not isinstance(series, list) or not series:
                return None
            for i, s in enumerate(series):
                if not isinstance(s, dict):
                    continue
                col = _PALETTE[i % len(_PALETTE)]
                label = str(s.get("label") or f"series {i + 1}")
                if s.get("form"):
                    xr = s.get("xrange") or [-10, 10]
                    try:
                        a, b = float(xr[0]), float(xr[1])
                    except (TypeError, ValueError, IndexError):
                        a, b = -10.0, 10.0
                    xs = np.linspace(a, b, 400)
                    ys = _curve_form(s.get("form"), s.get("params") or {}, xs)
                    if ys is None:
                        continue
                    fig.add_trace(go.Scatter(
                        x=list(xs), y=list(np.asarray(ys, dtype=float)),
                        mode="lines", name=label,
                        line=dict(color=col, width=2.5)))
                else:
                    xs, ys = _pts(s.get("points"))
                    if not xs:
                        continue
                    mode = "markers" if s.get("scatter") else "lines+markers"
                    fig.add_trace(go.Scatter(
                        x=xs, y=ys, mode=mode, name=label,
                        line=dict(color=col, width=2.5),
                        marker=dict(color=col, size=7)))
                if s.get("note"):
                    notes.append((f"series_{i}", str(s["note"])))
            fig.update_xaxes(title_text=xlabel, zeroline=True,
                             zerolinecolor="#888", gridcolor="#e6e6e6")
            fig.update_yaxes(title_text=ylabel, zeroline=True,
                             zerolinecolor="#888", gridcolor="#e6e6e6")

        fig.update_layout(
            width=_W, height=_H, showlegend=(kind == "plot2d"),
            margin=dict(l=62, r=24, t=24, b=58),
            paper_bgcolor="white", plot_bgcolor="white",
            font=dict(family="Georgia, serif", size=15, color="#222"),
            legend=dict(bgcolor="rgba(255,255,255,0.7)"))

        png = fig.to_image(format="png", width=_W, height=_H, scale=2)
    except Exception:  # noqa: BLE001
        return None

    if not png:
        return None
    return _png_to_svg(png, _W, _H, title), _narration(spec, notes)
