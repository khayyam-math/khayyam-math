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


def _caption(y: float, s: str, *, fs: int = 13, x: float | None = None,
             max_w: float = _W - 90, fill: str = "#3a4250",
             line_h: float | None = None) -> str:
    """A centred footer caption, wrapped so it cannot leave the canvas.

    The renderers used to emit their closing sentence as one ``<text>``
    anchored at the middle of a 940-wide viewBox.  At 13px a 200-char
    sentence is roughly 1350px wide, so both ends were simply clipped
    off — the Koch footer opened mid-word ("p replaces the middle
    third…") and lost its dimension figure at the other end.  Wrapping
    to ``max_w`` keeps the whole sentence readable.

    Width is estimated at 0.52em per character, which is a slight
    over-estimate for this sans stack — erring toward breaking early
    is the safe direction, since a short line is fine and a clipped
    one is not.
    """
    cx = _W / 2 if x is None else x
    lh = fs + 6 if line_h is None else line_h
    per_line = max(12, int(max_w / (fs * 0.52)))
    words, lines, cur = s.split(), [], ""
    for w in words:
        trial = w if not cur else f"{cur} {w}"
        if len(trial) > per_line and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return "".join(
        _text(cx, y + i * lh, line, fs=fs, anchor="middle", fill=fill)
        for i, line in enumerate(lines)
    )


def _frame(svg_body: str) -> str:
    # xlink is declared unconditionally: the escape-time renderers embed
    # their field as an <image xlink:href="data:image/png…">, and an
    # undeclared prefix makes the whole document invalid XML — which the
    # pre-deploy quality gate rejects outright.
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
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
    body.append(_caption(536,
                         "Each step replaces the middle third of every edge "
                         "with a bump: length ×4/3 each time, so the "
                         "perimeter → ∞ while the area stays finite. "
                         "Dimension = log4/log3 ≈ 1.262."))
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
    body.append(_caption(540,
                      "Each triangle splits into 4 half-size copies and the "
                      "central one is removed, leaving 3. After n steps: 3ⁿ "
                      "triangles, area (3/4)ⁿ → 0. Dimension = log3/log2 ≈ "
                      "1.585.",
                         fs=13))
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
    body.append(_caption(545,
                      "Divide each square into a 3×3 grid and remove the "
                      "centre; repeat on the 8 survivors. Area (8/9)ⁿ → 0. "
                      "Dimension = log8/log3 ≈ 1.893. Its 3-D analogue is the "
                      "Menger sponge.",
                         fs=13))
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


def _bil(c00, c10, c11, c01, u, v):
    """Bilinear point at (u, v) inside the parallelogram c00..c01."""
    return ((1 - u) * (1 - v) * c00[0] + u * (1 - v) * c10[0]
            + u * v * c11[0] + (1 - u) * v * c01[0],
            (1 - u) * (1 - v) * c00[1] + u * (1 - v) * c10[1]
            + u * v * c11[1] + (1 - u) * v * c01[1])


def _carpet_hole(a: int, b: int, depth: int) -> bool:
    """Is cell (a, b) of a 3^depth grid removed from a Sierpinski carpet?

    Every face of the Menger sponge IS a Sierpinski carpet, so the
    face at level n is exactly this test at ``depth = n``: a cell is
    a hole when, at ANY level of the base-3 expansion of its
    coordinates, both digits are the middle one.
    """
    for _ in range(depth):
        if a % 3 == 1 and b % 3 == 1:
            return True
        a //= 3
        b //= 3
    return False


