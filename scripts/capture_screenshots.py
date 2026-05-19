"""Capture screenshots of khayyammath.com for the IP registration package.

Writes PNG files into ``uae_ip_registration/screenshots/`` for inclusion
in ``application_forms_and_screens.tex``.

Usage:
    .venv/bin/python scripts/capture_screenshots.py
"""
from __future__ import annotations

from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "uae_ip_registration" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

# Each entry: (url-path, filename, optional viewport, optional wait_ms,
#              optional post-load action callable taking the page)
TARGETS = [
    ("/",        "01_landing.png",     {"width": 1280, "height": 900},  1500, None),
    ("/studio",  "03_studio_chat.png", {"width": 1280, "height": 800},  2000, None),
    ("/terms",   "05_terms.png",       {"width": 1100, "height": 900},  1000, None),
    ("/contact", "06_contact.png",     {"width": 1100, "height": 900},  1000, None),
]

BASE = "https://khayyammath.com"


def capture():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for path, fname, viewport, wait_ms, post in TARGETS:
            url = BASE + path
            ctx = browser.new_context(viewport=viewport,
                                      device_scale_factor=2)
            page = ctx.new_page()
            print(f"  loading {url} ...", flush=True)
            try:
                page.goto(url, wait_until="networkidle", timeout=20000)
            except Exception as exc:
                print(f"    ! navigation: {exc}", flush=True)
                ctx.close(); continue
            time.sleep(wait_ms / 1000.0)
            if post is not None:
                try:
                    post(page)
                except Exception as exc:
                    print(f"    ! post-load action: {exc}", flush=True)
            out_path = OUT / fname
            page.screenshot(path=str(out_path), full_page=False)
            print(f"    -> {out_path.relative_to(ROOT)} "
                  f"({out_path.stat().st_size // 1024} KB)", flush=True)
            ctx.close()
        browser.close()


if __name__ == "__main__":
    capture()
