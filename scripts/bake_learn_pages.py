"""Bake every entry in service/learn/topics.yaml into a static page.

Reads service/learn/topics.yaml, generates an SVG per topic via
hand-coded generator functions (see ``_TOPIC_SVGS`` at the bottom),
renders the Jinja template at service/templates/learn_topic.html.j2,
and writes each page to service/static/learn/<slug>.html.

Also re-generates the /learn/ index at service/static/learn/index.html.

Run after editing topics.yaml.  Output is fully deterministic — every
re-run produces byte-identical HTML unless the registry or the
generator code changed.

    $ python scripts/bake_learn_pages.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape


_ROOT = Path(__file__).resolve().parent.parent
_REG = _ROOT / "service" / "learn" / "topics.yaml"
_TPL_DIR = _ROOT / "service" / "templates"
_OUT_DIR = _ROOT / "service" / "static" / "learn"


# --------------------------------------------------------------------
# Registry loading + light validation
# --------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_REQUIRED = (
    "slug branch title subtitle prompt meta_description "
    "body_what_this_shows body_applications faq related"
).split()


def load_topics() -> list[dict]:
    data = yaml.safe_load(_REG.read_text())
    if not isinstance(data, list) or not data:
        sys.exit(f"{_REG}: must be a non-empty YAML list")
    slugs: set[str] = set()
    for i, t in enumerate(data):
        for field in _REQUIRED:
            if field not in t:
                sys.exit(f"topic #{i}: missing required field '{field}'")
        if not _SLUG_RE.match(t["slug"]):
            sys.exit(f"topic #{i}: slug '{t['slug']}' must be kebab-case")
        if t["slug"] in slugs:
            sys.exit(f"topic #{i}: duplicate slug '{t['slug']}'")
        slugs.add(t["slug"])
        if not t["faq"]:
            sys.exit(f"topic '{t['slug']}': faq must be non-empty")
    # related: must reference existing slugs
    for t in data:
        for r in t["related"]:
            if r not in slugs:
                sys.exit(f"topic '{t['slug']}': related slug '{r}' not in registry")
    return data


# --------------------------------------------------------------------
# JSON-LD per page
# --------------------------------------------------------------------


def jsonld_for(topic: dict) -> str:
    """Return a JSON-LD @graph with WebPage + BreadcrumbList +
    LearningResource + FAQPage entries for one topic."""
    base = "https://khayyammath.com"
    slug = topic["slug"]
    return json.dumps(
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "WebPage",
                    "@id": f"{base}/learn/{slug}",
                    "url": f"{base}/learn/{slug}",
                    "name": topic["title"],
                    "description": " ".join(topic["meta_description"].split()),
                    "inLanguage": "en",
                    "isPartOf": {
                        "@type": "WebSite",
                        "@id": f"{base}/",
                        "name": "Khayyam Math",
                    },
                },
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                        {"@type": "ListItem", "position": 2, "name": "Learn", "item": f"{base}/learn/"},
                        {"@type": "ListItem", "position": 3, "name": topic["title"]},
                    ],
                },
                {
                    "@type": "LearningResource",
                    "name": topic["title"],
                    "description": " ".join(topic["meta_description"].split()),
                    "url": f"{base}/learn/{slug}",
                    "learningResourceType": "WorkedExample",
                    "educationalLevel": "https://schema.org/HighSchool",
                    "inLanguage": "en",
                    "isAccessibleForFree": True,
                    "about": topic["branch"],
                },
                {
                    "@type": "FAQPage",
                    "mainEntity": [
                        {
                            "@type": "Question",
                            "name": qa["q"],
                            "acceptedAnswer": {"@type": "Answer", "text": qa["a"]},
                        }
                        for qa in topic["faq"]
                    ],
                },
            ],
        },
        indent=2,
    )


# --------------------------------------------------------------------
# Per-topic SVG generators — hand-coded, deterministic, no LLM
# --------------------------------------------------------------------


def _svg_unit_circle() -> str:
    """Unit circle with cos/sin labelled at 0, 30, 45, 60, 90 degrees."""
    import math
    R = 180
    cx, cy = 250, 250
    angles = [0, 30, 45, 60, 90]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 500" aria-label="Unit circle with sin and cos at standard angles">',
        # axes
        f'<line x1="40"  y1="{cy}" x2="460" y2="{cy}" stroke="#999" stroke-width="1.2"/>',
        f'<line x1="{cx}" y1="40"  x2="{cx}" y2="460" stroke="#999" stroke-width="1.2"/>',
        f'<text x="465" y="{cy + 4}"  font-size="14" font-family="serif">x</text>',
        f'<text x="{cx - 4}" y="35" font-size="14" font-family="serif" text-anchor="end">y</text>',
        # unit circle
        f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="#2a6fd6" stroke-width="2"/>',
    ]
    colors = ["#000", "#c0392b", "#27ae60", "#8e44ad", "#000"]
    coord_labels = {
        0:  ("(1, 0)",        "1",        "0"),
        30: ("(√3/2, 1/2)",   "√3/2",     "1/2"),
        45: ("(√2/2, √2/2)",  "√2/2",     "√2/2"),
        60: ("(1/2, √3/2)",   "1/2",      "√3/2"),
        90: ("(0, 1)",        "0",        "1"),
    }
    for ang, color in zip(angles, colors):
        rad = math.radians(ang)
        px = cx + R * math.cos(rad)
        py = cy - R * math.sin(rad)
        # ray
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{px:.1f}" y2="{py:.1f}" stroke="{color}" stroke-width="1.8"/>')
        # dot at terminal point
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{color}"/>')
        # coordinate label (push outward along the ray)
        lx = cx + (R + 30) * math.cos(rad)
        ly = cy - (R + 30) * math.sin(rad)
        anchor = "middle"
        if ang == 0:
            anchor = "start"; lx += 4
        elif ang == 90:
            anchor = "middle"; ly -= 10
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="13" '
            f'font-family="serif" fill="{color}" text-anchor="{anchor}">'
            f'{ang}° {coord_labels[ang][0]}</text>'
        )
    # origin label
    parts.append(f'<text x="{cx + 6}" y="{cy + 16}" font-size="12" font-family="serif" fill="#666">O</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_pythagorean() -> str:
    """Right triangle 3-4-5 with a labelled square on each side."""
    s = 30  # one unit = 30 px
    # Triangle vertices: A (right angle) at the origin, legs along +x and +y
    Ax, Ay = 220, 290
    Bx, By = Ax + 4*s, Ay        # 4-leg horizontal (right)
    Cx, Cy = Ax,       Ay - 3*s  # 3-leg vertical (up)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 440" '
        'aria-label="Right triangle 3-4-5 with the three labelled squares">',
        # square on the 4-leg (below A--B)  area 16
        f'<rect x="{Ax}" y="{Ay}" width="{4*s}" height="{4*s}" fill="#fdecea" stroke="#c0392b" stroke-width="1.5"/>',
        f'<text x="{Ax + 2*s}" y="{Ay + 2*s + 6}" font-size="20" font-family="serif" fill="#c0392b" text-anchor="middle">16</text>',
        # square on the 3-leg (to the left of A--C)  area 9
        f'<rect x="{Ax - 3*s}" y="{Cy}" width="{3*s}" height="{3*s}" fill="#eafaf1" stroke="#27ae60" stroke-width="1.5"/>',
        f'<text x="{Ax - 1.5*s}" y="{Cy + 1.5*s + 6}" font-size="20" font-family="serif" fill="#27ae60" text-anchor="middle">9</text>',
        # square on the hypotenuse (tilted) — area 25.  Vertices found by rotating BC by -90° around B and C
    ]
    # Hypotenuse vector from B to C is (-4s, -3s), length 5s.
    # Outward perpendicular (away from A) rotates that by +90° in screen coords: (-3s, +4s)
    # We need a 5s × 5s square on segment BC sitting on the OUTSIDE of the triangle.
    # Outward normal in screen coords (y down): rotate BC by -90 -> (perp_x, perp_y) = (-(C-B).y, (C-B).x) = -(Cy-By), Cx-Bx
    perp_x = -(Cy - By)  # screen-y negation handled by reversed rotation
    perp_y = (Cx - Bx)
    # That's vector (3s, -4s) — outward (up-right).
    Dx, Dy = Bx + perp_x, By + perp_y
    Ex, Ey = Cx + perp_x, Cy + perp_y
    parts.append(
        f'<polygon points="{Bx},{By} {Cx},{Cy} {Ex},{Ey} {Dx},{Dy}" '
        f'fill="#eaf2fc" stroke="#2a6fd6" stroke-width="1.5"/>'
    )
    # Label "25" at centre of that square
    cxq = (Bx + Cx + Ex + Dx) / 4
    cyq = (By + Cy + Ey + Dy) / 4
    parts.append(
        f'<text x="{cxq:.1f}" y="{cyq + 6:.1f}" font-size="20" font-family="serif" fill="#2a6fd6" text-anchor="middle">25</text>'
    )
    # Triangle itself (drawn on top so its edges are crisp)
    parts.append(
        f'<polygon points="{Ax},{Ay} {Bx},{By} {Cx},{Cy}" '
        f'fill="none" stroke="#1a1a1a" stroke-width="2.5"/>'
    )
    # Right-angle mark at A
    parts.append(
        f'<polyline points="{Ax+12},{Ay} {Ax+12},{Ay-12} {Ax},{Ay-12}" '
        f'fill="none" stroke="#1a1a1a" stroke-width="1.5"/>'
    )
    # Side-length labels
    parts.append(f'<text x="{(Ax+Bx)/2}" y="{Ay+18}" font-size="14" font-family="serif" text-anchor="middle">b = 4</text>')
    parts.append(f'<text x="{Ax-10}" y="{(Ay+Cy)/2 + 5}" font-size="14" font-family="serif" text-anchor="end">a = 3</text>')
    # Hypotenuse label — midpoint of BC plus a small outward offset
    mxBC = (Bx + Cx) / 2 + perp_x / 8
    myBC = (By + Cy) / 2 + perp_y / 8 - 4
    parts.append(f'<text x="{mxBC:.1f}" y="{myBC:.1f}" font-size="14" font-family="serif" fill="#2a6fd6" text-anchor="middle">c = 5</text>')
    # Formula caption at the bottom
    parts.append('<text x="270" y="420" font-size="16" font-family="serif" text-anchor="middle">a² + b² = c²  →  9 + 16 = 25</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_quadratic() -> str:
    """y = x² - 5x + 6, parabola with roots at x=2 and x=3 marked."""
    # Plot range: x ∈ [-0.5, 5.5], y ∈ [-1.5, 6.5]
    # Map to viewBox 500×360 with 40 px margins
    xmin, xmax = -0.5, 5.5
    ymin, ymax = -1.5, 6.5
    W, H = 500, 360
    mL, mR, mT, mB = 50, 30, 20, 40
    plotW = W - mL - mR
    plotH = H - mT - mB

    def sx(x): return mL + (x - xmin) / (xmax - xmin) * plotW
    def sy(y): return mT + (ymax - y) / (ymax - ymin) * plotH

    # Build parabola path
    pts = []
    n = 80
    for i in range(n + 1):
        x = xmin + (xmax - xmin) * i / n
        y = x*x - 5*x + 6
        if y < ymin or y > ymax: continue
        pts.append(f"{sx(x):.1f},{sy(y):.1f}")

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 360" '
        'aria-label="Parabola y = x squared minus five x plus six, with roots at x equals 2 and x equals 3">',
        # axes
        f'<line x1="{mL}" y1="{sy(0):.1f}" x2="{W-mR}" y2="{sy(0):.1f}" stroke="#999" stroke-width="1.2"/>',
        f'<line x1="{sx(0):.1f}" y1="{mT}" x2="{sx(0):.1f}" y2="{H-mB}" stroke="#999" stroke-width="1.2"/>',
        # x-axis ticks at integers
    ]
    for ix in range(0, 6):
        parts.append(f'<line x1="{sx(ix):.1f}" y1="{sy(0)-3:.1f}" x2="{sx(ix):.1f}" y2="{sy(0)+3:.1f}" stroke="#999"/>')
        parts.append(f'<text x="{sx(ix):.1f}" y="{sy(0)+16:.1f}" font-size="11" font-family="serif" text-anchor="middle">{ix}</text>')
    # y-axis ticks
    for iy in (-1, 1, 2, 3, 4, 5, 6):
        parts.append(f'<line x1="{sx(0)-3:.1f}" y1="{sy(iy):.1f}" x2="{sx(0)+3:.1f}" y2="{sy(iy):.1f}" stroke="#999"/>')
        parts.append(f'<text x="{sx(0)-6:.1f}" y="{sy(iy)+3:.1f}" font-size="11" font-family="serif" text-anchor="end">{iy}</text>')
    # parabola
    parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#2a6fd6" stroke-width="2.2"/>')
    # mark roots
    for r in (2, 3):
        parts.append(f'<circle cx="{sx(r):.1f}" cy="{sy(0):.1f}" r="5" fill="#c0392b"/>')
        parts.append(f'<text x="{sx(r):.1f}" y="{sy(0)+30:.1f}" font-size="13" font-family="serif" fill="#c0392b" text-anchor="middle">x = {r}</text>')
    # vertex annotation
    vx = 2.5
    vy = vx*vx - 5*vx + 6
    parts.append(f'<circle cx="{sx(vx):.1f}" cy="{sy(vy):.1f}" r="3" fill="#444"/>')
    parts.append(f'<text x="{sx(vx)+8:.1f}" y="{sy(vy)+4:.1f}" font-size="11" font-family="serif" fill="#444">vertex (2.5, −0.25)</text>')
    # formula caption
    parts.append('<text x="250" y="14" font-size="14" font-family="serif" text-anchor="middle">y = x² − 5x + 6</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_dfa() -> str:
    """Three-state DFA: q0 (start), q1, q2 (accept).  Transitions on {a,b}."""
    Q0 = (90, 200)
    Q1 = (270, 200)
    Q2 = (450, 200)
    R = 30
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 360" '
        'aria-label="Three-state DFA accepting strings ending in ab">',
        '<defs>'
        '<marker id="arrow" viewBox="0 -5 10 10" refX="10" refY="0" '
        'markerWidth="6" markerHeight="6" orient="auto">'
        '<path d="M0,-5L10,0L0,5" fill="#1a1a1a"/></marker>'
        '</defs>',
        # start arrow
        f'<line x1="{Q0[0]-55}" y1="{Q0[1]}" x2="{Q0[0]-R-2}" y2="{Q0[1]}" stroke="#1a1a1a" stroke-width="1.5" marker-end="url(#arrow)"/>',
        f'<text x="{Q0[0]-58}" y="{Q0[1]-8}" font-size="11" font-family="serif" text-anchor="end">start</text>',
        # state circles
    ]
    for (cx, cy), name, accept in ((Q0, "q₀", False), (Q1, "q₁", False), (Q2, "q₂", True)):
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="white" stroke="#1a1a1a" stroke-width="2"/>')
        if accept:
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{R-5}" fill="none" stroke="#1a1a1a" stroke-width="1.5"/>')
        parts.append(f'<text x="{cx}" y="{cy+5}" font-size="18" font-family="serif" text-anchor="middle">{name}</text>')

    # Transition: q0 --a--> q1 (top of straight line)
    parts.append(f'<line x1="{Q0[0]+R+1}" y1="{Q0[1]-6}" x2="{Q1[0]-R-1}" y2="{Q1[1]-6}" stroke="#1a1a1a" stroke-width="1.5" marker-end="url(#arrow)"/>')
    parts.append(f'<text x="{(Q0[0]+Q1[0])/2}" y="{Q0[1]-12}" font-size="14" font-family="serif" text-anchor="middle">a</text>')
    # Transition: q1 --b--> q2 (top straight line)
    parts.append(f'<line x1="{Q1[0]+R+1}" y1="{Q1[1]-6}" x2="{Q2[0]-R-1}" y2="{Q1[1]-6}" stroke="#1a1a1a" stroke-width="1.5" marker-end="url(#arrow)"/>')
    parts.append(f'<text x="{(Q1[0]+Q2[0])/2}" y="{Q1[1]-12}" font-size="14" font-family="serif" text-anchor="middle">b</text>')
    # q0 self-loop on b (top)
    parts.append(f'<path d="M {Q0[0]-15} {Q0[1]-R+4} C {Q0[0]-50} {Q0[1]-R-50}, {Q0[0]+15} {Q0[1]-R-50}, {Q0[0]+15} {Q0[1]-R+4}" '
                 f'fill="none" stroke="#1a1a1a" stroke-width="1.5" marker-end="url(#arrow)"/>')
    parts.append(f'<text x="{Q0[0]}" y="{Q0[1]-R-30}" font-size="14" font-family="serif" text-anchor="middle">b</text>')
    # q1 self-loop on a (top)
    parts.append(f'<path d="M {Q1[0]-15} {Q1[1]-R+4} C {Q1[0]-50} {Q1[1]-R-50}, {Q1[0]+15} {Q1[1]-R-50}, {Q1[0]+15} {Q1[1]-R+4}" '
                 f'fill="none" stroke="#1a1a1a" stroke-width="1.5" marker-end="url(#arrow)"/>')
    parts.append(f'<text x="{Q1[0]}" y="{Q1[1]-R-30}" font-size="14" font-family="serif" text-anchor="middle">a</text>')
    # q2 --a--> q1 (bottom curve back)
    parts.append(f'<path d="M {Q2[0]-R-1} {Q2[1]+6} Q {(Q1[0]+Q2[0])/2} {Q2[1]+50}, {Q1[0]+R+1} {Q1[1]+6}" '
                 f'fill="none" stroke="#1a1a1a" stroke-width="1.5" marker-end="url(#arrow)"/>')
    parts.append(f'<text x="{(Q1[0]+Q2[0])/2}" y="{Q2[1]+62}" font-size="14" font-family="serif" text-anchor="middle">a</text>')
    # q2 --b--> q0 (bottom long curve)
    parts.append(f'<path d="M {Q2[0]} {Q2[1]+R+1} Q {(Q0[0]+Q2[0])/2} {Q2[1]+110}, {Q0[0]} {Q0[1]+R+1}" '
                 f'fill="none" stroke="#1a1a1a" stroke-width="1.5" marker-end="url(#arrow)"/>')
    parts.append(f'<text x="{(Q0[0]+Q2[0])/2}" y="{Q2[1]+122}" font-size="14" font-family="serif" text-anchor="middle">b</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_matrix_inverse() -> str:
    """Display A and A⁻¹ side by side with the determinant in the middle."""
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 280" '
        'aria-label="3x3 matrix A and its inverse, with determinant labelled">',
    ]
    def matrix(cx: float, cy: float, label: str, rows: list[list[str]], colour: str = "#1a1a1a") -> list[str]:
        out = []
        # Bracket dimensions
        col_widths = [40, 40, 40]
        row_h = 26
        w = sum(col_widths) + 20
        h = row_h * len(rows) + 16
        x0 = cx - w / 2
        y0 = cy - h / 2
        # Left bracket
        out.append(f'<path d="M {x0+6} {y0} L {x0} {y0} L {x0} {y0+h} L {x0+6} {y0+h}" fill="none" stroke="{colour}" stroke-width="2"/>')
        # Right bracket
        out.append(f'<path d="M {x0+w-6} {y0} L {x0+w} {y0} L {x0+w} {y0+h} L {x0+w-6} {y0+h}" fill="none" stroke="{colour}" stroke-width="2"/>')
        # Cells
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                tx = x0 + 10 + sum(col_widths[:c]) + col_widths[c] / 2
                ty = y0 + 8 + (r + 0.5) * row_h + 4
                out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="16" font-family="serif" fill="{colour}" text-anchor="middle">{val}</text>')
        # Label above
        out.append(f'<text x="{cx}" y="{y0-12}" font-size="18" font-family="serif" fill="{colour}" text-anchor="middle">{label}</text>')
        return out

    parts += matrix(120, 140, "A", [["2", "1", "1"], ["1", "3", "2"], ["1", "0", "0"]])
    parts.append('<text x="270" y="100" font-size="16" font-family="serif" text-anchor="middle">det A = −1</text>')
    parts.append('<text x="270" y="148" font-size="22" font-family="serif" text-anchor="middle">⟶</text>')
    parts.append('<text x="270" y="180" font-size="13" font-family="serif" fill="#666" text-anchor="middle">adjugate / det</text>')
    parts += matrix(430, 140, "A⁻¹", [["0", "0", "1"], ["−2", "1", "3"], ["3", "−1", "−5"]], colour="#2a6fd6")
    parts.append('</svg>')
    return "".join(parts)


def _svg_heron() -> str:
    """Triangle with sides 5, 6, 7 and Heron's-formula caption."""
    import math
    # Math coords: B at (0, 0), C at (7, 0), A computed.
    # x_A = 19/7, y_A = sqrt(864)/7
    xA = 19 / 7
    yA = math.sqrt(864) / 7
    scale = 30
    ox, oy = 145, 250            # screen origin = math (0, 0)
    Bx, By = ox,                oy
    Cx, Cy = ox + 7 * scale,    oy
    Ax, Ay = ox + xA * scale,   oy - yA * scale
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 580 320" '
        'aria-label="Triangle with sides 5, 6, 7 and Heron\'s formula">',
        f'<polygon points="{Ax:.1f},{Ay:.1f} {Bx},{By} {Cx},{Cy}" '
        f'fill="#eaf2fc" stroke="#1a1a1a" stroke-width="2.2"/>',
        # vertices
        f'<circle cx="{Ax:.1f}" cy="{Ay:.1f}" r="3.5" fill="#1a1a1a"/>',
        f'<circle cx="{Bx}" cy="{By}" r="3.5" fill="#1a1a1a"/>',
        f'<circle cx="{Cx}" cy="{Cy}" r="3.5" fill="#1a1a1a"/>',
        # vertex labels
        f'<text x="{Ax:.1f}" y="{Ay - 10:.1f}" font-size="14" font-family="serif" text-anchor="middle">A</text>',
        f'<text x="{Bx - 10}" y="{By + 16}" font-size="14" font-family="serif" text-anchor="end">B</text>',
        f'<text x="{Cx + 10}" y="{Cy + 16}" font-size="14" font-family="serif">C</text>',
        # side labels:  a = BC = 7 (bottom);  b = CA = 6 (right);  c = AB = 5 (left)
        f'<text x="{(Bx + Cx) / 2}" y="{By + 24}" font-size="14" font-family="serif" fill="#c0392b" text-anchor="middle">a = 7</text>',
        f'<text x="{(Cx + Ax) / 2 + 14:.1f}" y="{(Cy + Ay) / 2 + 4:.1f}" font-size="14" font-family="serif" fill="#27ae60">b = 6</text>',
        f'<text x="{(Ax + Bx) / 2 - 14:.1f}" y="{(Ay + By) / 2 + 4:.1f}" font-size="14" font-family="serif" fill="#2a6fd6" text-anchor="end">c = 5</text>',
        # title
        '<text x="250" y="22" font-size="14" font-family="serif" text-anchor="middle">Triangle with sides 5, 6, 7</text>',
        # formula block (right side, right-anchored so it stays inside the viewBox)
        '<text x="570" y="80"  font-size="13" font-family="serif" text-anchor="end">s = (5 + 6 + 7) / 2 = 9</text>',
        '<text x="570" y="105" font-size="13" font-family="serif" text-anchor="end">A = √(s(s−a)(s−b)(s−c))</text>',
        '<text x="570" y="130" font-size="13" font-family="serif" text-anchor="end">  = √(9 · 4 · 3 · 2)</text>',
        '<text x="570" y="155" font-size="13" font-family="serif" text-anchor="end">  = √216  =  6√6</text>',
        '<text x="570" y="180" font-size="13" font-family="serif" fill="#c0392b" text-anchor="end">  ≈ 14.697</text>',
        '</svg>',
    ]
    return "".join(parts)