def _sponge(s: float, ox: float, oy: float, depth: int) -> list[str]:
    """The three visible faces of a level-``depth`` Menger sponge."""
    n = 3 ** depth
    sw = 0.7 if depth == 1 else 0.35

    def _cell(c00, c10, c11, c01, a, b, fill):
        q = [_bil(c00, c10, c11, c01, a / n, b / n),
             _bil(c00, c10, c11, c01, (a + 1) / n, b / n),
             _bil(c00, c10, c11, c01, (a + 1) / n, (b + 1) / n),
             _bil(c00, c10, c11, c01, a / n, (b + 1) / n)]
        return ('<path d="M ' + " L ".join(f"{x:.1f},{y:.1f}" for x, y in q)
                + f' Z" fill="{fill}" stroke="#27425f" '
                  f'stroke-width="{sw}"/>')

    faces = [
        ([_iso(0, 3, 0, s, ox, oy), _iso(3, 3, 0, s, ox, oy),
          _iso(3, 3, 3, s, ox, oy), _iso(0, 3, 3, s, ox, oy)],
         "#cfe0f5", "#3a567a"),                                # top   j=3
        ([_iso(3, 3, 0, s, ox, oy), _iso(3, 0, 0, s, ox, oy),
          _iso(3, 0, 3, s, ox, oy), _iso(3, 3, 3, s, ox, oy)],
         "#6f93c4", "#2c4258"),                                # right i=3
        ([_iso(0, 3, 3, s, ox, oy), _iso(0, 0, 3, s, ox, oy),
          _iso(3, 0, 3, s, ox, oy), _iso(3, 3, 3, s, ox, oy)],
         "#9bb8de", "#34506f"),                                # left  k=3
    ]
    out = []
    for corners, light, hole in faces:
        for a in range(n):
            for b in range(n):
                out.append(_cell(*corners, a, b,
                                 hole if _carpet_hole(a, b, depth) else light))
    return out


def render_menger():
    """Two iterations side by side.

    The original figure drew level 1 only, and the field report said so
    ("50/50. More iterations would be even more informative.").  One
    iteration of a recursive object shows the RULE but not the
    recursion — level 2 beside it is what makes the self-similarity
    visible, and it is also where the "every face is a Sierpinski
    carpet" claim in the facts panel becomes something you can check
    by eye rather than take on trust.
    """
    body = [_text(_W / 2, 40, "The Menger Sponge", fs=21, anchor="middle",
                  weight="700")]
    s = 34
    for ox, depth, label in ((175, 1, "Level 1 — 20 cubes"),
                             (430, 2, "Level 2 — 400 cubes")):
        body.extend(_sponge(s, ox, 250, depth))
        # The sponge spans oy ± 3s, so the label has to clear y = 352.
        body.append(_text(ox, 382, label, fs=13, anchor="middle",
                          weight="600", fill="#2c4258"))
    body.append(_text(303, 250, "→", fs=26, anchor="middle", fill="#7a8794"))
    # facts panel
    body.append(_text(620, 150, "Level 1: a 3×3×3 cube of 27 cells", fs=14,
                      weight="600"))
    body.append(_text(620, 174, "with the centre and the 6 face", fs=13))
    body.append(_text(620, 194, "centres bored out → 20 cubes.", fs=13))
    body.append(_text(620, 232, "Repeat inside each of the 20", fs=13))
    body.append(_text(620, 252, "cubes: 20ⁿ cubes at level n,", fs=13))
    body.append(_text(620, 272, "so level 2 already has 400.", fs=13))
    body.append(_text(620, 310, "Volume (20/27)ⁿ → 0,", fs=13, fill="#b03a3a"))
    body.append(_text(620, 330, "surface area → ∞.", fs=13, fill="#b03a3a"))
    body.append(_text(620, 368, "Dimension = log20/log3 ≈ 2.727.", fs=13,
                      weight="600"))
    body.append(_text(620, 392, "Every face is a Sierpinski carpet.", fs=12,
                      fill="#5a6470"))
    body.append(_caption(438,
                         "Each level applies the same drilling inside every "
                         "surviving cube, so the level-2 faces are the "
                         "level-1 carpet with the rule applied again inside "
                         "each of its eight surviving squares.",
                         fs=12.5))
    narration = [
        {"speak": "The Menger sponge is the three-dimensional cousin of the "
                  "Sierpinski carpet."},
        {"speak": "Take a cube, divide it into a three by three by three grid "
                  "of twenty-seven smaller cubes, and drill out the very "
                  "centre together with the centre of each of the six faces."},
        {"speak": "That leaves twenty cubes, the level one sponge on the left. "
                  "Applying the identical drilling inside each of those twenty "
                  "gives the level two sponge on the right, four hundred "
                  "cubes."},
        {"speak": "At level n there are twenty to the n cubes, so the volume "
                  "is twenty over twenty-seven to the n, which collapses to "
                  "zero, while the surface area grows without bound."},
        {"speak": "Its fractal dimension is log twenty over log three, about "
                  "2.73, and tellingly, every flat face of the sponge is "
                  "itself a Sierpinski carpet."},
    ]
    return _frame("".join(body)), narration


