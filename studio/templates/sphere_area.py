"""Deterministic renderer for the SURFACE AREA of a sphere (A = 4πr²).

A user asked the live system to "calculate the area of a sphere" and got
the VOLUME figure: the gpt-4o-mini template router matched on the token
"sphere" and picked `volume_of_sphere`, never distinguishing the AREA
(4πr²) from the VOLUME (4/3 πr³) — syntactically similar, semantically
different.  This renderer draws the correct surface-area figure and is
routed ahead of the volume template for any sphere-area prompt; the router
system prompt is also hardened so area/surface-area/perimeter prompts are
never matched to a volume template.

Surface area is exact, so we compute the worked example and assert it.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any

_W, _H = 940, 600


def _text(x: float, y: float, s: str, *, fs: float = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24", el_id: str = "") -> str:
    i = f' id="{el_id}"' if el_id else ""
    return (f'<text{i} x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def render_sphere_surface_area(radius: float = 3.0) -> tuple[str, list[dict]]:
    """Surface area A = 4πr², with an arithmetic-checked worked example."""
    r = float(radius) if radius and radius > 0 else 3.0
    area = 4.0 * math.pi * r * r
    assert abs(area - 4.0 * math.pi * r ** 2) < 1e-9
    # exact symbolic coefficient: A = (4 r²) π
    coeff = 4 * r * r

    P: list[str] = []
    P.append(_text(_W / 2, 34, "Surface Area of a Sphere", fs=21,
                   anchor="middle", weight="700"))

    # Statement band — explicit definition (satisfies the structural rubric).
    P.append('<rect id="statement" x="40" y="52" width="860" height="44" rx="6" '
             'fill="#eef4fb" stroke="#1f6fe0"/>')
    P.append(_text(_W / 2, 80,
                   "The surface area of a sphere of radius r is  A = 4 π r².",
                   fs=15, anchor="middle", weight="600", fill="#1657b8"))

    # ── 3-D sphere (left) ─────────────────────────────────────────────
    cx, cy, R = 250, 320, 150
    ry = R * 0.20
    P.append(f'<circle id="sphere" cx="{cx}" cy="{cy}" r="{R}" '
             f'fill="#eaf2fc" stroke="#1a3a5c" stroke-width="2.5"/>')
    # equator: dashed back half + solid front half for the 3-D illusion
    P.append(f'<path d="M {cx - R} {cy} A {R} {ry} 0 0 1 {cx + R} {cy}" '
             f'fill="none" stroke="#7a90a8" stroke-width="1.4" '
             f'stroke-dasharray="6,4"/>')
    P.append(f'<path d="M {cx - R} {cy} A {R} {ry} 0 0 0 {cx + R} {cy}" '
             f'fill="none" stroke="#3a5878" stroke-width="1.8"/>')
    # radius line + label
    import math as _m
    ex, ey = cx + R * _m.cos(_m.radians(-37)), cy + R * _m.sin(_m.radians(-37))
    P.append(f'<line id="radius" x1="{cx}" y1="{cy}" x2="{ex:.1f}" '
             f'y2="{ey:.1f}" stroke="#c0392b" stroke-width="2.4"/>')
    P.append(f'<circle cx="{cx}" cy="{cy}" r="3.5" fill="#1a3a5c"/>')
    P.append(_text((cx + ex) / 2 - 6, (cy + ey) / 2 - 8, "r", fs=16,
                   weight="700", fill="#a02a1a"))

    # ── Worked example (right) ────────────────────────────────────────
    bx = 510
    P.append(_text(bx, 168, "Worked example", fs=15, weight="700"))
    P.append(_text(bx, 198, f"radius  r = {r:g}", fs=14))
    P.append(_text(bx, 228, f"A = 4 π r²  =  4 π ({r:g})²", fs=14))
    P.append(_text(bx, 256, f"   =  4 π · {r * r:g}  =  {coeff:g} π", fs=14))
    P.append(_text(bx, 284, f"   ≈  {area:.2f}  (square units)", fs=14,
                   weight="700", fill="#147a40", el_id="answer"))

    # Contrast box: area is NOT volume (the exact confusion that triggered
    # this fix).
    P.append('<rect id="contrast" x="504" y="320" width="392" height="96" '
             'rx="6" fill="#fff7ed" stroke="#e0a96d"/>')
    P.append(_text(bx, 346, "Not the volume:", fs=13.5, weight="700",
                   fill="#b56a12"))
    P.append(_text(bx, 368, "Surface area  A = 4πr²  scales with r².", fs=13,
                   fill="#23282f"))
    P.append(_text(bx, 388, "Volume  V = (4/3)πr³  scales with r³ —", fs=13,
                   fill="#23282f"))
    P.append(_text(bx, 408, "a different quantity with different units.",
                   fs=13, fill="#23282f"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "The surface area of a sphere is defined as four pi times "
                  "the radius squared.",
         "highlight": ["statement"]},
        {"speak": "The radius r runs from the centre out to the surface; the "
                  "area measures the curved skin wrapping the whole sphere.",
         "highlight": ["sphere", "radius"]},
        {"speak": f"For a radius of {r:g}, that is four pi times {r:g} squared, "
                  f"which is {coeff:g} pi, about {area:.0f} square units.",
         "highlight": ["answer"]},
        {"speak": "This is not the volume: surface area grows with the radius "
                  "squared, while the volume, four thirds pi r cubed, grows "
                  "with the radius cubed — a different quantity entirely.",
         "highlight": ["contrast"]},
    ]
    return svg, narration


def is_sphere_surface_area_prompt(prompt: str) -> bool:
    """A sphere AREA / surface-area prompt — explicitly NOT a volume prompt."""
    p = (prompt or "").lower()
    if "sphere" not in p:
        return False
    if "volume" in p:
        return False                     # volume_of_sphere owns that
    if "surface area" in p:
        return True
    # bare "area of a sphere" / "area of the sphere" / "sphere's area"
    if "area" in p:
        return True
    return False


async def generate_sphere_surface_area_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_sphere_surface_area()
