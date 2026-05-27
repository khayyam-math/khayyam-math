"""UX-audit screenshots: send a prompt to /studio, wait for the
canvas to finish emerging, take screenshots at desktop + mobile
viewports.

Usage:
    .venv/bin/python scripts/audit_studio_screenshots.py \\
        --url http://127.0.0.1:8765 \\
        --out /tmp/audit_shots \\
        --prompt "draw a DFA for L = (a|b)* ending in ab"
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import (
    Page, TimeoutError as PWTimeout, sync_playwright,
)


VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800,
                "device_scale_factor": 1.0,
                "is_mobile": False, "has_touch": False},
    "mobile":  {"width": 390, "height": 844,
                "device_scale_factor": 3.0,
                "is_mobile": True, "has_touch": True,
                "user_agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                )},
}


def _wait_for_canvas_ready(page: Page, timeout_s: float = 90.0) -> str:
    """Wait until the canvas iframe finishes 'Emerging…' state.

    The play button in the canvas iframe shows 'Emerging…' while
    the figure is being built. When it changes to something else
    (Play / Replay / ready), the canvas is done.

    Returns the final play-button text on success, "" on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            iframe = page.frame_locator("iframe.canvas-iframe").first
            btn = iframe.locator("#play-btn")
            text = btn.text_content(timeout=2000)
            if text and text.strip() != "Emerging…":
                return text.strip()
        except Exception:
            pass
        time.sleep(0.5)
    return ""


def _hide_loading_overlay(page: Page) -> None:
    """If the 'Preparing visualization' overlay is still up, dismiss
    it explicitly so it doesn't sit over the screenshot."""
    page.evaluate("""
        () => {
            const el = document.querySelector('.canvas-loading');
            if (el) el.setAttribute('hidden', '');
        }
    """)


def run_one(
    pw_browser, *, name: str, viewport: dict, url: str, prompt: str,
    out_dir: Path, wait_after_render_s: float = 1.5,
    login_url: str | None = None,
) -> dict:
    print(f"  [{name}] viewport={viewport['width']}x{viewport['height']}",
          flush=True)
    ctx = pw_browser.new_context(
        viewport={"width": viewport["width"], "height": viewport["height"]},
        device_scale_factor=viewport.get("device_scale_factor", 1.0),
        is_mobile=viewport.get("is_mobile", False),
        has_touch=viewport.get("has_touch", False),
        user_agent=viewport.get("user_agent"),
    )
    page = ctx.new_page()
    page.set_default_timeout(20_000)
    if login_url:
        # Magic-link verify → sets auth cookie → 302 to /studio.
        page.goto(login_url, wait_until="networkidle")
    else:
        page.goto(url + "/studio", wait_until="networkidle")
    # PRE-prompt screenshot (empty studio)
    page.screenshot(
        path=str(out_dir / f"{name}__01_studio_empty.png"),
        full_page=False,
    )
    # Find the prompt textarea and submit button.
    page.wait_for_selector("#input", timeout=10_000)
    page.fill("#input", prompt)
    # Click the send button.
    page.wait_for_selector("#send-btn", timeout=5_000)
    page.click("#send-btn")
    # Wait for the figure to finish emerging.
    btn_text = _wait_for_canvas_ready(page, timeout_s=240.0)
    if btn_text:
        print(f"  [{name}] play-btn now: {btn_text!r}", flush=True)
    else:
        print(f"  [{name}] timed out waiting for 'Emerging…' to clear",
              flush=True)
    # Give the iframe a tick to settle, hide the overlay, then snap.
    time.sleep(wait_after_render_s)
    try:
        _hide_loading_overlay(page)
    except Exception:
        pass
    full_path = out_dir / f"{name}__02_canvas_ready.png"
    page.screenshot(path=str(full_path), full_page=False)
    # Try a wider full-page shot too — captures any scrolling content.
    full_path2 = out_dir / f"{name}__03_full_page.png"
    page.screenshot(path=str(full_path2), full_page=True)
    # Also save just the canvas iframe at native size.
    try:
        iframe_elem = page.locator("iframe.canvas-iframe").first
        iframe_path = out_dir / f"{name}__04_canvas_only.png"
        iframe_elem.screenshot(path=str(iframe_path))
    except Exception as exc:
        print(f"  [{name}] canvas-only shot FAILED: {exc}", flush=True)
    ctx.close()
    return {"viewport": name, "btn_text_after": btn_text,
            "shots": [str(full_path.relative_to(out_dir.parent))]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--out", type=Path, default=Path("/tmp/audit_shots"))
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--tag", default="run", help="Tag added to filenames.")
    ap.add_argument("--viewports", default="desktop,mobile",
                    help="comma-separated list of viewports to run")
    ap.add_argument("--login-url", default=None,
                    help="Magic-link verify URL (auto-logs in before "
                         "navigating to /studio).")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    requested = [v.strip() for v in args.viewports.split(",") if v.strip()]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for vp in requested:
            if vp not in VIEWPORTS:
                print(f"  skipping unknown viewport: {vp}")
                continue
            name = f"{args.tag}__{vp}"
            run_one(browser, name=name, viewport=VIEWPORTS[vp],
                    url=args.url, prompt=args.prompt, out_dir=args.out,
                    login_url=args.login_url)
        browser.close()
    print(f"\nscreenshots in {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
