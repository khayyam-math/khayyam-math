"""Capture canvas-only screenshots for the landing-page gallery.

Six prompts spanning topic + route diversity:
  1. unit circle with sin/cos       — geometry, LLM-SVG path
  2. Riemann sum                    — calculus, LLM-SVG path
  3. matrix multiplication          — linalg, TEMPLATE path (deterministic)
  4. DFA L=(a|b)*ab                 — automata, GRAPHVIZ path
  5. Pythagorean theorem            — geometry, LLM-SVG path
  6. Venn diagram                   — set theory, LLM-SVG path

For each, log in via magic link, send prompt, wait for canvas to
finish emerging, screenshot the canvas iframe only at 1200×800.

Output: service/static/screenshots/landing_<slug>.png
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROMPTS = [
    ("trig_unit_circle",
     "show the unit circle with sin and cos labelled at 30, 45, 60 degrees"),
    ("calc_riemann_sum",
     "Riemann sum approximating the integral of x^2 from 0 to 2 with 8 rectangles"),
    ("linalg_matrix_mul",
     "matrix multiplication of [[1,2],[3,4]] and [[5,6],[7,8]] with a worked example"),
    ("auto_dfa_ab",
     "draw a DFA for the language L = (a|b)* ending in ab"),
    ("geo_pythagoras",
     "show the Pythagorean theorem with a 3-4-5 triangle and squares on each side"),
    ("set_venn",
     "Venn diagram for A union B intersect C with three labelled regions"),
]


def _wait_canvas_ready(page, timeout_s: float = 120.0) -> bool:
    """Two-phase wait so we don't get fooled when the previous
    request's "Play" state lingers before the new one's "Emerging…"
    state arrives. Phase 1: wait for Emerging…. Phase 2: wait for
    it to clear. Returns True only if both phases succeed."""
    iframe = page.frame_locator("iframe#canvas-frame")
    # Phase 1: a NEW request must enter Emerging… within 45s.
    deadline_p1 = time.monotonic() + 45.0
    saw_emerging = False
    while time.monotonic() < deadline_p1:
        try:
            t = iframe.locator("#play-btn").text_content(timeout=2000)
            if t and t.strip() == "Emerging…":
                saw_emerging = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not saw_emerging:
        return False
    # Phase 2: wait for Emerging… to clear within remaining budget.
    deadline_p2 = time.monotonic() + timeout_s
    while time.monotonic() < deadline_p2:
        try:
            t = iframe.locator("#play-btn").text_content(timeout=2000)
            if t and t.strip() != "Emerging…":
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _crop_canvas(in_path: Path) -> None:
    """Crop the tall iframe screenshot to the canvas content only.

    The iframe is rendered taller than the figure; the area below
    the figure is solid white. We trim trailing white rows so the
    landing-page gallery shows the figure flush against its border.
    """
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return
    im = Image.open(in_path).convert("RGB")
    # Strip top header + a thin gap, then auto-trim the bottom.
    # Find the bottom-most non-white row.
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg)
    bbox = diff.getbbox()
    if bbox:
        # Keep the full width, trim top of header / bottom of whitespace.
        left, upper, right, lower = bbox
        # Add a small margin so the figure breathes.
        upper = max(0, upper - 8)
        lower = min(im.size[1], lower + 8)
        im.crop((0, upper, im.size[0], lower)).save(in_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--login-url", required=True)
    ap.add_argument("--url", default="https://khayyammath.com")
    ap.add_argument("--out", type=Path,
                    default=Path("service/static/screenshots"))
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=2.0,
        )
        page = ctx.new_page()
        page.set_default_timeout(20_000)
        # Log in once; reuse session across prompts.
        page.goto(args.login_url, wait_until="networkidle")
        page.wait_for_selector("#input", timeout=10_000)

        for slug, prompt in PROMPTS:
            print(f"  → {slug}: {prompt[:60]}", flush=True)
            # Force-enable the textarea + send button (the studio
            # disables them across rev-bumps; manually safe to fire).
            page.evaluate(
                "() => { "
                "  for (const sel of ['#input', '#send-btn']) { "
                "    const el = document.querySelector(sel); "
                "    if (el) { el.disabled = false; "
                "             el.removeAttribute('disabled'); } "
                "  } "
                "}",
            )
            page.fill("#input", prompt)
            page.click("#send-btn")
            ok = _wait_canvas_ready(page)
            if not ok:
                print(f"    timed out — saving what's there anyway", flush=True)
            time.sleep(3.0)  # let the canvas + narration manifest settle
            # Screenshot the canvas iframe only, then crop the
            # trailing whitespace.
            try:
                iframe_el = page.locator("iframe#canvas-frame")
                out = args.out / f"landing_{slug}.png"
                iframe_el.screenshot(path=str(out))
                _crop_canvas(out)
                print(f"    saved {out}", flush=True)
            except Exception as exc:
                print(f"    canvas screenshot FAILED: {exc}", flush=True)
                page.screenshot(
                    path=str(args.out / f"landing_{slug}_fallback.png"),
                )
        ctx.close()
        browser.close()
    print(f"\nDone. files in {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
