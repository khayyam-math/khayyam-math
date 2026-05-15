"""Record a 60-90 second demo of the studio in action.

Playwright captures the rendered viewport as a silent webm; we then
convert to mp4 (h.264) via ffmpeg. The narration WAV plays through
the system audio at runtime (so you can hear it locally if you run
non-headless), but the recording does NOT capture audio. The user
will overlay narration themselves or accept the silent demo.

What it captures:
  0:00–0:05  Empty studio (intro shot).
  0:05–0:20  Prompt 1 — "draw a DFA for L = (a|b)* ending in ab"
                       → Graphviz route → state diagram appears.
  0:20–0:40  Prompt 2 — "matrix inverse of [[4,7],[2,6]]"
                       → matrix template fast-path.
  0:40–1:00  Prompt 3 — "show the Pythagorean theorem with a 3-4-5
                          triangle and squares on each side"
                       → LLM-SVG path with vision audit.
  1:00–1:05  Final shot of canvas + chat history.

Output:
  /tmp/khayyam_demo.webm    (raw Playwright output)
  /tmp/khayyam_demo.mp4     (re-encoded for upload)

Usage:
  .venv/bin/python scripts/record_demo_video.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765/studio"
OUT_DIR = Path("/tmp/khayyam_demo")
OUT_WEBM = OUT_DIR / "demo.webm"
OUT_MP4 = OUT_DIR / "demo.mp4"

PROMPTS = [
    ("draw a DFA for the language L = (a|b)* ending in ab", 18),
    ("matrix inverse of [[4,7],[2,6]]", 20),
    ("show the unit circle with sin and cos labelled at 30, 45, 60 "
     "degrees", 22),
]


def _wait_canvas_ready(page, timeout_s: float = 60.0) -> bool:
    """Two-phase wait: see Emerging…, then see it clear."""
    iframe = page.frame_locator("iframe#canvas-frame")
    deadline_emerging = time.monotonic() + 45.0
    saw_emerging = False
    while time.monotonic() < deadline_emerging:
        try:
            t = iframe.locator("#play-btn").text_content(timeout=1500)
            if t and t.strip() == "Emerging…":
                saw_emerging = True
                break
        except Exception:
            pass
        time.sleep(0.4)
    if not saw_emerging:
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            t = iframe.locator("#play-btn").text_content(timeout=1500)
            if t and t.strip() != "Emerging…":
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def main():
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(OUT_DIR),
            record_video_size={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        page.set_default_timeout(20_000)
        print(f"[rec] navigate to {URL}", flush=True)
        page.goto(URL, wait_until="networkidle")

        # 0:00–0:05 intro shot
        time.sleep(5)

        for i, (prompt, wait_after_s) in enumerate(PROMPTS, 1):
            print(f"[rec] prompt {i}: {prompt[:60]}", flush=True)
            # Force-enable input + send button so a previous in-flight
            # generation doesn't block the next type.
            try:
                page.evaluate(
                    "() => { for (const sel of ['#input','#send-btn']) "
                    "{ const el=document.querySelector(sel); "
                    "if (el) { el.disabled=false; "
                    "el.removeAttribute('disabled'); } } }",
                )
            except Exception:
                pass
            # Type slowly so the typing is visible in the recording.
            page.click("#input")
            page.fill("#input", "")
            for ch in prompt:
                page.keyboard.type(ch)
                time.sleep(0.025)
            time.sleep(0.5)
            page.click("#send-btn")
            ok = _wait_canvas_ready(page, timeout_s=wait_after_s)
            print(f"[rec]   ready={ok}", flush=True)
            # Linger on the rendered canvas so the viewer can see it.
            time.sleep(4)

        # Final shot of the chat + canvas
        time.sleep(3)

        ctx.close()
        browser.close()

    # Find the recorded webm (Playwright writes to a random filename).
    found = sorted(OUT_DIR.glob("*.webm"))
    if not found:
        print("[rec] FAIL — no webm produced", file=sys.stderr)
        return 1
    raw = found[0]
    raw.rename(OUT_WEBM)
    print(f"[rec] webm: {OUT_WEBM} ({OUT_WEBM.stat().st_size:,} bytes)",
          flush=True)

    # Re-encode to mp4 for broad compatibility.
    print(f"[rec] transcoding to {OUT_MP4} via ffmpeg…", flush=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(OUT_WEBM),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "20",
        "-movflags", "+faststart",
        str(OUT_MP4),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[rec] ffmpeg failed: {r.stderr[-500:]}", file=sys.stderr)
        return 1
    print(f"[rec] mp4: {OUT_MP4} ({OUT_MP4.stat().st_size:,} bytes)",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