# ── escape-time fractals (Mandelbrot / Julia) ────────────────────────
_MAXIT = 60


def _palette(it):
    if it >= _MAXIT:
        return "#0a1124"                      # interior
    t = it / _MAXIT
    r = max(0, min(255, int(12 + 243 * t ** 0.6)))
    g = max(0, min(255, int(28 + 180 * t ** 1.2)))
    b = max(0, min(255, int(90 + 120 * (1 - t))))
    return f"#{r:02x}{g:02x}{b:02x}"


def _rle(rows, ox, oy, cell):
    out = []
    for jy, row in enumerate(rows):
        ix, n = 0, len(row)
        while ix < n:
            it = row[ix]
            run = 1
            while ix + run < n and row[ix + run] == it:
                run += 1
            out.append(f'<rect x="{ox + ix * cell:.1f}" y="{oy + jy * cell:.1f}" '
                       f'width="{run * cell + 0.4:.1f}" height="{cell + 0.4:.1f}" '
                       f'fill="{_palette(it)}"/>')
            ix += run
    return "".join(out)


def _escape_field(wc, hc, xmin, xmax, ymin, ymax, step, *, flip_y=False):
    """Escape-iteration counts on a wc×hc grid.

    ``flip_y=True`` walks the imaginary axis from ymax DOWN to ymin, so
    row 0 is the top of the picture and the result is in standard
    orientation (+i up).  That matters as soon as anything is labelled
    on the plane: for a c with a non-zero imaginary part the set is not
    symmetric about the real axis, so an unflipped field would put
    marked points on the wrong side.
    """
    rows = []
    for jy in range(hc):
        t = jy / (hc - 1)
        cy = (ymax - (ymax - ymin) * t) if flip_y else (ymin + (ymax - ymin) * t)
        row = []
        for ix in range(wc):
            cx = xmin + (xmax - xmin) * ix / (wc - 1)
            row.append(step(cx, cy))
        rows.append(row)
    return rows


def _rgb(it, maxit):
    if it >= maxit:
        return (10, 17, 36)                       # interior
    t = it / maxit
    return (max(0, min(255, int(12 + 243 * t ** 0.6))),
            max(0, min(255, int(28 + 180 * t ** 1.2))),
            max(0, min(255, int(90 + 120 * (1 - t)))))


def _field_image(rows, ox, oy, w, h, maxit):
    """The escape field as ONE embedded PNG rather than thousands of rects.

    Run-length rectangles cost roughly 3 KB per output pixel-row: the
    168×130 Julia field alone was 280 KB of SVG, which capped the
    resolution we could ship and made the fractal visibly blocky
    (field report 2026-07-06: "Not detailed enough").  A base64 PNG of
    the same field at 300×230 is an order of magnitude smaller, so
    detail goes UP and payload goes DOWN at the same time.

    Falls back to the rect encoding if Pillow is unavailable, so the
    route degrades instead of failing.
    """
    try:
        import base64
        import io as _io
        from PIL import Image
    except Exception:  # noqa: BLE001 — Pillow missing; use the old path
        cell = w / max(1, len(rows[0]))
        return _rle(rows, ox, oy, cell)
    hc, wc = len(rows), len(rows[0])
    img = Image.new("RGB", (wc, hc))
    img.putdata([_rgb(it, maxit) for row in rows for it in row])
    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return (f'<image x="{ox:.1f}" y="{oy:.1f}" width="{w:.1f}" '
            f'height="{h:.1f}" preserveAspectRatio="none" '
            f'xlink:href="data:image/png;base64,{b64}" '
            f'href="data:image/png;base64,{b64}"/>')