def _svg_polynomial_long_division() -> str:
    """Long-division layout for (x^3 - 2x^2 + 4x - 8) / (x - 2).

    SVG text collapses runs of whitespace, so columns can't be aligned via
    leading spaces.  Each cell gets its own absolutely-positioned <text>.
    """
    F = ('font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, '
         'monospace" font-size="16"')
    # Column centres for each term of the dividend:  x³  −2x²  +4x  −8
    cols = {"x3": 230, "x2": 300, "x1": 370, "x0": 430}
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 290" '
        'aria-label="Polynomial long division of x cubed minus two x squared plus four x minus eight, divided by x minus two">',
        # Quotient (above the bar): x² above x³ column, +4 above x⁰ column
        f'<text x="{cols["x3"]}" y="56" {F} text-anchor="middle">x²</text>',
        f'<text x="{cols["x0"]}" y="56" {F} text-anchor="middle">+ 4</text>',
        # Horizontal bar above the dividend
        '<line x1="200" y1="64" x2="470" y2="64" stroke="#1a1a1a" stroke-width="1.6"/>',
        # Vertical bar separating divisor from dividend
        '<line x1="194" y1="64" x2="194" y2="92" stroke="#1a1a1a" stroke-width="1.6"/>',
        # Divisor on the left
        f'<text x="186" y="88" {F} text-anchor="end">x − 2</text>',
        # Dividend row
        f'<text x="{cols["x3"]}" y="88" {F} text-anchor="middle">x³</text>',
        f'<text x="{cols["x2"]}" y="88" {F} text-anchor="middle">− 2x²</text>',
        f'<text x="{cols["x1"]}" y="88" {F} text-anchor="middle">+ 4x</text>',
        f'<text x="{cols["x0"]}" y="88" {F} text-anchor="middle">− 8</text>',
        # Step 1: subtract x² · (x − 2) = x³ − 2x²
        f'<text x="{cols["x3"]}" y="116" {F} text-anchor="middle">−(x³</text>',
        f'<text x="{cols["x2"]}" y="116" {F} text-anchor="middle">− 2x²)</text>',
        f'<line x1="{cols["x3"] - 28}" y1="124" x2="{cols["x2"] + 30}" y2="124" stroke="#1a1a1a" stroke-width="1.2"/>',
        # Remainder line after step 1:  0   + 4x   − 8   (bring down)
        f'<text x="{cols["x2"]}" y="148" {F} text-anchor="middle">0</text>',
        f'<text x="{cols["x1"]}" y="148" {F} text-anchor="middle">+ 4x</text>',
        f'<text x="{cols["x0"]}" y="148" {F} text-anchor="middle">− 8</text>',
        # Step 2: subtract 4·(x − 2) = 4x − 8
        f'<text x="{cols["x1"]}" y="176" {F} text-anchor="middle">−(4x</text>',
        f'<text x="{cols["x0"]}" y="176" {F} text-anchor="middle">− 8)</text>',
        f'<line x1="{cols["x1"] - 28}" y1="184" x2="{cols["x0"] + 26}" y2="184" stroke="#1a1a1a" stroke-width="1.2"/>',
        # Final remainder
        f'<text x="{cols["x0"]}" y="208" {F} fill="#27ae60" text-anchor="middle">0</text>',
        # Conclusion caption
        '<text x="270" y="252" font-size="15" font-family="serif" text-anchor="middle">'
        'x³ − 2x² + 4x − 8 = (x − 2)(x² + 4)</text>',
        '<text x="270" y="274" font-size="13" font-family="serif" fill="#666" text-anchor="middle">'
        'remainder 0, so (x − 2) is a factor</text>',
        '</svg>',
    ]
    return "".join(parts)


