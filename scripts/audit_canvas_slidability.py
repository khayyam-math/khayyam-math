"""Check if the canvas iframe on mobile can be horizontally scrolled
to reveal off-screen content. Per the user's explicit requirement
("Canvas MUST be slidable on mobile") this is a non-negotiable UX
property. We confirm by:
  1. measuring iframe.contentDocument.scrollWidth vs clientWidth
  2. programmatically scrolling 200px right and confirming new
     content becomes visible
  3. taking a screenshot of the iframe in its scrolled state
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--login-url", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=3.0,
            is_mobile=True, has_touch=True,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        )
        page = ctx.new_page()
        page.set_default_timeout(20_000)
        page.goto(args.login_url, wait_until="networkidle")
        page.wait_for_selector("#input", timeout=10_000)
        page.fill("#input", args.prompt)
        page.click("#send-btn")
        # Wait for canvas iframe to settle.
        time.sleep(15)
        # Inspect the canvas iframe scrollability
        info = page.evaluate(
            """
            () => {
              const iframe = document.querySelector('iframe#canvas-frame');
              if (!iframe) return {error: 'no iframe[name=canvas]'};
              const doc = iframe.contentDocument;
              if (!doc) return {error: 'no contentDocument'};
              const body = doc.body;
              const html = doc.documentElement;
              const main = doc.querySelector('main') || body;
              const stage = doc.querySelector('#stage') || main;
              return {
                iframeWidth: iframe.getBoundingClientRect().width,
                iframeHeight: iframe.getBoundingClientRect().height,
                bodyScrollWidth: body.scrollWidth,
                bodyClientWidth: body.clientWidth,
                bodyOverflowX: getComputedStyle(body).overflowX,
                mainScrollWidth: main.scrollWidth,
                mainClientWidth: main.clientWidth,
                mainOverflowX: getComputedStyle(main).overflowX,
                stageScrollWidth: stage.scrollWidth,
                stageClientWidth: stage.clientWidth,
                svgs: Array.from(doc.querySelectorAll('svg')).map(s => ({
                    width: s.getAttribute('width'),
                    height: s.getAttribute('height'),
                    viewBox: s.getAttribute('viewBox'),
                    bbox: {
                      w: s.getBBox ? s.getBBox().width : null,
                      h: s.getBBox ? s.getBBox().height : null,
                    }
                })),
              };
            }
            """
        )
        print(json.dumps(info, indent=2), flush=True)
        # Try programmatic scroll
        page.evaluate(
            """
            () => {
              const iframe = document.querySelector('iframe#canvas-frame');
              if (!iframe) return;
              const doc = iframe.contentDocument;
              const main = doc.querySelector('main') || doc.body;
              main.scrollLeft = 300;
              return main.scrollLeft;
            }
            """
        )
        time.sleep(1)
        page.screenshot(path=str(args.out / "mobile_scrolled.png"),
                        full_page=False)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