def render_mandelbrot():
    def step(cx, cy):
        zr = zi = 0.0
        for it in range(_MAXIT):
            zr2, zi2 = zr * zr, zi * zi
            if zr2 + zi2 > 4.0:
                return it
            zi = 2 * zr * zi + cy
            zr = zr2 - zi2 + cx
        return _MAXIT
    # Same PNG-field trick as the Julia route: 2.3× the sampling
    # density of the old run-length rectangles, in a fraction of the
    # bytes, so the boundary reads as a fractal rather than as steps.
    iw, ih = 386, 314
    rows = _escape_field(390, 317, -2.4, 0.8, -1.3, 1.3, step, flip_y=True)
    ox = (_W - iw) / 2
    body = [_text(_W / 2, 40, "The Mandelbrot Set", fs=21, anchor="middle",
                  weight="700"), _field_image(rows, ox, 90, iw, ih, _MAXIT)]
    body.append(_caption(90 + ih + 28,
                      "Points c for which z ↦ z² + c stays bounded from "
                      "z₀ = 0. The boundary is an infinitely intricate "
                      "fractal of dimension 2.",
                         fs=13))
    narration = [
        {"speak": "The Mandelbrot set is the most famous fractal in "
                  "mathematics, living in the plane of complex numbers."},
        {"speak": "For each point c, repeatedly apply the rule z becomes z "
                  "squared plus c, starting from zero."},
        {"speak": "If the values stay bounded forever, c belongs to the set, "
                  "the dark interior; if they fly off to infinity, c is "
                  "outside."},
        {"speak": "The colours outside record how quickly each point escapes, "
                  "which is what paints the glowing bands around the set."},
        {"speak": "Its boundary is endlessly detailed: zoom in anywhere and "
                  "tiny copies of the whole shape reappear, a hallmark of "
                  "self-similar fractals."},
    ]
    return _frame("".join(body)), narration


_JULIA_MAXIT = 140


