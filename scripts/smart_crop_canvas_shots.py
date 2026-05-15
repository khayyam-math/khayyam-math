"""Crop the landing-gallery screenshots: keep only content bands
(rows with at least one non-white pixel), stack them with small
padding, drop the empty whitespace in between.

In-place: rewrites each PNG.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image


def smart_crop(path: Path) -> None:
    im = np.array(Image.open(path).convert("RGB"))
    h, w, _ = im.shape

    # Row is "content" if it has any non-near-white pixel.
    is_content = (im < 245).any(axis=2).any(axis=1)
    if not is_content.any():
        return

    # Find content bands (consecutive content rows).
    bands = []
    in_band = False
    start = 0
    for y, c in enumerate(is_content):
        if c and not in_band:
            start = y
            in_band = True
        elif not c and in_band:
            bands.append((start, y))
            in_band = False
    if in_band:
        bands.append((start, h))

    if not bands:
        return

    # Merge bands separated by <= 40 rows of whitespace.
    merged = [bands[0]]
    for s, e in bands[1:]:
        ps, pe = merged[-1]
        if s - pe <= 40:
            merged[-1] = (ps, e)
        else:
            merged.append((s, e))

    # Concatenate content bands with 16px padding between them.
    pad = 16
    out_rows = []
    for s, e in merged:
        s = max(0, s - pad)
        e = min(h, e + pad)
        if out_rows:
            # Add a thin spacer so the figure breathes from the header.
            spacer = np.full((pad * 2, w, 3), 255, dtype=np.uint8)
            out_rows.append(spacer)
        out_rows.append(im[s:e])

    if not out_rows:
        return
    result = np.vstack(out_rows)
    Image.fromarray(result).save(path, optimize=True)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1
               else "service/static/screenshots")
    for p in sorted(out.glob("landing_*.png")):
        before = p.stat().st_size
        h_before = Image.open(p).size[1]
        smart_crop(p)
        after = p.stat().st_size
        h_after = Image.open(p).size[1]
        print(f"  {p.name}: {h_before}px → {h_after}px  "
              f"({before:,} → {after:,} bytes)")


if __name__ == "__main__":
    main()
