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


_TOPIC_SVGS = {
    "unit-circle":         _svg_unit_circle,
    "pythagorean-theorem": _svg_pythagorean,
    "quadratic-formula":   _svg_quadratic,
    "dfa-construction":    _svg_dfa,
    "matrix-inverse-3x3":  _svg_matrix_inverse,
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