def render_julia():
    """The set, plus the iteration that defines it.

    Field report 2026-07-06: "Not detailed enough".  The old figure was
    a single 168×130 escape field — visibly blocky, and it showed only
    the OUTCOME of the iteration while the bottom half of the canvas
    sat empty.  The request had explicitly asked to see the iteration
    itself: starting points, their orbits, bounded versus escaping,
    and a zoom.

    So this draws three things that reinforce each other: the set at
    2.6× the old resolution, two worked orbits with the actual numbers
    (one bounded, one escaping, both marked on the plane where they
    start), and a zoom into the boundary showing the detail continues
    below the pixel scale.
    """
    cr, ci = -0.8, 0.156
    xmin, xmax, ymin, ymax = -1.7, 1.7, -1.3, 1.3

    def step(zr, zi):
        for it in range(_JULIA_MAXIT):
            zr2, zi2 = zr * zr, zi * zi
            if zr2 + zi2 > 4.0:
                return it
            zi = 2 * zr * zi + ci
            zr = zr2 - zi2 + cr
        return _JULIA_MAXIT

    def orbit(z0r, z0i, n):
        """First n+1 iterates of z ↦ z² + c, as (re, im, |z|)."""
        pts, zr, zi = [], z0r, z0i
        for _ in range(n + 1):
            pts.append((zr, zi, math.hypot(zr, zi)))
            zr, zi = zr * zr - zi * zi + cr, 2 * zr * zi + ci
        return pts

    # ── main field ────────────────────────────────────────────────
    ox, oy, iw, ih = 40, 76, 470, 359
    rows = _escape_field(300, 230, xmin, xmax, ymin, ymax, step, flip_y=True)
    body = [_text(_W / 2, 40, "A Julia Set", fs=21, anchor="middle",
                  weight="700"),
            _field_image(rows, ox, oy, iw, ih, _JULIA_MAXIT)]

    def to_px(re, im):
        return (ox + (re - xmin) / (xmax - xmin) * iw,
                oy + (ymax - im) / (ymax - ymin) * ih)

    # ── two worked orbits ─────────────────────────────────────────
    # z0 = 0 sits in the interior for this c, so its orbit is bounded;
    # a point out near the corner escapes fast.  Both are computed, not
    # asserted, so the table can never drift from the picture.
    samples = [
        ((0.0, 0.0), "#7ef7c8", "bounded"),
        ((0.95, 0.62), "#ffd166", "escapes"),
    ]
    for (z0r, z0i), colour, _kind in samples:
        px, py = to_px(z0r, z0i)
        body.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" '
                    f'fill="none" stroke="{colour}" stroke-width="2.2"/>')
        body.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="1.8" '
                    f'fill="{colour}"/>')

    # ── zoom inset on the boundary ────────────────────────────────
    zx, zy, zw, zh = 548, 76, 230, 177
    zc_r, zc_i, half = -0.62, 0.30, 0.16
    zrows = _escape_field(190, 146, zc_r - half, zc_r + half,
                          zc_i - half * 0.77, zc_i + half * 0.77,
                          step, flip_y=True)
    body.append(_field_image(zrows, zx, zy, zw, zh, _JULIA_MAXIT))
    body.append(f'<rect x="{zx:.1f}" y="{zy:.1f}" width="{zw}" '
                f'height="{zh}" fill="none" stroke="#5a6470" '
                f'stroke-width="1"/>')
    # the same window outlined on the main image, so the zoom is located
    bx0, by0 = to_px(zc_r - half, zc_i + half * 0.77)
    bx1, by1 = to_px(zc_r + half, zc_i - half * 0.77)
    body.append(f'<rect x="{bx0:.1f}" y="{by0:.1f}" '
                f'width="{bx1 - bx0:.1f}" height="{by1 - by0:.1f}" '
                f'fill="none" stroke="#ffffff" stroke-width="1.2"/>')
    body.append(_text(zx + zw / 2, zy + zh + 18,
                      f"zoom ×{(xmax - xmin) / (2 * half):.0f} — the detail "
                      f"never runs out", fs=11.5, anchor="middle",
                      fill="#5a6470"))

    # ── orbit table ───────────────────────────────────────────────
    ty = 300
    body.append(_text(548, ty, "Same rule, two starting points:", fs=13.5,
                      weight="600"))
    body.append(_text(548, ty + 20, "zₙ₊₁ = zₙ² + c,  "
                                    "c = −0.8 + 0.156i", fs=12.5,
                      fill="#3a4250"))
    row_y = ty + 46
    for (z0r, z0i), colour, kind in samples:
        pts = orbit(z0r, z0i, 4)
        body.append(f'<circle cx="554" cy="{row_y - 4:.0f}" r="4.5" '
                    f'fill="{colour}" stroke="#3a4250" stroke-width="0.8"/>')
        body.append(_text(568, row_y,
                          f"z₀ = {z0r:.2f} {'+' if z0i >= 0 else '−'} "
                          f"{abs(z0i):.2f}i", fs=12.5, weight="600"))
        mags = ",  ".join(f"{m:.2f}" for _r, _i, m in pts[1:])
        body.append(_text(568, row_y + 19, f"|z₁…z₄| = {mags}", fs=12,
                          fill="#3a4250"))
        verdict = ("stays below 2 forever → in the set"
                   if kind == "bounded"
                   else "passes 2 and runs away → outside")
        body.append(_text(568, row_y + 37, verdict, fs=12,
                          fill="#2f7d5a" if kind == "bounded" else "#b03a3a"))
        row_y += 66

    body.append(_caption(500,
                         "The rule z ↦ z² + c is the same one that builds the "
                         "Mandelbrot set, but here c is FIXED and the "
                         "STARTING point varies. Points whose orbit stays "
                         "bounded form the dark filled set; the colours "
                         "outside record how fast the orbit escapes.",
                         fs=12.5, x=_W / 2, max_w=860))
    narration = [
        {"speak": "A Julia set uses the same rule as the Mandelbrot set, z "
                  "becomes z squared plus c, but with the roles swapped."},
        {"speak": "Here the constant c is fixed at minus zero point eight plus "
                  "zero point one five six i, and we instead vary the starting "
                  "point and ask whether its orbit stays bounded."},
        {"speak": "Start at zero and the successive magnitudes stay below two "
                  "for ever, so that point belongs to the set; start out near "
                  "one plus zero point six i and the magnitude passes two "
                  "within a few steps and then runs away."},
        {"speak": "Every point of the plane gets that test, and the ones that "
                  "stay bounded form the dark filled shape, while the colours "
                  "outside record how quickly each orbit escaped."},
        {"speak": "Magnifying the boundary shows the structure repeating at "
                  "every scale, and in fact c lies in the Mandelbrot set "
                  "exactly when its Julia set is connected, tying the two "
                  "fractals together."},
    ]
    return _frame("".join(body)), narration