def _svg_vector_projection() -> str:
    """Vectors a = (4, 3), b = (5, 0), and the projection proj_b(a) = (4, 0)."""
    scale = 40
    ox, oy = 60, 250     # screen origin = (0, 0)
    bx, by = ox + 5 * scale, oy             # tip of b
    ax, ay = ox + 4 * scale, oy - 3 * scale # tip of a
    px, py = ox + 4 * scale, oy             # tip of proj_b(a) = (4, 0)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 300" '
        'aria-label="Projection of vector a = (4, 3) onto vector b = (5, 0)">',
        '<defs>'
        '<marker id="arr" viewBox="0 -5 10 10" refX="9" refY="0" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,-5L10,0L0,5" fill="#1a1a1a"/></marker>'
        '<marker id="arrB" viewBox="0 -5 10 10" refX="9" refY="0" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,-5L10,0L0,5" fill="#27ae60"/></marker>'
        '<marker id="arrA" viewBox="0 -5 10 10" refX="9" refY="0" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,-5L10,0L0,5" fill="#2a6fd6"/></marker>'
        '<marker id="arrP" viewBox="0 -5 10 10" refX="9" refY="0" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,-5L10,0L0,5" fill="#c0392b"/></marker>'
        '</defs>',
        # Axes (light)
        f'<line x1="40" y1="{oy}" x2="470" y2="{oy}" stroke="#bbb" stroke-width="1"/>',
        f'<line x1="{ox}" y1="40" x2="{ox}" y2="280" stroke="#bbb" stroke-width="1"/>',
        # Grid ticks
    ]
    for i in range(1, 6):
        tx = ox + i * scale
        parts.append(f'<line x1="{tx}" y1="{oy - 3}" x2="{tx}" y2="{oy + 3}" stroke="#bbb"/>')
        parts.append(f'<text x="{tx}" y="{oy + 16}" font-size="11" font-family="serif" text-anchor="middle" fill="#666">{i}</text>')
    for i in range(1, 4):
        ty = oy - i * scale
        parts.append(f'<line x1="{ox - 3}" y1="{ty}" x2="{ox + 3}" y2="{ty}" stroke="#bbb"/>')
        parts.append(f'<text x="{ox - 8}" y="{ty + 4}" font-size="11" font-family="serif" text-anchor="end" fill="#666">{i}</text>')
    parts += [
        # b (green)
        f'<line x1="{ox}" y1="{oy}" x2="{bx}" y2="{by}" stroke="#27ae60" stroke-width="2.5" marker-end="url(#arrB)"/>',
        f'<text x="{bx + 8}" y="{by + 4}" font-size="14" font-family="serif" fill="#27ae60">b = (5, 0)</text>',
        # a (blue)
        f'<line x1="{ox}" y1="{oy}" x2="{ax}" y2="{ay}" stroke="#2a6fd6" stroke-width="2.5" marker-end="url(#arrA)"/>',
        f'<text x="{ax - 6}" y="{ay - 8}" font-size="14" font-family="serif" fill="#2a6fd6" text-anchor="end">a = (4, 3)</text>',
        # Perpendicular dashed drop from tip of a to projection foot
        f'<line x1="{ax}" y1="{ay}" x2="{px}" y2="{py}" stroke="#888" stroke-width="1.6" stroke-dasharray="5,4"/>',
        # Projection (red), drawn last so it sits on top of b
        f'<line x1="{ox}" y1="{oy}" x2="{px}" y2="{py}" stroke="#c0392b" stroke-width="3.2" marker-end="url(#arrP)"/>',
        f'<text x="{(ox + px) / 2}" y="{py + 30}" font-size="13" font-family="serif" fill="#c0392b" text-anchor="middle">proj_b(a) = (4, 0)</text>',
        # Origin label
        f'<text x="{ox - 12}" y="{oy + 16}" font-size="11" font-family="serif" fill="#666" text-anchor="end">O</text>',
        # Formula caption
        '<text x="250" y="22" font-size="14" font-family="serif" text-anchor="middle">'
        'proj_b(a) = (a·b / |b|²)·b  =  (20 / 25)·(5, 0)  =  (4, 0)</text>',
        '</svg>',
    ]
    return "".join(parts)


