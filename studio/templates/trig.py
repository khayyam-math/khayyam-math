"""Deterministic trigonometry templates.

Unit-circle and triangle figures drawn by free-hand SVG come out with
points off the circle and mislabelled coordinates.  These templates
compute every point with ``math`` --- the geometry is exact.

  unit_circle(angles, show_triangle=False, title="")
  triangle(a, b, c, title="")
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

P = "π"

# Exact (cos, sin) for the 16 standard angles.
_EXACT = {
    0: ("1", "0"), 30: ("√3/2", "1/2"),
    45: ("√2/2", "√2/2"), 60: ("1/2", "√3/2"),
    90: ("0", "1"), 120: ("-1/2", "√3/2"),
    135: ("-√2/2", "√2/2"), 150: ("-√3/2", "1/2"),
    180: ("-1", "0"), 210: ("-√3/2", "-1/2"),
    225: ("-√2/2", "-√2/2"), 240: ("-1/2", "-√3/2"),
    270: ("0", "-1"), 300: ("1/2", "-√3/2"),
    315: ("√2/2", "-√2/2"), 330: ("√3/2", "-1/2"),
}
_RAD = {
    0: "0", 30: P + "/6", 45: P + "/4", 60: P + "/3", 90: P + "/2",
    120: "2" + P + "/3", 135: "3" + P + "/4", 150: "5" + P + "/6",
    180: P, 210: "7" + P + "/6", 225: "5" + P + "/4",
    240: "4" + P + "/3", 270: "3" + P + "/2", 300: "5" + P + "/3",
    315: "7" + P + "/4", 330: "11" + P + "/6",
}


def _esc(s: object) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def unit_circle(angles: Optional[List[int]] = None, *,
                show_triangle: bool = False,
                title: str = "") -> Tuple[str, List[dict]]:
    """Unit circle with the given angles (degrees) marked: a radius to
    each point, the point, its angle (degrees and radians) and its
    exact (cos, sin) coordinates.  Optionally the cos/sin right
    triangle for the first angle."""
    angs: List[int] = []
    for a in (angles if angles else [45]):
        try:
            angs.append(int(round(float(a))) % 360)
        except (TypeError, ValueError):
            continue
    angs = angs[:10] or [45]
    R = 300.0
    pad = 210.0
    top = 58.0 if title else 16.0
    W = 2 * (R + pad)
    H = W + top
    cx = R + pad
    cy = R + pad + top
    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
    ]
    if title:
        out.append(
            f'<text id="title" x="{W/2:.0f}" y="40" font-size="26" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(title)}</text>')
    # axes
    out.append(
        f'<line x1="{cx-R-50:.1f}" y1="{cy:.1f}" x2="{cx+R+50:.1f}" '
        f'y2="{cy:.1f}" stroke="#999" stroke-width="1.4"/>')
    out.append(
        f'<line x1="{cx:.1f}" y1="{cy-R-50:.1f}" x2="{cx:.1f}" '
        f'y2="{cy+R+50:.1f}" stroke="#999" stroke-width="1.4"/>')
    out.append(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R:.1f}" fill="none" '
        f'stroke="#1a3a5c" stroke-width="2.6"/>')
    narration: List[dict] = [{
        "speak": ("The unit circle has radius one, centred at the "
                  "origin."),
        "highlight": ["title"] if title else []}]
    for i, a in enumerate(angs):
        rad = math.radians(a)
        cos_v, sin_v = math.cos(rad), math.sin(rad)
        px, py = cx + R * cos_v, cy - R * sin_v
        if show_triangle and i == 0:
            out.append(
                f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{px:.1f}" '
                f'y2="{cy:.1f}" stroke="#cc4125" stroke-width="3"/>')
            out.append(
                f'<line x1="{px:.1f}" y1="{cy:.1f}" x2="{px:.1f}" '
                f'y2="{py:.1f}" stroke="#6aa84f" stroke-width="3"/>')
            out.append(
                f'<text x="{(cx+px)/2:.1f}" y="{cy+24:.1f}" '
                f'font-size="18" text-anchor="middle" '
                f'font-family="serif" fill="#cc4125">cos {chr(952)}</text>')
            out.append(
                f'<text x="{px+(14 if cos_v>=0 else -14):.1f}" '
                f'y="{(cy+py)/2:.1f}" font-size="18" '
                f'text-anchor="{"start" if cos_v>=0 else "end"}" '
                f'font-family="serif" fill="#6aa84f">sin {chr(952)}</text>')
        out.append(
            f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{px:.1f}" '
            f'y2="{py:.1f}" stroke="#1a3a5c" stroke-width="2.2"/>')
        out.append(
            f'<circle id="pt_{i}" cx="{px:.1f}" cy="{py:.1f}" r="8" '
            f'fill="#cc4125" stroke="#7a2010" stroke-width="2"/>')
        ex = _EXACT.get(a)
        cos_s, sin_s = ex if ex else (f"{cos_v:.2f}", f"{sin_v:.2f}")
        deg_lab = f"{a}°"
        if a in _RAD:
            deg_lab += " = " + _RAD[a]
        lx = cx + (R + 70) * cos_v
        ly = cy - (R + 70) * sin_v
        anchor = ("middle" if abs(cos_v) < 0.3
                  else ("start" if cos_v > 0 else "end"))
        out.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="20" '
            f'text-anchor="{anchor}" font-family="serif" '
            f'font-weight="bold" fill="#1a3a5c">{deg_lab}</text>')
        out.append(
            f'<text x="{lx:.1f}" y="{ly+24:.1f}" font-size="16" '
            f'text-anchor="{anchor}" font-family="serif" '
            f'fill="#555">({cos_s}, {sin_s})</text>')
        narration.append({
            "speak": (f"At {a} degrees, the point on the circle is "
                      f"cosine {cos_s}, sine {sin_s}."),
            "highlight": [f"pt_{i}"]})
    out.append("</svg>")
    return "".join(out), narration


def triangle(a: float, b: float, c: float,
             title: str = "") -> Tuple[str, List[dict]]:
    """A triangle with side lengths a, b, c (a opposite vertex A,
    etc.).  Sides and the three angles are computed exactly with the
    law of cosines and labelled."""
    a, b, c = float(a), float(b), float(c)
    if min(a, b, c) <= 0 or a + b <= c or b + c <= a or a + c <= b:
        raise ValueError("triangle inequality violated")
    # place A=(0,0), B=(c,0); C from the two distances
    cxC = (b * b + c * c - a * a) / (2 * c)
    cyC = math.sqrt(max(0.0, b * b - cxC * cxC))
    ax, ay, bx, by = 0.0, 0.0, c, 0.0
    A = math.degrees(math.acos(
        max(-1, min(1, (b*b + c*c - a*a) / (2*b*c)))))
    B = math.degrees(math.acos(
        max(-1, min(1, (a*a + c*c - b*b) / (2*a*c)))))
    C = 180.0 - A - B
    # scale into the canvas
    span = max(c, cxC, abs(cyC)) or 1.0
    s = 460.0 / span
    m = 130.0
    top = 56.0 if title else 30.0
    pts = {k: (m + x * s, top + m + (cyC - y) * s)
           for k, (x, y) in
           {"A": (ax, ay), "B": (bx, by), "C": (cxC, cyC)}.items()}
    W = m * 2 + max(c, cxC) * s
    H = top + m * 2 + cyC * s
    poly = " ".join(f"{pts[k][0]:.1f},{pts[k][1]:.1f}"
                    for k in ("A", "B", "C"))
    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
    ]
    if title:
        out.append(
            f'<text id="title" x="{W/2:.0f}" y="38" font-size="24" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(title)}</text>')
    out.append(
        f'<polygon points="{poly}" fill="#eef2f8" stroke="#1a3a5c" '
        f'stroke-width="2.6"/>')

    def _num(v: float) -> str:
        return str(int(round(v))) if abs(v - round(v)) < 1e-6 \
            else f"{v:.1f}"

    sides = [("a", "B", "C", a), ("b", "C", "A", b), ("c", "A", "B", c)]
    for name, p1, p2, length in sides:
        mx = (pts[p1][0] + pts[p2][0]) / 2
        my = (pts[p1][1] + pts[p2][1]) / 2
        out.append(
            f'<text x="{mx:.1f}" y="{my-10:.1f}" font-size="20" '
            f'text-anchor="middle" font-family="serif" '
            f'font-style="italic" fill="#cc4125">{name} = '
            f'{_num(length)}</text>')
    for vk, ang in (("A", A), ("B", B), ("C", C)):
        vx, vy = pts[vk]
        out.append(
            f'<text x="{vx:.1f}" y="{vy:.1f}" font-size="22" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#1a3a5c">{vk}</text>')
        out.append(
            f'<text x="{vx:.1f}" y="{vy+22:.1f}" font-size="14" '
            f'text-anchor="middle" font-family="serif" '
            f'fill="#555">{_num(ang)}°</text>')
    out.append("</svg>")
    narration = [
        {"speak": (f"This triangle has sides {_num(a)}, {_num(b)} and "
                   f"{_num(c)}."),
         "highlight": ["title"] if title else []},
        {"speak": (f"Its angles, found from the side lengths, are "
                   f"{_num(A)}, {_num(B)} and {_num(C)} degrees, which "
                   f"sum to 180."), "highlight": []}]
    return "".join(out), narration