# ── Barnsley fern (an IFS fractal modelling a natural object) ─────────
def render_barnsley_fern():
    import random
    rnd = random.Random(20240613)
    x, y = 0.0, 0.0
    pts = []
    for i in range(9000):
        r = rnd.random()
        if r < 0.01:
            x, y = 0.0, 0.16 * y
        elif r < 0.86:
            x, y = 0.85 * x + 0.04 * y, -0.04 * x + 0.85 * y + 1.6
        elif r < 0.93:
            x, y = 0.20 * x - 0.26 * y, 0.23 * x + 0.22 * y + 1.6
        else:
            x, y = -0.15 * x + 0.28 * y, 0.26 * x + 0.24 * y + 0.44
        if i > 30:
            pts.append((x, y))
    # map x in [-2.2, 2.7], y in [0, 10] to screen (y up)
    ox, oy, sc = 320, 540, 47
    dots = "".join(
        f'<circle cx="{ox + px * sc:.1f}" cy="{oy - py * sc:.1f}" r="0.7" '
        f'fill="#1f7a33"/>' for px, py in pts)
    body = [_text(_W / 2, 40, "The Barnsley Fern", fs=21, anchor="middle",
                  weight="700"), dots]
    body.append(_text(650, 150, "An iterated function system:", fs=14,
                      weight="700"))
    for k, ln in enumerate(["four affine maps, each a",
                            "rotate-scale-shift of the plane,",
                            "applied at random with fixed",
                            "probabilities to a single point."]):
        body.append(_text(650, 176 + k * 20, ln, fs=13, fill="#3a4250"))
    body.append(_text(650, 286,
                      "The orbit fills out a fern whose", fs=13))
    body.append(_text(650, 306,
                      "fronds are smaller copies of the", fs=13))
    body.append(_text(650, 326, "whole — self-similar, like real", fs=13))
    body.append(_text(650, 346, "plants. Dimension ≈ 1.74.", fs=13,
                      weight="600"))
    narration = [
        {"speak": "The Barnsley fern shows how a lifelike natural shape can "
                  "emerge from pure mathematics with almost no information."},
        {"speak": "It uses four affine transformations, each one a rotate, "
                  "scale, and shift of the plane, chosen at random with fixed "
                  "probabilities."},
        {"speak": "Start from a single point and repeatedly apply a randomly "
                  "chosen map; the points quickly settle onto the fern."},
        {"speak": "One map builds the stem, one the ever-shrinking main frond, "
                  "and two place the left and right leaflets."},
        {"speak": "Because each leaflet is a smaller copy of the whole fern, "
                  "the figure is self-similar — the same principle nature uses "
                  "to grow plants efficiently."},
    ]
    return _frame("".join(body)), narration