def _svg_eigenvalues_2x2() -> str:
    """A = [[2,1],[1,2]], characteristic polynomial, eigenvalues, eigenvector arrows."""
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 580 320" '
        'aria-label="Eigenvalues of the matrix [[2, 1], [1, 2]] are lambda equals 1 and lambda equals 3">',
    ]
    # Matrix A on the left
    def matrix(cx: float, cy: float, label: str, rows: list[list[str]], colour: str = "#1a1a1a") -> list[str]:
        out = []
        col_w = 34
        row_h = 26
        w = col_w * len(rows[0]) + 18
        h = row_h * len(rows) + 12
        x0 = cx - w / 2
        y0 = cy - h / 2
        out.append(f'<path d="M {x0 + 6} {y0} L {x0} {y0} L {x0} {y0 + h} L {x0 + 6} {y0 + h}" fill="none" stroke="{colour}" stroke-width="2"/>')
        out.append(f'<path d="M {x0 + w - 6} {y0} L {x0 + w} {y0} L {x0 + w} {y0 + h} L {x0 + w - 6} {y0 + h}" fill="none" stroke="{colour}" stroke-width="2"/>')
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                tx = x0 + 9 + (c + 0.5) * col_w
                ty = y0 + 6 + (r + 0.5) * row_h + 4
                out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="16" font-family="serif" fill="{colour}" text-anchor="middle">{val}</text>')
        out.append(f'<text x="{cx}" y="{y0 - 10}" font-size="17" font-family="serif" fill="{colour}" text-anchor="middle">{label}</text>')
        return out

    parts += matrix(85, 100, "A", [["2", "1"], ["1", "2"]])
    # Characteristic polynomial
    parts.append('<text x="290" y="60"  font-size="14" font-family="serif" text-anchor="middle">det(A − λI) = 0</text>')
    parts.append('<text x="290" y="86"  font-size="14" font-family="serif" text-anchor="middle">(2 − λ)(2 − λ) − 1·1 = 0</text>')
    parts.append('<text x="290" y="112" font-size="14" font-family="serif" text-anchor="middle">λ² − 4λ + 3 = 0</text>')
    parts.append('<text x="290" y="138" font-size="15" font-family="serif" fill="#c0392b" text-anchor="middle">λ = 1,  λ = 3</text>')
    # Eigenvector picture on the right.  Anchor labels with text-anchor="end"
    # so they grow leftward from the arrow tip and never overflow the viewBox.
    ox, oy = 440, 200
    s = 28
    parts.append(f'<line x1="370" y1="{oy}" x2="570" y2="{oy}" stroke="#bbb" stroke-width="1"/>')
    parts.append(f'<line x1="{ox}" y1="120" x2="{ox}" y2="290" stroke="#bbb" stroke-width="1"/>')
    parts.append('<defs><marker id="arrEV" viewBox="0 -5 10 10" refX="9" refY="0" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,-5L10,0L0,5" fill="#1a1a1a"/></marker></defs>')
    # Eigenvector for λ=3: (1, 1)
    parts.append(f'<line x1="{ox}" y1="{oy}" x2="{ox + 2 * s}" y2="{oy - 2 * s}" stroke="#2a6fd6" stroke-width="2.5" marker-end="url(#arrEV)"/>')
    parts.append(f'<text x="{ox + 2 * s + 8}" y="{oy - 2 * s - 6}" font-size="12" font-family="serif" fill="#2a6fd6">v = (1, 1)</text>')
    parts.append(f'<text x="{ox + 2 * s + 8}" y="{oy - 2 * s + 8}" font-size="12" font-family="serif" fill="#2a6fd6">λ = 3</text>')
    # Eigenvector for λ=1: (1, -1)
    parts.append(f'<line x1="{ox}" y1="{oy}" x2="{ox + 2 * s}" y2="{oy + 2 * s}" stroke="#27ae60" stroke-width="2.5" marker-end="url(#arrEV)"/>')
    parts.append(f'<text x="{ox + 2 * s + 8}" y="{oy + 2 * s - 2}" font-size="12" font-family="serif" fill="#27ae60">v = (1, −1)</text>')
    parts.append(f'<text x="{ox + 2 * s + 8}" y="{oy + 2 * s + 12}" font-size="12" font-family="serif" fill="#27ae60">λ = 1</text>')
    parts.append(f'<text x="{ox - 8}" y="{oy + 14}" font-size="11" font-family="serif" fill="#666" text-anchor="end">O</text>')
    parts.append(f'<text x="{ox}" y="135" font-size="13" font-family="serif" text-anchor="middle">eigenvectors</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_chain_rule() -> str:
    """Composite y = (3x² + 1)^5 unfolded into outer/inner with derivatives."""
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 280" '
        'aria-label="Chain rule applied to y equals (3 x squared plus 1) to the fifth power">',
        '<defs><marker id="arrCh" viewBox="0 -5 10 10" refX="9" refY="0" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,-5L10,0L0,5" fill="#1a1a1a"/></marker></defs>',
        # Three boxes:  x  →  g(x) = 3x² + 1  →  f(u) = u⁵  →  y
    ]
    boxes = [
        (40, "x", "input"),
        (180, "g(x) = 3x² + 1", "inner"),
        (370, "f(u) = u⁵", "outer"),
        (520, "y", "output"),
    ]
    bw, bh = 90, 50
    by = 70
    for x, label, kind in boxes:
        parts.append(f'<rect x="{x}" y="{by}" width="{bw}" height="{bh}" rx="6" ry="6" '
                     f'fill="#eaf2fc" stroke="#2a6fd6" stroke-width="1.6"/>')
        parts.append(f'<text x="{x + bw / 2}" y="{by + 30}" font-size="15" font-family="serif" text-anchor="middle">{label}</text>')
        parts.append(f'<text x="{x + bw / 2}" y="{by - 8}" font-size="11" font-family="serif" fill="#666" text-anchor="middle">{kind}</text>')
    # Arrows between boxes
    for i in range(3):
        x1 = boxes[i][0] + bw + 3
        x2 = boxes[i + 1][0] - 3
        parts.append(f'<line x1="{x1}" y1="{by + bh / 2}" x2="{x2}" y2="{by + bh / 2}" stroke="#1a1a1a" stroke-width="1.6" marker-end="url(#arrCh)"/>')
    # Derivative line below
    parts.append('<text x="320" y="180" font-size="15" font-family="serif" text-anchor="middle">'
                 'dy/dx = f\'(g(x)) · g\'(x) = 5·(3x² + 1)⁴ · 6x</text>')
    parts.append('<text x="320" y="218" font-size="17" font-family="serif" fill="#c0392b" text-anchor="middle">'
                 'dy/dx = 30x · (3x² + 1)⁴</text>')
    parts.append('<text x="320" y="256" font-size="12" font-family="serif" fill="#666" text-anchor="middle">'
                 'outer derivative at inner value, times inner derivative</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_integration_by_parts() -> str:
    """Tabular layout for ∫ x · e^x dx using u dv = uv - ∫ v du."""
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 320" '
        'aria-label="Integration by parts applied to integral of x times e to the x dx">',
        # Title
        '<text x="270" y="32" font-size="16" font-family="serif" text-anchor="middle">'
        '∫ x · eˣ dx     (integration by parts)</text>',
        # Choice table (two columns: u and dv ; rows: derivative / antiderivative)
        '<rect x="120" y="60" width="300" height="100" rx="6" fill="#eaf2fc" stroke="#2a6fd6" stroke-width="1.5"/>',
        '<line x1="270" y1="60" x2="270" y2="160" stroke="#2a6fd6" stroke-width="1.2"/>',
        '<line x1="120" y1="100" x2="420" y2="100" stroke="#2a6fd6" stroke-width="1.2"/>',
        # Column headers
        '<text x="195" y="86" font-size="15" font-family="serif" text-anchor="middle" fill="#2a6fd6">u = x</text>',
        '<text x="345" y="86" font-size="15" font-family="serif" text-anchor="middle" fill="#2a6fd6">dv = eˣ dx</text>',
        # Row 2
        '<text x="195" y="135" font-size="14" font-family="serif" text-anchor="middle">du = dx</text>',
        '<text x="345" y="135" font-size="14" font-family="serif" text-anchor="middle">v = eˣ</text>',
        # Formula
        '<text x="270" y="200" font-size="15" font-family="serif" text-anchor="middle">'
        '∫ u dv = u·v − ∫ v du</text>',
        '<text x="270" y="232" font-size="15" font-family="serif" text-anchor="middle">'
        '∫ x · eˣ dx = x · eˣ − ∫ eˣ dx</text>',
        '<text x="270" y="264" font-size="17" font-family="serif" fill="#c0392b" text-anchor="middle">'
        '            = x · eˣ − eˣ + C</text>',
        '<text x="270" y="298" font-size="12" font-family="serif" fill="#666" text-anchor="middle">'
        '(pick u = x because differentiating it simplifies the integrand)</text>',
        '</svg>',
    ]
    return "".join(parts)


