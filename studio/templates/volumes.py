"""Deterministic templates for proving volumes of revolution by the
disk method.

Replaces the general LLM-SVG path for prompts like:

    "Prove the formula for the volume of a sphere"
    "Derive V = (4/3) π r^3"
    "Show why a cone has volume one third base times height"

The LLM-drawn version of these consistently overlapped the integral
text on top of the figure, drew the sphere as a flat 2D circle with
no slice visible, and rendered the cone as a generic triangle.  The
templates here:

  * draw the solid in side view as a clean shape with an ellipse
    equator/base for 3D illusion,
  * place ONE representative horizontal disk slice (drawn as an
    ellipse so it reads as a disk seen edge-on),
  * draw the right triangle inside the solid that explains where
    the disk's radius comes from (Pythagoras for sphere, similar
    triangles for cone),
  * place the integral derivation as a tidy right-side text block
    that does not overlap the figure.

Public API:

    volume_of_sphere(radius=1.0, title="")
    volume_of_cone(radius=1.0, height=2.0, title="")
"""
from __future__ import annotations

import math
from typing import List, Tuple


def _esc(s: object) -> str:
    return (str(s).replace("&", "&amp;")
                  .replace("<", "&lt;")
                  .replace(">", "&gt;"))


def _emit_lines(
    out: List[str],
    rx: float,
    y_top: float,
    lines: List[Tuple[str, str, int, bool]],
    line_step: int = 26,
    gap_step: int = 10,
) -> None:
    """Emit a vertically-stacked block of right-side text.  Each line
    is (text, fill_color, font_size, bold).  Empty text creates a gap.
    """
    y = y_top
    for text, color, size, bold in lines:
        if not text:
            y += gap_step
            continue
        weight = ' font-weight="bold"' if bold else ''
        out.append(
            f'<text x="{rx:.1f}" y="{y:.1f}" font-size="{size}" '
            f'font-family="serif" fill="{color}"{weight}>'
            f'{_esc(text)}</text>'
        )
        y += line_step


