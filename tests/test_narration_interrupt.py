"""Lock the narration-interrupt wiring in place.

The interrupt path is plain JavaScript inside two static HTML files:
``service/static/canvas.html`` (the iframe — listens for postMessage)
and ``studio/static/studio.html`` (the chat — sends postMessage on
typing / mic click / send).  We can't drive a real browser from
pytest, but we CAN guarantee the wiring exists so a future refactor
that drops a listener fails CI instead of silently shipping a
narrator that talks over the user.
"""
from __future__ import annotations

import re
from pathlib import Path


CANVAS_HTML = (Path(__file__).resolve().parent.parent
               / "service" / "static" / "canvas.html")
STUDIO_HTML = (Path(__file__).resolve().parent.parent
               / "studio" / "static" / "studio.html")


def _read(path: Path) -> str:
    return path.read_text()


def test_canvas_listens_for_pause_message():
    """Iframe listens for the parent's pause command and stops both
    audio elements + clears the highlight."""
    html = _read(CANVAS_HTML)
    assert "addEventListener('message'" in html, (
        "canvas.html must register a window 'message' listener for the "
        "interrupt path"
    )
    # The pause must be triggered specifically on type='sevim_pause_audio'
    # (not just any message), and must touch BOTH the intro and narration
    # audio elements.
    block = re.search(
        r"addEventListener\('message'.*?\}\);", html, re.DOTALL,
    )
    assert block, "couldn't find the message listener block"
    body = block.group(0)
    assert "sevim_pause_audio" in body
    assert "intro.pause()" in body
    assert "audio.pause()" in body
    # And it should clear the highlight so the now-paused phrase doesn't
    # blink forever.
    assert "clearHighlight()" in body


def test_studio_posts_pause_on_user_interaction():
    """Studio chat fires interruptNarration on focus, input, mic, send."""
    html = _read(STUDIO_HTML)
    assert "function interruptNarration" in html, (
        "studio.html must define interruptNarration() helper"
    )
    assert "postMessage({type: 'sevim_pause_audio'}" in html, (
        "interruptNarration must postMessage 'sevim_pause_audio'"
    )
    # All four user-interaction triggers wire to it.
    # 1. Mic button click → before recog.start()
    mic_section = re.search(
        r"micBtn\.addEventListener\('click'.*?\}\);", html, re.DOTALL,
    )
    assert mic_section and "interruptNarration()" in mic_section.group(0), (
        "mic click handler must call interruptNarration before opening "
        "the mic — otherwise the speakers feed into the recording"
    )
    # 2. sendMessage start
    send_section = re.search(
        r"async function sendMessage\(\)\s*\{(.*?)\n  \}", html, re.DOTALL,
    )
    assert send_section and "interruptNarration()" in send_section.group(1), (
        "sendMessage must call interruptNarration as its first line"
    )
    # 3-4. Textarea focus + input listeners
    assert "input.addEventListener('focus', interruptNarration)" in html
    assert "input.addEventListener('input', interruptNarration)" in html


def test_pause_message_does_not_intercept_unrelated_messages():
    """The handler short-circuits on non-matching messages so a future
    cross-frame integration (e.g. analytics) doesn't accidentally
    silence the narrator."""
    html = _read(CANVAS_HTML)
    block = re.search(
        r"addEventListener\('message'.*?\}\);", html, re.DOTALL,
    ).group(0)
    # Guard against falsy or non-object payloads.
    assert "typeof ev.data !== 'object'" in block or "ev.data === null" in block
    # Specific type check.
    assert "ev.data.type === 'sevim_pause_audio'" in block