def _svg_taylor_sin() -> str:
    """sin(x) vs Taylor polynomials of degrees 1, 3, 5 around x = 0."""
    import math
    xmin, xmax = -math.pi, math.pi
    ymin, ymax = -2.0, 2.0
    W, H = 540, 320
    mL, mR, mT, mB = 50, 30, 32, 40
    plotW = W - mL - mR
    plotH = H - mT - mB

    def sx(x: float) -> float:
        return mL + (x - xmin) / (xmax - xmin) * plotW

    def sy(y: float) -> float:
        return mT + (ymax - y) / (ymax - ymin) * plotH

    def curve(fn, n: int = 220) -> str:
        out = []
        for i in range(n + 1):
            x = xmin + (xmax - xmin) * i / n
            y = fn(x)
            if y < ymin or y > ymax:
                continue
            out.append(f"{sx(x):.1f},{sy(y):.1f}")
        return " ".join(out)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 320" '
        'aria-label="Taylor polynomials of sin x of degree 1, 3, 5 hugging the sin curve">',
        # axes
        f'<line x1="{mL}" y1="{sy(0):.1f}" x2="{W - mR}" y2="{sy(0):.1f}" stroke="#999" stroke-width="1.1"/>',
        f'<line x1="{sx(0):.1f}" y1="{mT}" x2="{sx(0):.1f}" y2="{H - mB}" stroke="#999" stroke-width="1.1"/>',
        # x-axis tick marks at -pi, -pi/2, pi/2, pi
    ]
    for x_val, label in [(-math.pi, "−π"), (-math.pi / 2, "−π/2"), (math.pi / 2, "π/2"), (math.pi, "π")]:
        parts.append(f'<line x1="{sx(x_val):.1f}" y1="{sy(0) - 3:.1f}" x2="{sx(x_val):.1f}" y2="{sy(0) + 3:.1f}" stroke="#999"/>')
        parts.append(f'<text x="{sx(x_val):.1f}" y="{sy(0) + 17:.1f}" font-size="11" font-family="serif" text-anchor="middle">{label}</text>')
    # y-ticks
    for y_val in (-1, 1):
        parts.append(f'<line x1="{sx(0) - 3:.1f}" y1="{sy(y_val):.1f}" x2="{sx(0) + 3:.1f}" y2="{sy(y_val):.1f}" stroke="#999"/>')
        parts.append(f'<text x="{sx(0) - 6:.1f}" y="{sy(y_val) + 3:.1f}" font-size="11" font-family="serif" text-anchor="end">{y_val}</text>')

    # Curves
    parts.append(f'<polyline points="{curve(lambda x: x)}" fill="none" stroke="#27ae60" stroke-width="1.6" stroke-dasharray="5,4"/>')
    parts.append(f'<polyline points="{curve(lambda x: x - x ** 3 / 6)}" fill="none" stroke="#8e44ad" stroke-width="1.6" stroke-dasharray="5,4"/>')
    parts.append(f'<polyline points="{curve(lambda x: x - x ** 3 / 6 + x ** 5 / 120)}" fill="none" stroke="#c0392b" stroke-width="1.6" stroke-dasharray="5,4"/>')
    parts.append(f'<polyline points="{curve(math.sin)}" fill="none" stroke="#2a6fd6" stroke-width="2.4"/>')

    # Legend
    legend_x = 60
    legend_y = 50
    items = [
        ("#2a6fd6", "sin x",                          False),
        ("#27ae60", "T₁(x) = x",                      True),
        ("#8e44ad", "T₃(x) = x − x³/6",               True),
        ("#c0392b", "T₅(x) = x − x³/6 + x⁵/120",      True),
    ]
    for i, (col, lbl, dashed) in enumerate(items):
        ly = legend_y + i * 18
        dash = ' stroke-dasharray="5,4"' if dashed else ''
        parts.append(f'<line x1="{legend_x}" y1="{ly}" x2="{legend_x + 22}" y2="{ly}" stroke="{col}" stroke-width="2.2"{dash}/>')
        parts.append(f'<text x="{legend_x + 28}" y="{ly + 4}" font-size="11" font-family="serif">{lbl}</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_graph_bfs_dfs() -> str:
    """Small tree graph with BFS and DFS visit numbers on each node."""
    # Layout
    nodes = {
        "A": (270, 50),
        "B": (140, 150),
        "C": (400, 150),
        "D": (60,  250),
        "E": (210, 250),
        "F": (340, 250),
        "G": (470, 250),
    }
    edges = [("A", "B"), ("A", "C"),
             ("B", "D"), ("B", "E"),
             ("C", "F"), ("C", "G")]
    # BFS order:  A B C D E F G   →  numbers 1..7
    # DFS order:  A B D E C F G
    bfs = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7}
    dfs = {"A": 1, "B": 2, "D": 3, "E": 4, "C": 5, "F": 6, "G": 7}
    R = 22
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 320" '
        'aria-label="A seven-node graph with BFS and DFS visit numbers on each node">',
    ]
    # Edges first (so they're behind circles)
    for a, b in edges:
        ax, ay = nodes[a]
        bx, by = nodes[b]
        parts.append(f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="#1a1a1a" stroke-width="1.6"/>')
    # Nodes
    for name, (x, y) in nodes.items():
        parts.append(f'<circle cx="{x}" cy="{y}" r="{R}" fill="white" stroke="#1a1a1a" stroke-width="2"/>')
        parts.append(f'<text x="{x}" y="{y + 5}" font-size="16" font-family="serif" text-anchor="middle">{name}</text>')
        # Visit-number badge to the upper-right
        parts.append(f'<text x="{x + R + 4}" y="{y - R + 4}" font-size="10" font-family="serif" fill="#2a6fd6">BFS:{bfs[name]}</text>')
        parts.append(f'<text x="{x + R + 4}" y="{y - R + 16}" font-size="10" font-family="serif" fill="#c0392b">DFS:{dfs[name]}</text>')
    # Order strings at the bottom
    parts.append('<text x="270" y="295" font-size="13" font-family="serif" fill="#2a6fd6" text-anchor="middle">'
                 'BFS order:  A → B → C → D → E → F → G</text>')
    parts.append('<text x="270" y="313" font-size="13" font-family="serif" fill="#c0392b" text-anchor="middle">'
                 'DFS order:  A → B → D → E → C → F → G</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_normal_distribution() -> str:
    """Standard normal bell curve with ±1σ, ±2σ, ±3σ bands shaded."""
    import math
    xmin, xmax = -4.0, 4.0
    ymin, ymax = 0.0, 0.45
    W, H = 540, 320
    mL, mR, mT, mB = 40, 30, 32, 50
    plotW = W - mL - mR
    plotH = H - mT - mB

    def sx(x: float) -> float:
        return mL + (x - xmin) / (xmax - xmin) * plotW

    def sy(y: float) -> float:
        return mT + (ymax - y) / (ymax - ymin) * plotH

    def pdf(x: float) -> float:
        return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)

    def band_path(lo: float, hi: float) -> str:
        # Closed polygon under the curve from lo to hi, back along the x-axis.
        pts = []
        n = 80
        for i in range(n + 1):
            x = lo + (hi - lo) * i / n
            pts.append(f"{sx(x):.1f},{sy(pdf(x)):.1f}")
        pts.append(f"{sx(hi):.1f},{sy(0):.1f}")
        pts.append(f"{sx(lo):.1f},{sy(0):.1f}")
        return " ".join(pts)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 320" '
        'aria-label="Standard normal bell curve with the 68, 95, and 99.7 percent bands shaded">',
        # Bands (lightest outermost, darkest centre)
        f'<polygon points="{band_path(-3, 3)}"   fill="#eaf2fc"/>',
        f'<polygon points="{band_path(-2, 2)}"   fill="#bcd6f5"/>',
        f'<polygon points="{band_path(-1, 1)}"   fill="#7eaff0"/>',
        # x-axis
        f'<line x1="{mL}" y1="{sy(0):.1f}" x2="{W - mR}" y2="{sy(0):.1f}" stroke="#999" stroke-width="1.1"/>',
    ]
    # x ticks at integers
    for ix in range(-4, 5):
        parts.append(f'<line x1="{sx(ix):.1f}" y1="{sy(0) - 3:.1f}" x2="{sx(ix):.1f}" y2="{sy(0) + 3:.1f}" stroke="#999"/>')
        label = "μ" if ix == 0 else f"{ix:+d}σ"
        parts.append(f'<text x="{sx(ix):.1f}" y="{sy(0) + 16:.1f}" font-size="11" font-family="serif" text-anchor="middle">{label}</text>')
    # Curve on top
    curve_pts = []
    n = 160
    for i in range(n + 1):
        x = xmin + (xmax - xmin) * i / n
        curve_pts.append(f"{sx(x):.1f},{sy(pdf(x)):.1f}")
    parts.append(f'<polyline points="{" ".join(curve_pts)}" fill="none" stroke="#2a6fd6" stroke-width="2.2"/>')
    # Band labels (percentages)
    parts.append(f'<text x="{sx(0):.1f}" y="{sy(0.18):.1f}" font-size="13" font-family="serif" text-anchor="middle" fill="#1a1a1a">68%</text>')
    parts.append(f'<text x="{sx(1.5):.1f}" y="{sy(0.06):.1f}" font-size="11" font-family="serif" text-anchor="middle" fill="#1a1a1a">95% within ±2σ</text>')
    parts.append(f'<text x="{sx(2.7):.1f}" y="{sy(0.015):.1f}" font-size="10" font-family="serif" text-anchor="middle" fill="#444">99.7% within ±3σ</text>')
    # Title
    parts.append('<text x="270" y="22" font-size="14" font-family="serif" text-anchor="middle">'
                 'Normal distribution — the 68 / 95 / 99.7 rule</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_binomial_pmf() -> str:
    """Bar chart of P(X = k) for X ~ Binomial(n = 10, p = 0.5)."""
    # Pre-computed probabilities (out of 1024)
    counts = [1, 10, 45, 120, 210, 252, 210, 120, 45, 10, 1]
    probs = [c / 1024 for c in counts]
    W, H = 540, 320
    mL, mR, mT, mB = 50, 20, 36, 50
    plotW = W - mL - mR
    plotH = H - mT - mB
    n = len(probs)
    bar_w = plotW / n * 0.7
    gap = plotW / n - bar_w
    max_p = max(probs)

    def by(p: float) -> float:
        return mT + plotH - p / max_p * plotH

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 320" '
        'aria-label="Bar chart of the binomial probability mass function for n equals 10 and p equals one half">',
        # x-axis
        f'<line x1="{mL}" y1="{mT + plotH}" x2="{W - mR}" y2="{mT + plotH}" stroke="#999" stroke-width="1.1"/>',
        # y-axis
        f'<line x1="{mL}" y1="{mT}" x2="{mL}" y2="{mT + plotH}" stroke="#999" stroke-width="1.1"/>',
        # Title
        '<text x="270" y="22" font-size="14" font-family="serif" text-anchor="middle">'
        'Binomial(n = 10, p = 0.5) — P(X = k) = C(10, k) · 0.5¹⁰</text>',
    ]
    for k, p in enumerate(probs):
        x = mL + gap / 2 + k * (bar_w + gap)
        y = by(p)
        h = mT + plotH - y
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                     f'fill="#2a6fd6" stroke="#1a4f99" stroke-width="0.6"/>')
        # k label below
        parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{mT + plotH + 16:.1f}" font-size="11" font-family="serif" text-anchor="middle">{k}</text>')
        # probability above the bar (only the four tallest for readability)
        if p >= 0.10:
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 4:.1f}" font-size="10" font-family="serif" fill="#1a4f99" text-anchor="middle">{p:.3f}</text>')
    # y-axis tick labels at 0.1, 0.2
    for y_val in (0.1, 0.2):
        ty = by(y_val)
        parts.append(f'<line x1="{mL - 3}" y1="{ty:.1f}" x2="{mL + 3}" y2="{ty:.1f}" stroke="#999"/>')
        parts.append(f'<text x="{mL - 6}" y="{ty + 4:.1f}" font-size="11" font-family="serif" text-anchor="end">{y_val:.1f}</text>')
    # x-axis label
    parts.append(f'<text x="{mL + plotW / 2:.1f}" y="{mT + plotH + 36:.1f}" font-size="12" font-family="serif" text-anchor="middle">k (number of successes)</text>')
    parts.append('</svg>')
    return "".join(parts)