def volume_of_sphere(radius: float = 1.0, title: str = "") -> Tuple[str, List[dict]]:
    """Prove V = (4/3) π r³ via the disk method.

    Draws a side-view sphere, an equator ellipse for 3D illusion, a
    horizontal disk slice at height y, and the right triangle
    (center → top-of-y → disk edge) that gives the disk radius
    √(r² − y²) by Pythagoras.  The integral derivation is on the
    right side as a tidy block.  Coordinates and labels are exact.
    """
    if radius <= 0:
        raise ValueError("radius must be positive")

    # ── figure layout ──────────────────────────────────────────────
    W, H = 980.0, 640.0
    title_h = 56.0 if title else 24.0
    # The sphere occupies the LEFT third; derivation lives on the
    # RIGHT half.  Pixel radius is fixed; physical radius is just a
    # label.
    R_pix = 200.0
    cx, cy = 290.0, 330.0
    perspective_ry = R_pix * 0.18

    # Disk slice at math-height y_frac * r above the centre.  0.55
    # gives a visually balanced position: above the equator, well
    # below the top, with a disk radius about 83% of r.
    y_frac = 0.55
    y_pix = R_pix * y_frac
    disk_r_pix = R_pix * math.sqrt(1.0 - y_frac * y_frac)
    disk_perspective_ry = disk_r_pix * 0.18
    disk_cy = cy - y_pix

    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
    ]
    if title:
        out.append(
            f'<text id="title" x="{W/2:.0f}" y="{title_h-14:.0f}" '
            f'font-size="24" text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(title)}</text>'
        )

    # ── sphere outline ─────────────────────────────────────────────
    out.append(
        f'<circle id="sphere" cx="{cx:.1f}" cy="{cy:.1f}" '
        f'r="{R_pix:.1f}" fill="#f4f8ff" stroke="#1a3a5c" '
        f'stroke-width="2.5"/>'
    )

    # ── equator: dashed back half + solid front half ──────────────
    # Back (further from viewer) is the upper half of the ellipse;
    # front (closer) is the lower half.
    out.append(
        f'<path id="equator_back" '
        f'd="M {cx-R_pix:.1f} {cy:.1f} '
        f'A {R_pix:.1f} {perspective_ry:.1f} 0 0 1 '
        f'{cx+R_pix:.1f} {cy:.1f}" '
        f'fill="none" stroke="#7a90a8" stroke-width="1.4" '
        f'stroke-dasharray="6,4"/>'
    )
    out.append(
        f'<path id="equator_front" '
        f'd="M {cx-R_pix:.1f} {cy:.1f} '
        f'A {R_pix:.1f} {perspective_ry:.1f} 0 0 0 '
        f'{cx+R_pix:.1f} {cy:.1f}" '
        f'fill="none" stroke="#3a5878" stroke-width="1.8"/>'
    )

    # ── y-axis (vertical reference line through the sphere) ───────
    out.append(
        f'<line id="y_axis" x1="{cx:.1f}" y1="{cy + R_pix + 36:.1f}" '
        f'x2="{cx:.1f}" y2="{cy - R_pix - 36:.1f}" '
        f'stroke="#aaa" stroke-width="1.0" stroke-dasharray="4,3"/>'
    )
    out.append(
        f'<text x="{cx+10:.1f}" y="{cy - R_pix - 36:.1f}" '
        f'font-size="14" font-family="serif" fill="#666">y</text>'
    )
    out.append(
        f'<text x="{cx+10:.1f}" y="{cy + R_pix + 48:.1f}" '
        f'font-size="14" font-family="serif" fill="#666">−y</text>'
    )

    # ── disk slice (ellipse) ──────────────────────────────────────
    # Back half dashed, front half solid for 3D illusion, then a
    # transparent fill across the full ellipse.
    out.append(
        f'<ellipse id="disk" cx="{cx:.1f}" cy="{disk_cy:.1f}" '
        f'rx="{disk_r_pix:.1f}" ry="{disk_perspective_ry:.1f}" '
        f'fill="#c0392b" fill-opacity="0.16" stroke="none"/>'
    )
    out.append(
        f'<path d="M {cx-disk_r_pix:.1f} {disk_cy:.1f} '
        f'A {disk_r_pix:.1f} {disk_perspective_ry:.1f} 0 0 1 '
        f'{cx+disk_r_pix:.1f} {disk_cy:.1f}" '
        f'fill="none" stroke="#c0392b" stroke-width="1.4" '
        f'stroke-dasharray="5,3"/>'
    )
    out.append(
        f'<path d="M {cx-disk_r_pix:.1f} {disk_cy:.1f} '
        f'A {disk_r_pix:.1f} {disk_perspective_ry:.1f} 0 0 0 '
        f'{cx+disk_r_pix:.1f} {disk_cy:.1f}" '
        f'fill="none" stroke="#c0392b" stroke-width="2.2"/>'
    )

    # ── Pythagorean right triangle inside the sphere ──────────────
    # vertical leg from center (cx, cy) up to (cx, disk_cy): length y
    out.append(
        f'<line id="leg_y" x1="{cx:.1f}" y1="{cy:.1f}" '
        f'x2="{cx:.1f}" y2="{disk_cy:.1f}" '
        f'stroke="#1f6b1f" stroke-width="2.6"/>'
    )
    # horizontal leg from (cx, disk_cy) to (cx + disk_r_pix, disk_cy): length √(r²-y²)
    out.append(
        f'<line id="leg_disk_radius" x1="{cx:.1f}" y1="{disk_cy:.1f}" '
        f'x2="{cx+disk_r_pix:.1f}" y2="{disk_cy:.1f}" '
        f'stroke="#c0392b" stroke-width="2.6"/>'
    )
    # hypotenuse: from sphere centre to disk-edge point, length r
    out.append(
        f'<line id="hyp_r" x1="{cx:.1f}" y1="{cy:.1f}" '
        f'x2="{cx+disk_r_pix:.1f}" y2="{disk_cy:.1f}" '
        f'stroke="#1a3a5c" stroke-width="2.6"/>'
    )
    # right-angle marker at the corner where the two legs meet
    rmark = 12.0
    out.append(
        f'<polyline points="{cx+rmark:.1f},{disk_cy:.1f} '
        f'{cx+rmark:.1f},{disk_cy+rmark:.1f} '
        f'{cx:.1f},{disk_cy+rmark:.1f}" '
        f'fill="none" stroke="#333" stroke-width="1.5"/>'
    )

    # ── triangle labels ──────────────────────────────────────────
    # y on the vertical leg (mid-point, left side)
    out.append(
        f'<text x="{cx-14:.1f}" y="{(cy+disk_cy)/2 + 5:.1f}" '
        f'font-size="20" font-family="serif" text-anchor="end" '
        f'fill="#1f6b1f" font-weight="bold">y</text>'
    )
    # √(r²−y²) on the horizontal leg (slightly above)
    out.append(
        f'<text x="{cx + disk_r_pix/2:.1f}" y="{disk_cy - 12:.1f}" '
        f'font-size="16" font-family="serif" text-anchor="middle" '
        f'fill="#c0392b" font-weight="bold">'
        f'√(r² − y²)</text>'
    )
    # r on the hypotenuse, offset to the upper-right
    mid_hx = (cx + cx + disk_r_pix) / 2
    mid_hy = (cy + disk_cy) / 2
    out.append(
        f'<text x="{mid_hx + 14:.1f}" y="{mid_hy - 4:.1f}" '
        f'font-size="20" font-family="serif" '
        f'fill="#1a3a5c" font-weight="bold">r</text>'
    )
    # central dot
    out.append(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="#1a3a5c"/>'
    )
    # disk-edge dot
    out.append(
        f'<circle cx="{cx+disk_r_pix:.1f}" cy="{disk_cy:.1f}" '
        f'r="4" fill="#c0392b"/>'
    )

    # thickness annotation: a tiny "dy" tucked just above the disk's
    # right edge so it doesn't reach the derivation panel.
    out.append(
        f'<text x="{cx + disk_r_pix + 6:.1f}" y="{disk_cy + 4:.1f}" '
        f'font-size="15" font-family="serif" fill="#c0392b" '
        f'font-style="italic">dy</text>'
    )

    # ── right-side derivation ──────────────────────────────────────
    rx = 580.0
    lines: List[Tuple[str, str, int, bool]] = [
        ("Disk method", "#111", 19, True),
        ("(slice the sphere into thin", "#444", 14, False),
        ("horizontal disks of thickness dy):", "#444", 14, False),
        ("", "", 0, False),
        ("A disk at height y has radius", "#222", 15, False),
        ("    √(r² − y²)   (Pythagoras)", "#c0392b", 16, True),
        ("and thickness dy.  Its volume:", "#222", 15, False),
        ("    dV = π · (r² − y²) · dy", "#c0392b", 17, True),
        ("", "", 0, False),
        ("Sum over the whole sphere,", "#222", 15, False),
        ("from y = −r to y = +r :", "#222", 15, False),
        ("    V  =  π ∫₋ᵣ^r (r² − y²) dy", "#222", 17, False),
        ("        =  π [ r²·y − y³/3 ]₋ᵣ^r", "#222", 15, False),
        ("        =  π · ( 2r³ − 2r³/3 )", "#222", 15, False),
        ("        =  π · 4r³/3", "#222", 16, False),
        ("", "", 0, False),
        ("Therefore", "#222", 15, False),
        ("    V  =  (4/3) π r³", "#1f6b1f", 24, True),
    ]
    _emit_lines(out, rx, title_h + 24, lines, line_step=24, gap_step=10)

    out.append('</svg>')
    svg = "\n".join(out)

    # ── narration ─────────────────────────────────────────────────
    narration: List[dict] = [
        {"speak": ("To prove the volume formula for a sphere of "
                   "radius r, we slice the sphere into thin "
                   "horizontal disks and add them up with an "
                   "integral."),
         "highlight": ["title", "sphere"]},
        {"speak": ("Look at one disk, at height y above the centre. "
                   "Inside the sphere, the line from the centre to "
                   "the disk's edge is a radius of length r. That "
                   "radius, the height y, and the disk's own radius "
                   "form a right triangle."),
         "highlight": ["disk", "hyp_r", "leg_y", "leg_disk_radius"]},
        {"speak": ("By Pythagoras, the disk's radius is the square "
                   "root of r squared minus y squared. So the disk "
                   "has area pi times r squared minus y squared, "
                   "and volume pi times r squared minus y squared "
                   "times dy."),
         "highlight": ["leg_disk_radius"]},
        {"speak": ("Integrate from y equals minus r to y equals "
                   "plus r. The integral of r squared minus y "
                   "squared works out to four r cubed over three, "
                   "so the volume is four thirds pi r cubed."),
         "highlight": ["sphere"]},
    ]
    return svg, narration