# ── Cantor set ───────────────────────────────────────────────────────
def render_cantor():
    body = [_text(_W / 2, 40, "The Cantor Set", fs=21, anchor="middle",
                  weight="700")]
    segs = [(0.0, 1.0)]
    x0, w, y = 90, 760, 110
    for level in range(7):
        for a, b in segs:
            body.append(f'<rect x="{x0 + a * w:.1f}" y="{y}" '
                        f'width="{(b - a) * w:.1f}" height="14" '
                        f'fill="#1a3a63"/>')
        body.append(_text(x0 + w + 14, y + 12, f"n = {level}", fs=12,
                          fill="#5a6470"))
        nxt = []
        for a, b in segs:
            t = (b - a) / 3
            nxt.append((a, a + t))
            nxt.append((b - t, b))
        segs = nxt
        y += 50
    body.append(_caption(y + 14,
                      "Remove the open middle third of every segment, forever. "
                      "Total length (2/3)ⁿ → 0, yet uncountably many points "
                      "remain. Dimension = log2/log3 ≈ 0.631.",
                         fs=13))
    narration = [
        {"speak": "The Cantor set is the simplest fractal, built on a single "
                  "line segment."},
        {"speak": "Delete the open middle third, leaving two segments; then "
                  "delete the middle third of each of those, and continue "
                  "without end."},
        {"speak": "The total length removed adds up to the whole, so what "
                  "remains has length zero."},
        {"speak": "Yet uncountably many points survive — every endpoint, and "
                  "far more — so the set is large in number while tiny in "
                  "length."},
        {"speak": "Its fractal dimension, log two over log three, about 0.63, "
                  "is between a point and a line, capturing this in-between "
                  "nature."},
    ]
    return _frame("".join(body)), narration


# ── Heighway dragon curve ────────────────────────────────────────────
def render_dragon():
    n = 13
    seq = []
    for i in range(1, 1 << n):
        seq.append(1 if (((i & -i) << 1) & i) else 0)   # 1 = left, 0 = right
    import math as _m
    x, y, ang = 0.0, 0.0, 0.0
    pts = [(x, y)]
    for turn in [None] + seq:
        if turn is not None:
            ang += 90 if turn else -90
        x += _m.cos(_m.radians(ang))
        y += _m.sin(_m.radians(ang))
        pts.append((x, y))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    sc = min(740 / (maxx - minx), 430 / (maxy - miny))
    ox, oy = 100 - minx * sc, 110 - miny * sc
    d = "M " + " L ".join(f"{ox + px * sc:.1f},{oy + py * sc:.1f}"
                          for px, py in pts)
    body = [_text(_W / 2, 40, "The Dragon Curve", fs=21, anchor="middle",
                  weight="700"),
            f'<path d="{d}" fill="none" stroke="#1a3a63" stroke-width="1.4"/>']
    body.append(_caption(560,
                      "Fold a strip of paper in half repeatedly, then unfold "
                      "each crease to 90°: the edge traces this curve. It "
                      "never crosses itself and tiles the plane. Dimension = 2.",
                         fs=13))
    narration = [
        {"speak": "The dragon curve has a charming origin: fold a long strip of "
                  "paper in half, again and again, always the same way."},
        {"speak": "Unfold it so every crease becomes a right angle, and the "
                  "edge of the strip traces out this intricate dragon."},
        {"speak": "Each extra fold doubles the curve by copying it and turning "
                  "the copy ninety degrees, which is the rule drawn here."},
        {"speak": "Remarkably, the path never crosses itself, and infinitely "
                  "many dragons fit together to tile the whole plane."},
        {"speak": "Although drawn with line segments, it wiggles so densely "
                  "that its fractal dimension is two — it fills area like a "
                  "region, not a curve."},
    ]
    return _frame("".join(body)), narration