def _svg_khayyam_cubic() -> str:
    """Khayyam's solution of x³ + 3x = 14 — parabola y = x²/√3 ∩ circle through O."""
    import math
    sqrt3 = math.sqrt(3)
    b_param = 3.0
    c_param = 14.0
    cx_math = c_param / (2 * b_param)   # circle centre x = 7/3
    r_math  = c_param / (2 * b_param)   # circle radius   = 7/3
    # Real positive root x = 2  (verify: 8 + 6 = 14)
    root_x = 2.0
    root_y = root_x ** 2 / sqrt3        # ≈ 2.309

    # Equal-aspect axes (same math range on x and y) so the circle in
    # Khayyam's construction actually renders as a circle, not an ellipse.
    xmin, xmax = -1.0, 5.5
    ymin, ymax = -3.25, 3.25
    W, H = 540, 532
    mL, mR, mT, mB = 50, 30, 32, 40
    plotW = W - mL - mR
    plotH = H - mT - mB

    def sx(x: float) -> float:
        return mL + (x - xmin) / (xmax - xmin) * plotW

    def sy(y: float) -> float:
        return mT + (ymax - y) / (ymax - ymin) * plotH

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        'aria-label="Omar Khayyam\'s geometric solution to x cubed plus three x equals fourteen">',
        # axes
        f'<line x1="{mL}" y1="{sy(0):.1f}" x2="{W - mR}" y2="{sy(0):.1f}" stroke="#999" stroke-width="1.1"/>',
        f'<line x1="{sx(0):.1f}" y1="{mT}" x2="{sx(0):.1f}" y2="{H - mB}" stroke="#999" stroke-width="1.1"/>',
    ]
    # axis ticks at integers
    for ix in range(0, 6):
        parts.append(f'<line x1="{sx(ix):.1f}" y1="{sy(0) - 3:.1f}" x2="{sx(ix):.1f}" y2="{sy(0) + 3:.1f}" stroke="#999"/>')
        parts.append(f'<text x="{sx(ix):.1f}" y="{sy(0) + 16:.1f}" font-size="10" font-family="serif" text-anchor="middle">{ix}</text>')
    for iy in (-3, -2, -1, 1, 2, 3):
        parts.append(f'<line x1="{sx(0) - 3:.1f}" y1="{sy(iy):.1f}" x2="{sx(0) + 3:.1f}" y2="{sy(iy):.1f}" stroke="#999"/>')
        parts.append(f'<text x="{sx(0) - 6:.1f}" y="{sy(iy) + 3:.1f}" font-size="10" font-family="serif" text-anchor="end">{iy}</text>')
    # Parabola y = x² / √3   (extend a bit beyond intersection y)
    pts = []
    nseg = 90
    x_lo, x_hi = -2.6, 2.5
    for i in range(nseg + 1):
        x = x_lo + (x_hi - x_lo) * i / nseg
        y = x * x / sqrt3
        if y < ymin or y > ymax:
            continue
        pts.append(f"{sx(x):.1f},{sy(y):.1f}")
    parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#2a6fd6" stroke-width="2.2"/>')
    # Circle (x - cx)² + y² = r²
    cx_screen = sx(cx_math)
    cy_screen = sy(0)
    r_screen_x = sx(r_math) - sx(0)        # converts a math-x-length to screen-x-length
    # Aspect: math y-range is 7 units (−3.5..3.5), math x-range is 6.5 units.  Plot scales differ slightly.
    # Compute screen Y radius from math radius.
    r_screen_y = sy(0) - sy(r_math)
    parts.append(f'<ellipse cx="{cx_screen:.1f}" cy="{cy_screen:.1f}" rx="{r_screen_x:.1f}" ry="{r_screen_y:.1f}" '
                 f'fill="none" stroke="#27ae60" stroke-width="2.2"/>')
    # Mark intersection at (2, 4/√3) and origin (the trivial intersection)
    parts.append(f'<circle cx="{sx(0):.1f}" cy="{sy(0):.1f}" r="3.5" fill="#1a1a1a"/>')
    parts.append(f'<circle cx="{sx(root_x):.1f}" cy="{sy(root_y):.1f}" r="5" fill="#c0392b"/>')
    # Drop dashed line from intersection to x-axis to highlight the root
    parts.append(f'<line x1="{sx(root_x):.1f}" y1="{sy(root_y):.1f}" x2="{sx(root_x):.1f}" y2="{sy(0):.1f}" '
                 f'stroke="#c0392b" stroke-width="1.5" stroke-dasharray="5,4"/>')
    parts.append(f'<text x="{sx(root_x):.1f}" y="{sy(0) + 30:.1f}" font-size="13" font-family="serif" fill="#c0392b" text-anchor="middle">'
                 'x = 2  (root)</text>')
    # Curve labels
    parts.append(f'<text x="{sx(-1.7):.1f}" y="{sy(1.8):.1f}" font-size="12" font-family="serif" fill="#2a6fd6">parabola: y = x²/√3</text>')
    parts.append(f'<text x="{sx(3.4):.1f}" y="{sy(-2.7):.1f}" font-size="12" font-family="serif" fill="#27ae60">circle: x² + y² = (14/3)·x</text>')
    # Title caption
    parts.append('<text x="270" y="22" font-size="13" font-family="serif" text-anchor="middle">'
                 'Khayyam: the non-trivial intersection x-coordinate solves x³ + 3x = 14</text>',)
    parts.append('</svg>')
    return "".join(parts)