def volume_of_cone(
    radius: float = 1.0,
    height: float = 2.0,
    title: str = "",
) -> Tuple[str, List[dict]]:
    """Prove V = (1/3) π r² h for a right circular cone via disks.

    Side-view drawing: cone with elliptical base, a representative
    horizontal disk slice partway up, and the similar-triangles
    relation that gives the disk's radius x = r·(1 − y/h) (measuring
    y from the cone's apex).  Integral derivation on the right.
    """
    if radius <= 0 or height <= 0:
        raise ValueError("radius and height must both be positive")

    W, H = 980.0, 640.0
    title_h = 56.0 if title else 24.0

    # The cone's apex is at the top; its circular base sits at the
    # bottom drawn as a perspective ellipse.
    apex_x, apex_y = 270.0, 130.0
    base_cx, base_cy = apex_x, 530.0
    base_rx = 200.0  # screen-x radius of the base
    base_ry = base_rx * 0.20  # perspective squish
    cone_height = base_cy - apex_y

    # Representative disk at fraction f down from the apex (0 = apex,
    # 1 = base).  Pick 0.45 so the slice sits visibly above the base.
    f = 0.45
    disk_cy = apex_y + f * cone_height
    disk_rx = base_rx * f  # similar triangles
    disk_ry = disk_rx * 0.20

    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
    ]
    if title:
        out.append(
            f'<text id="title" x="{W/2:.0f}" y="{title_h-14:.0f}" '
            f'font-size="24" text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(title)}</text>'
        )

    # ── base ellipse (back half dashed) ──────────────────────────
    out.append(
        f'<path d="M {base_cx-base_rx:.1f} {base_cy:.1f} '
        f'A {base_rx:.1f} {base_ry:.1f} 0 0 1 '
        f'{base_cx+base_rx:.1f} {base_cy:.1f}" '
        f'fill="none" stroke="#7a90a8" stroke-width="1.4" '
        f'stroke-dasharray="6,4"/>'
    )
    out.append(
        f'<path d="M {base_cx-base_rx:.1f} {base_cy:.1f} '
        f'A {base_rx:.1f} {base_ry:.1f} 0 0 0 '
        f'{base_cx+base_rx:.1f} {base_cy:.1f}" '
        f'fill="none" stroke="#1a3a5c" stroke-width="2.4"/>'
    )

    # cone sides
    out.append(
        f'<polygon id="cone" points="'
        f'{apex_x:.1f},{apex_y:.1f} '
        f'{base_cx-base_rx:.1f},{base_cy:.1f} '
        f'{base_cx+base_rx:.1f},{base_cy:.1f}" '
        f'fill="#f4f8ff" fill-opacity="0.5" '
        f'stroke="#1a3a5c" stroke-width="2.4"/>'
    )

    # ── representative disk ─────────────────────────────────────
    out.append(
        f'<ellipse id="disk" cx="{base_cx:.1f}" cy="{disk_cy:.1f}" '
        f'rx="{disk_rx:.1f}" ry="{disk_ry:.1f}" '
        f'fill="#c0392b" fill-opacity="0.18" stroke="none"/>'
    )
    out.append(
        f'<path d="M {base_cx-disk_rx:.1f} {disk_cy:.1f} '
        f'A {disk_rx:.1f} {disk_ry:.1f} 0 0 1 '
        f'{base_cx+disk_rx:.1f} {disk_cy:.1f}" '
        f'fill="none" stroke="#c0392b" stroke-width="1.4" '
        f'stroke-dasharray="5,3"/>'
    )
    out.append(
        f'<path d="M {base_cx-disk_rx:.1f} {disk_cy:.1f} '
        f'A {disk_rx:.1f} {disk_ry:.1f} 0 0 0 '
        f'{base_cx+disk_rx:.1f} {disk_cy:.1f}" '
        f'fill="none" stroke="#c0392b" stroke-width="2.2"/>'
    )

    # ── central vertical axis y (apex → base) ───────────────────
    out.append(
        f'<line id="axis_y" x1="{apex_x:.1f}" y1="{apex_y:.1f}" '
        f'x2="{base_cx:.1f}" y2="{base_cy:.1f}" '
        f'stroke="#1f6b1f" stroke-width="2.0"/>'
    )
    # height-label h on the axis (lower portion to keep clear of disk)
    out.append(
        f'<text x="{apex_x-12:.1f}" y="{(apex_y + base_cy)/2 + 50:.1f}" '
        f'font-size="20" font-family="serif" text-anchor="end" '
        f'fill="#1f6b1f" font-weight="bold">h</text>'
    )

    # y label from apex to disk
    out.append(
        f'<text x="{apex_x + 8:.1f}" y="{(apex_y + disk_cy)/2 + 5:.1f}" '
        f'font-size="18" font-family="serif" '
        f'fill="#1f6b1f" font-weight="bold">y</text>'
    )

    # disk-radius label x
    out.append(
        f'<line x1="{base_cx:.1f}" y1="{disk_cy:.1f}" '
        f'x2="{base_cx + disk_rx:.1f}" y2="{disk_cy:.1f}" '
        f'stroke="#c0392b" stroke-width="2.4"/>'
    )
    out.append(
        f'<text x="{base_cx + disk_rx/2:.1f}" y="{disk_cy - 10:.1f}" '
        f'font-size="16" font-family="serif" text-anchor="middle" '
        f'fill="#c0392b" font-weight="bold">x</text>'
    )

    # base-radius r at the bottom
    out.append(
        f'<line x1="{base_cx:.1f}" y1="{base_cy:.1f}" '
        f'x2="{base_cx + base_rx:.1f}" y2="{base_cy:.1f}" '
        f'stroke="#1a3a5c" stroke-width="2.4"/>'
    )
    out.append(
        f'<text x="{base_cx + base_rx/2:.1f}" y="{base_cy + 22:.1f}" '
        f'font-size="20" font-family="serif" text-anchor="middle" '
        f'fill="#1a3a5c" font-weight="bold">r</text>'
    )

    # ── derivation block ───────────────────────────────────────
    rx = 580.0
    lines = [
        ("Disk method", "#111", 19, True),
        ("(slice the cone into thin", "#444", 14, False),
        ("horizontal disks of thickness dy,", "#444", 14, False),
        ("measuring y from the apex):", "#444", 14, False),
        ("", "", 0, False),
        ("Similar triangles give the radius", "#222", 15, False),
        ("of the disk at height y:", "#222", 15, False),
        ("    x  =  r · (y / h)", "#c0392b", 17, True),
        ("", "", 0, False),
        ("So each disk has volume", "#222", 15, False),
        ("    dV  =  π · x² · dy", "#222", 15, False),
        ("        =  π · r² · y² / h² · dy", "#c0392b", 16, True),
        ("", "", 0, False),
        ("Integrate from y = 0 to y = h :", "#222", 15, False),
        ("    V  =  (π r² / h²) ∫₀^h y² dy", "#222", 16, False),
        ("        =  (π r² / h²) · (h³ / 3)", "#222", 15, False),
        ("        =  (1/3) π r² h", "#222", 16, False),
        ("", "", 0, False),
        ("Therefore", "#222", 15, False),
        ("    V  =  (1/3) π r² h", "#1f6b1f", 24, True),
    ]
    _emit_lines(out, rx, title_h + 24, lines, line_step=24, gap_step=10)

    out.append('</svg>')
    svg = "\n".join(out)

    narration: List[dict] = [
        {"speak": ("To prove the volume of a cone, we slice it into "
                   "thin horizontal disks and add them up with an "
                   "integral."),
         "highlight": ["title", "cone"]},
        {"speak": ("At a distance y below the apex, the disk's "
                   "radius x is determined by similar triangles: "
                   "x divided by y equals r divided by h, so x "
                   "equals r times y over h."),
         "highlight": ["disk", "axis_y"]},
        {"speak": ("Each disk has volume pi x squared dy, which is "
                   "pi r squared y squared over h squared times dy."),
         "highlight": ["disk"]},
        {"speak": ("Integrating from y equals zero at the apex to "
                   "y equals h at the base gives the volume one "
                   "third pi r squared h."),
         "highlight": ["cone"]},
    ]
    return svg, narration


__all__ = ["volume_of_sphere", "volume_of_cone"]