# ── Pythagoras tree ──────────────────────────────────────────────────
def render_pythagoras_tree():
    import math as _m
    out = []

    def grow(x1, y1, x2, y2, depth):
        if depth == 0:
            return
        dx, dy = x2 - x1, y2 - y1
        # perpendicular pointing "up" the tree (screen y is down): n = (dy, -dx)
        p3 = (x2 + dy, y2 - dx)
        p4 = (x1 + dy, y1 - dx)
        shade = 30 + depth * 16
        out.append(f'<path d="M {x1:.1f},{y1:.1f} L {x2:.1f},{y2:.1f} '
                   f'L {p3[0]:.1f},{p3[1]:.1f} L {p4[0]:.1f},{p4[1]:.1f} Z" '
                   f'fill="rgb({shade//2},{min(160,shade+70)},{shade//2})" '
                   f'stroke="#234a23" stroke-width="0.4"/>')
        # apex of the 45-45-90 cap on the top edge (p4 -> p3, direction (dx,dy))
        mx, my = (p4[0] + p3[0]) / 2, (p4[1] + p3[1]) / 2
        ax, ay = mx + dy / 2, my - dx / 2
        grow(p4[0], p4[1], ax, ay, depth - 1)
        grow(ax, ay, p3[0], p3[1], depth - 1)

    # Trunk sits high enough that the canopy (which grows ~2.6× the
    # trunk width upward) uses the empty band under the title instead
    # of hugging the bottom edge, and leaves two caption lines clear.
    grow(430, 512, 510, 512, 11)
    body = [_text(_W / 2, 40, "The Pythagoras Tree", fs=21, anchor="middle",
                  weight="700")] + out
    body.append(_caption(552,
                         "On each square, build two smaller squares meeting "
                         "at a right angle (a 45° split) and repeat. The "
                         "squares on the two children always sum to the "
                         "parent — the Pythagorean theorem.",
                         fs=12.5))
    narration = [
        {"speak": "The Pythagoras tree turns the most famous theorem in "
                  "geometry into a growing fractal."},
        {"speak": "Start from a square. On its top edge, erect a right "
                  "triangle, and build a smaller square on each of the "
                  "triangle's two short sides."},
        {"speak": "By the Pythagorean theorem the areas of the two child "
                  "squares always add up to the area of the parent square."},
        {"speak": "Apply the same construction to every new square, and the "
                  "squares branch out like the canopy of a tree."},
        {"speak": "Each branch is a scaled, rotated copy of the whole tree, so "
                  "the figure is self-similar, and with a forty-five degree "
                  "split it neatly fills a finite region."},
    ]
    return _frame("".join(body)), narration


# ── routing ──────────────────────────────────────────────────────────
def which_fractal(prompt: str):
    p = (prompt or "").lower()
    if "mandelbrot" in p:
        return "mandelbrot"
    if "julia" in p and "set" in p or "julia fractal" in p:
        return "julia"
    if "barnsley" in p or ("fern" in p and "fractal" in p) or \
            ("fractal" in p and "fern" in p):
        return "barnsley"
    if "cantor" in p:
        return "cantor"
    if "dragon curve" in p or ("dragon" in p and "fractal" in p) or \
            "heighway" in p:
        return "dragon"
    if "pythagoras tree" in p or "pythagorean tree" in p:
        return "pythagoras"
    if "menger" in p:
        return "menger"
    if "koch" in p or "snowflake" in p:
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
    "mandelbrot": render_mandelbrot,
    "julia": render_julia,
    "barnsley": render_barnsley_fern,
    "cantor": render_cantor,
    "dragon": render_dragon,
    "pythagoras": render_pythagoras_tree,
}


async def generate_fractal_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
):
    kind = which_fractal(prompt)
    if kind is None:
        return None
    return _RENDER[kind]()