_TOPIC_SVGS = {
    "unit-circle":                    _svg_unit_circle,
    "pythagorean-theorem":            _svg_pythagorean,
    "quadratic-formula":              _svg_quadratic,
    "dfa-construction":               _svg_dfa,
    "matrix-inverse-3x3":             _svg_matrix_inverse,
    "triangle-area-heron":            _svg_heron,
    "polynomial-long-division":       _svg_polynomial_long_division,
    "vector-projection":              _svg_vector_projection,
    "eigenvalues-2x2":                _svg_eigenvalues_2x2,
    "derivative-chain-rule":          _svg_chain_rule,
    "integration-by-parts":           _svg_integration_by_parts,
    "taylor-series-sin-x":            _svg_taylor_sin,
    "graph-bfs-vs-dfs":               _svg_graph_bfs_dfs,
    "normal-distribution-68-95-99":   _svg_normal_distribution,
    "binomial-pmf":                   _svg_binomial_pmf,
    "omar-khayyam-cubic-roots":       _svg_khayyam_cubic,
}


# --------------------------------------------------------------------
# Body paragraph splitting (keep monospaced blocks separate)
# --------------------------------------------------------------------


def split_paragraphs(body: str) -> list[str]:
    """Split a YAML block-text body into paragraphs on blank lines,
    preserving indented monospace blocks (lines starting with spaces)
    as separate items so the template can render them inside <pre>."""
    paras: list[str] = []
    buf: list[str] = []
    in_pre = False
    for line in body.splitlines():
        if line.strip() == "":
            if buf:
                paras.append("\n".join(buf))
                buf = []
            in_pre = False
            continue
        leading = len(line) - len(line.lstrip(" "))
        is_indented = leading >= 4
        if is_indented and not in_pre and buf:
            paras.append("\n".join(buf))
            buf = []
        if is_indented:
            in_pre = True
        elif in_pre:
            paras.append("\n".join(buf))
            buf = []
            in_pre = False
        buf.append(line if in_pre else line.strip())
    if buf:
        paras.append("\n".join(buf))
    return paras


