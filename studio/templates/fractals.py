"""Deterministic renderers for the classic fractals.

Fractals are defined by an exact recursive construction, so the LLM-SVG
path (which sketches a couple of triangles and gives up) is strictly worse
than computing the geometry directly.  We render the Koch snowflake,
Sierpinski triangle, Sierpinski carpet, and Menger sponge from their
recursions, so the figure is the actual fractal at several levels of
depth, with its fractal dimension and the area/perimeter behaviour stated.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any

_W, _H = 940, 600


def _text(x: float, y: float, s: str, *, fs: int = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def _frame(svg_body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
            + svg_body + "</svg>")


# ── Koch snowflake ───────────────────────────────────────────────────
def _koch(p1, p2, depth, out):
    if depth == 0:
        out.append(p2)
        return
    (x1, y1), (x2, y2) = p1, p2
    dx, dy = (x2 - x1) / 3.0, (y2 - y1) / 3.0
    a = (x1 + dx, y1 + dy)
    b = (x1 + 2 * dx, y1 + 2 * dy)
    # apex of the outward bump: rotate (b - a) by -60 degrees about a
    ang = math.radians(-60)
    vx, vy = b[0] - a[0], b[1] - a[1]
    peak = (a[0] + vx * math.cos(ang) - vy * math.sin(ang),
            a[1] + vx * math.sin(ang) + vy * math.cos(ang))
    _koch(p1, a, depth - 1, out)
    _koch(a, peak, depth - 1, out)
    _koch(peak, b, depth - 1, out)
    _koch(b, p2, depth - 1, out)


def _koch_poly(cx, cy, R, depth):
    verts = [(cx + R * math.cos(math.radians(a)),
              cy + R * math.sin(math.radians(a))) for a in (-90, 30, 150)]
    poly = [verts[0]]
    for i in range(3):
        _koch(verts[i], verts[(i + 1) % 3], depth, poly)
    return poly


def render_koch():
    body = [_text(_W / 2, 40, "The Koch Snowflake", fs=21, anchor="middle",
                  weight="700")]
    poly = _koch_poly(350, 320, 175, 4)
    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in poly) + " Z"
    body.append(f'<path d="{d}" fill="#dbeafe" stroke="#1a3a63" '
                f'stroke-width="1.4"/>')
    # construction row: iterations 0,1,2 small
    for k, n in enumerate((0, 1, 2)):
        sx, sy = 660 + k * 0, 130 + k * 130
        p = _koch_poly(720, sy, 48, n)
        dd = "M " + " L ".join(f"{a:.1f},{b:.1f}" for a, b in p) + " Z"
        body.append(f'<path d="{dd}" fill="#eef4fb" stroke="#1f6fe0" '
                    f'stroke-width="1"/>')
        body.append(_text(790, sy + 4, f"iteration {n}", fs=12, fill="#5a6470"))
    body.append(_text(_W / 2, 540,
                      "Each step replaces the middle third of every edge with "
                      "a bump: length ×4/3 each time, so the perimeter → ∞ "
                      "while the area stays finite. Dimension = log4/log3 ≈ "
                      "1.262.",
                      fs=13, anchor="middle", fill="#3a4250"))
    narration = [
        {"speak": "The Koch snowflake is built from an equilateral triangle by "
                  "a single rule applied over and over."},
        {"speak": "On every edge, replace the middle third with two sides of a "
                  "smaller outward equilateral triangle, turning one segment "
                  "into four."},
        {"speak": "Each iteration multiplies the total boundary length by "
                  "four thirds, so after infinitely many steps the perimeter "
                  "is infinite."},
        {"speak": "Yet the whole curve stays inside a finite region, so it "
                  "encloses a finite area: an infinite border around a finite "
                  "interior."},
        {"speak": "That mismatch is captured by its fractal dimension, log 4 "
                  "over log 3, about 1.26, between a line and a filled area."},
    ]
    return _frame("".join(body)), narration


# ── Sierpinski triangle ──────────────────────────────────────────────
def _sierp_tri(p1, p2, p3, depth, out):
    if depth == 0:
        out.append((p1, p2, p3))
        return
    m12 = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    m23 = ((p2[0] + p3[0]) / 2, (p2[1] + p3[1]) / 2)
    m13 = ((p1[0] + p3[0]) / 2, (p1[1] + p3[1]) / 2)
    _sierp_tri(p1, m12, m13, depth - 1, out)
    _sierp_tri(m12, p2, m23, depth - 1, out)
    _sierp_tri(m13, m23, p3, depth - 1, out)


def render_sierpinski_triangle():
    body = [_text(_W / 2, 40, "The Sierpinski Triangle", fs=21,
                  anchor="middle", weight="700")]
    tris = []
    _sierp_tri((350, 480), (90, 480), (220, 110), 6, tris)
    for (a, b, c) in tris:
        body.append(f'<path d="M {a[0]:.1f},{a[1]:.1f} L {b[0]:.1f},{b[1]:.1f} '
                    f'L {c[0]:.1f},{c[1]:.1f} Z" fill="#1a3a63" stroke="none"/>')
    for k, n in enumerate((0, 1, 2)):
        t2 = []
        _sierp_tri((760, 200 + k * 130), (660, 200 + k * 130),
                   (710, 110 + k * 130), n, t2)
        for (a, b, c) in t2:
            body.append(f'<path d="M {a[0]:.1f},{a[1]:.1f} L {b[0]:.1f},'
                        f'{b[1]:.1f} L {c[0]:.1f},{c[1]:.1f} Z" '
                        f'fill="#1f6fe0"/>')
        body.append(_text(800, 165 + k * 130, f"level {n}", fs=12,
                          fill="#5a6470"))
    body.append(_text(_W / 2, 540,
                      "Each triangle splits into 4 half-size copies and the "
                      "central one is removed, leaving 3. After n steps: 3ⁿ "
                      "triangles, area (3/4)ⁿ → 0. Dimension = log3/log2 ≈ "
                      "1.585.",
                      fs=13, anchor="middle", fill="#3a4250"))
    narration = [
        {"speak": "The Sierpinski triangle starts from one filled triangle and "
                  "removes material by a fixed rule."},
        {"speak": "Split the triangle into four half-size copies and delete the "
                  "middle one, leaving three corner triangles."},
        {"speak": "Apply the same deletion to each remaining triangle, forever; "
                  "after n steps there are three to the n triangles."},
        {"speak": "The total area is three quarters to the n, which shrinks to "
                  "zero, so the limit shape has no area at all."},
        {"speak": "Its fractal dimension, log 3 over log 2, about 1.585, "
                  "measures how it fills space more than a line but less than "
                  "a plane."},
    ]
    return _frame("".join(body)), narration


# ── Sierpinski carpet ────────────────────────────────────────────────
def _carpet(x, y, s, depth, out):
    if depth == 0:
        out.append((x, y, s))
        return
    t = s / 3.0
    for i in range(3):
        for j in range(3):
            if i == 1 and j == 1:
                continue
            _carpet(x + i * t, y + j * t, t, depth - 1, out)


def render_sierpinski_carpet():
    body = [_text(_W / 2, 40, "The Sierpinski Carpet", fs=21, anchor="middle",
                  weight="700")]
    sq = []
    _carpet(70, 95, 410, 4, sq)
    for (x, y, s) in sq:
        body.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{s:.2f}" '
                    f'height="{s:.2f}" fill="#1a3a63"/>')
    for k, n in enumerate((0, 1, 2)):
        s2 = []
        _carpet(660, 100 + k * 135, 95, n, s2)
        for (x, y, s) in s2:
            body.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{s:.2f}" '
                        f'height="{s:.2f}" fill="#1f6fe0"/>')
        body.append(_text(770, 150 + k * 135, f"level {n}", fs=12,
                          fill="#5a6470"))
    body.append(_text(_W / 2, 545,
                      "Divide each square into a 3×3 grid and remove the "
                      "centre; repeat on the 8 survivors. Area (8/9)ⁿ → 0. "
                      "Dimension = log8/log3 ≈ 1.893. Its 3-D analogue is the "
                      "Menger sponge.",
                      fs=13, anchor="middle", fill="#3a4250"))
    narration = [
        {"speak": "The Sierpinski carpet is the square version of the same "
                  "remove-the-middle idea."},
        {"speak": "Cut every square into a three by three grid of nine and "
                  "delete the central one, keeping the eight around the edge."},
        {"speak": "Repeat on each of those eight squares without end; the "
                  "remaining area is eight ninths to the n."},
        {"speak": "That fraction tends to zero, so the carpet, like the "
                  "triangle, ends up with zero area but infinitely intricate "
                  "structure."},
        {"speak": "Its dimension is log 8 over log 3, about 1.89, very close "
                  "to a full plane. Stacking this rule in three dimensions "
                  "gives the Menger sponge."},
    ]
    return _frame("".join(body)), narration


# ── Menger sponge (isometric level 1) ────────────────────────────────
def _iso(i, j, k, s, ox, oy):
    # simple isometric: x right-down, k back-down, j up
    sx = ox + (i - k) * s * 0.866
    sy = oy + (i + k) * s * 0.5 - j * s
    return sx, sy


def render_menger():
    body = [_text(_W / 2, 40, "The Menger Sponge", fs=21, anchor="middle",
                  weight="700")]
    s, ox, oy = 62, 300, 250

    def _bil(c00, c10, c11, c01, u, v):
        return ((1 - u) * (1 - v) * c00[0] + u * (1 - v) * c10[0]
                + u * v * c11[0] + (1 - u) * v * c01[0],
                (1 - u) * (1 - v) * c00[1] + u * (1 - v) * c10[1]
                + u * v * c11[1] + (1 - u) * v * c01[1])

    def _cell(c00, c10, c11, c01, a, b, fill):
        # sub-cell (a,b) of a 3x3 split of the parallelogram c00..c01
        q = [_bil(c00, c10, c11, c01, a / 3, b / 3),
             _bil(c00, c10, c11, c01, (a + 1) / 3, b / 3),
             _bil(c00, c10, c11, c01, (a + 1) / 3, (b + 1) / 3),
             _bil(c00, c10, c11, c01, a / 3, (b + 1) / 3)]
        return ('<path d="M ' + " L ".join(f"{x:.1f},{y:.1f}" for x, y in q)
                + f' Z" fill="{fill}" stroke="#27425f" stroke-width="0.7"/>')

    # three visible faces of the outer cube (j=3 top, i=3 right, k=3 left);
    # each a 3x3 grid whose centre cell is the bored-out hole.
    faces = [
        ([_iso(0, 3, 0, s, ox, oy), _iso(3, 3, 0, s, ox, oy),
          _iso(3, 3, 3, s, ox, oy), _iso(0, 3, 3, s, ox, oy)], "#cfe0f5", "#3a567a"),  # top j=3
        ([_iso(3, 3, 0, s, ox, oy), _iso(3, 0, 0, s, ox, oy),
          _iso(3, 0, 3, s, ox, oy), _iso(3, 3, 3, s, ox, oy)], "#6f93c4", "#2c4258"),  # right i=3
        ([_iso(0, 3, 3, s, ox, oy), _iso(0, 0, 3, s, ox, oy),
          _iso(3, 0, 3, s, ox, oy), _iso(3, 3, 3, s, ox, oy)], "#9bb8de", "#34506f"),  # left k=3
    ]
    for corners, light, hole in faces:
        for a in range(3):
            for b in range(3):
                body.append(_cell(*corners, a, b,
                                  hole if (a == 1 and b == 1) else light))
    # facts panel
    body.append(_text(640, 150, "Level 1: a 3×3×3 cube of 27 cells", fs=14,
                      weight="600"))
    body.append(_text(640, 174, "with the centre and the 6 face", fs=13))
    body.append(_text(640, 194, "centres bored out → 20 cubes.", fs=13))
    body.append(_text(640, 232, "Repeat inside each of the 20", fs=13))
    body.append(_text(640, 252, "cubes: 20ⁿ cubes at level n.", fs=13))
    body.append(_text(640, 290, "Volume (20/27)ⁿ → 0,", fs=13, fill="#b03a3a"))
    body.append(_text(640, 310, "surface area → ∞.", fs=13, fill="#b03a3a"))
    body.append(_text(640, 348, "Dimension = log20/log3 ≈ 2.727.", fs=13,
                      weight="600"))
    body.append(_text(640, 372, "Every face is a Sierpinski carpet.", fs=12,
                      fill="#5a6470"))
    narration = [
        {"speak": "The Menger sponge is the three-dimensional cousin of the "
                  "Sierpinski carpet."},
        {"speak": "Take a cube, divide it into a three by three by three grid "
                  "of twenty-seven smaller cubes, and drill out the very "
                  "centre together with the centre of each of the six faces."},
        {"speak": "That leaves twenty cubes. Now apply exactly the same "
                  "drilling inside each of those twenty, without end."},
        {"speak": "At level n there are twenty to the n cubes, so the volume "
                  "is twenty over twenty-seven to the n, which collapses to "
                  "zero, while the surface area grows without bound."},
        {"speak": "Its fractal dimension is log twenty over log three, about "
                  "2.73, and tellingly, every flat face of the sponge is "
                  "itself a Sierpinski carpet."},
    ]
    return _frame("".join(body)), narration


# ── routing ──────────────────────────────────────────────────────────
def which_fractal(prompt: str):
    p = (prompt or "").lower()
    if "menger" in p:
        return "menger"
    if "koch" in p or ("snowflake" in p and "fractal" not in p) or "snowflake" in p:
        return "koch"
    if "carpet" in p and ("sierpinski" in p or "sierpinsky" in p or "fractal" in p):
        return "sierpinski_carpet"
    if ("sierpinski" in p or "sierpinsky" in p) and "triangle" in p:
        return "sierpinski_triangle"
    if "sierpinski" in p or "sierpinsky" in p:
        return "sierpinski_triangle"   # default Sierpinski = the triangle
    return None


def is_fractal_prompt(prompt: str) -> bool:
    return which_fractal(prompt) is not None


_RENDER = {
    "koch": render_koch,
    "sierpinski_triangle": render_sierpinski_triangle,
    "sierpinski_carpet": render_sierpinski_carpet,
    "menger": render_menger,
}


async def generate_fractal_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
):
    kind = which_fractal(prompt)
    if kind is None:
        return None
    return _RENDER[kind]()