# --------------------------------------------------------------------
# Index page (list of all topics, grouped by branch)
# --------------------------------------------------------------------


_INDEX_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Learn — Khayyam Math</title>
<meta name="description" content="Worked examples from the Khayyam Math live diagram tutor — grouped by branch.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://khayyammath.com/learn/">
<link rel="manifest" href="/manifest.json">
<meta property="og:type" content="website">
<meta property="og:title" content="Learn — Khayyam Math">
<meta property="og:description" content="Worked examples from the Khayyam Math live diagram tutor — grouped by branch.">
<meta property="og:url" content="https://khayyammath.com/learn/">
<meta property="og:image" content="https://khayyammath.com/screenshots/social_preview.png">
<style>
  :root {{ --bg: #fafafa; --fg: #1a1a1a; --muted: #555; --border: #e0e0e0; --accent: #2a6fd6; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #1a1a1a; --fg: #f0f0f0; --muted: #aaa; --border: #2a2a2a; --accent: #79a9ff; }}
  }}
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, sans-serif;
          background: var(--bg); color: var(--fg); line-height: 1.55; }}
  header {{ padding: 1.5em 2em; border-bottom: 1px solid var(--border); }}
  header a.brand {{ font-weight: 700; color: var(--fg); text-decoration: none; }}
  main {{ max-width: 760px; margin: 0 auto; padding: 2em; }}
  h1 {{ font-size: 2em; }}
  h2 {{ font-size: 1.3em; margin-top: 2em; padding-bottom: 0.3em; border-bottom: 1px solid var(--border); text-transform: capitalize; }}
  ul {{ padding-left: 1.2em; }}
  li {{ margin: 0.5em 0; }}
  li a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
  li a:hover {{ text-decoration: underline; }}
  li .subtitle {{ display: block; color: var(--muted); font-weight: 400; font-size: 0.92em; margin-top: 0.15em; }}
  footer {{ border-top: 1px solid var(--border); padding: 1.5em 2em; color: var(--muted); font-size: 0.9em; text-align: center; }}
  footer a {{ color: var(--muted); }}
</style>
</head>
<body>
<header><a class="brand" href="/">Khayyam Math</a></header>
<main>
<h1>Topics</h1>
<p>Worked examples — one figure, ~500 words, FAQs.  Pick a branch.</p>
{groups}
</main>
<footer>
  <a href="/">Khayyam Math</a> &middot;
  <a href="https://github.com/khayyam-math/khayyam-math">GitHub</a>
</footer>
</body>
</html>
"""


def render_index(topics: list[dict]) -> str:
    by_branch: dict[str, list[dict]] = {}
    for t in topics:
        by_branch.setdefault(t["branch"], []).append(t)
    groups = []
    for branch in sorted(by_branch):
        items = "\n".join(
            f'  <li><a href="/learn/{t["slug"]}">{t["title"]}</a>'
            f'<span class="subtitle">{t["subtitle"]}</span></li>'
            for t in by_branch[branch]
        )
        groups.append(f'<h2>{branch.replace("-", " ")}</h2>\n<ul>\n{items}\n</ul>')
    return _INDEX_TEMPLATE.format(groups="\n".join(groups))


# --------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------


def main() -> None:
    topics = load_topics()
    print(f"loaded {len(topics)} topics from {_REG.relative_to(_ROOT)}")

    # Every topic must have an SVG generator
    missing = [t["slug"] for t in topics if t["slug"] not in _TOPIC_SVGS]
    if missing:
        sys.exit(f"no SVG generator for: {', '.join(missing)}")

    env = Environment(
        loader=FileSystemLoader(str(_TPL_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tpl = env.get_template("learn_topic.html.j2")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug_to_topic = {t["slug"]: t for t in topics}

    for t in topics:
        # Pre-split body text into paragraph items so the template can
        # render monospace blocks separately from prose paragraphs.
        t["body_what_this_shows_paragraphs"] = split_paragraphs(t["body_what_this_shows"])
        t["body_applications_paragraphs"]    = split_paragraphs(t["body_applications"])
        svg = _TOPIC_SVGS[t["slug"]]()
        html = tpl.render(
            topic=t,
            topic_svg=svg,
            jsonld_blocks=jsonld_for(t),
            related_topics=[slug_to_topic[s] for s in t["related"]],
        )
        out_path = _OUT_DIR / f"{t['slug']}.html"
        out_path.write_text(html)
        print(f"  ✓ {out_path.relative_to(_ROOT)}  ({len(html)} B)")

    # Index page
    index_path = _OUT_DIR / "index.html"
    index_path.write_text(render_index(topics))
    print(f"  ✓ {index_path.relative_to(_ROOT)}  ({len(index_path.read_text())} B)")

    print(f"done — {len(topics)} topic pages + 1 index page")


if __name__ == "__main__":
    main()
