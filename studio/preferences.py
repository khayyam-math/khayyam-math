"""Backend-handled UI/settings preferences.

When the user's message is about the *system* (highlight color, audio
speed, replay) and not about *figure content*, it shouldn't go to
gpt-4o.  This module detects such requests with cheap keyword/regex
matching and applies them server-side.

Each preference handler returns ``(status_text, applied_change)`` on
match, or ``None`` to fall through to the LLM path.

The current settings are stored in a process-global dict that the
canvas viewer fetches via ``/studio/preferences`` and re-applies on
every SSE state tick.
"""
from __future__ import annotations

import re
from typing import Any


# ── Live settings (process-global, reset on Studio restart) ──────────

_SETTINGS: dict[str, Any] = {
    # CSS color used for the narration-active highlight pulse.
    "highlight_color": "#fbbf24",         # default Tailwind amber-400
    # <audio>.playbackRate for the narration audio (1.0 = normal).
    "audio_speed": 1.0,
    # <audio>.volume (0.0..1.0).
    "audio_volume": 1.0,
    # When True, narration starts immediately on canvas open.
    "autoplay": True,
}


def get_settings() -> dict[str, Any]:
    """Return a shallow copy of the current settings."""
    return dict(_SETTINGS)


def set_setting(key: str, value: Any) -> None:
    if key in _SETTINGS:
        _SETTINGS[key] = value


# ── Color name → CSS hex map for natural-language inputs ─────────────

_COLOR_MAP: dict[str, str] = {
    "yellow":   "#fbbf24",
    "amber":    "#fbbf24",
    "red":      "#ef4444",
    "crimson":  "#dc2626",
    "orange":   "#f97316",
    "green":    "#22c55e",
    "lime":     "#84cc16",
    "blue":     "#3b82f6",
    "cyan":     "#06b6d4",
    "purple":   "#a855f7",
    "violet":   "#8b5cf6",
    "magenta":  "#ec4899",
    "pink":     "#f472b6",
    "white":    "#ffffff",
    "black":    "#000000",
    "gray":     "#9ca3af",
    "grey":     "#9ca3af",
}


# ── Preference handlers (each returns dict on match, None otherwise) ──

def _try_highlight_color(text: str) -> dict[str, Any] | None:
    """Detect 'use red for highlight' / 'change highlight color to blue'
    / 'highlight in green' / 'use a different color (red) for the
    highlights' style requests."""
    t = text.lower()
    if "highlight" not in t and "highlights" not in t:
        return None
    # Look for any color name in the message; first match wins.
    for color_word, hex_value in _COLOR_MAP.items():
        # Word boundary so 'orange' doesn't match inside 'orangish'.
        if re.search(rf"\b{color_word}\b", t):
            set_setting("highlight_color", hex_value)
            return {
                "intent": "preference",
                "key": "highlight_color",
                "value": hex_value,
                "spoken_value": color_word,
                "status": f"Highlight color set to {color_word} ({hex_value}).",
            }
    # Hex code like #ff00aa or rgb(...)
    m = re.search(r"#[0-9a-fA-F]{3,8}\b", text)
    if m:
        set_setting("highlight_color", m.group(0))
        return {
            "intent": "preference",
            "key": "highlight_color",
            "value": m.group(0),
            "spoken_value": m.group(0),
            "status": f"Highlight color set to {m.group(0)}.",
        }
    return None


def _try_audio_speed(text: str) -> dict[str, Any] | None:
    """'speak slower / faster / 1.5x speed / normal speed'"""
    t = text.lower()
    if not re.search(r"\b(speak|narration|audio|voice|play|read|talk)\w*\b", t):
        return None
    mapping = {
        r"\bnormal speed\b|\bnormal pace\b":             1.0,
        r"\bvery slow\b":                                0.5,
        r"\bslower\b|\bslow down\b|\bspeak slowly\b":    0.75,
        r"\bfaster\b|\bspeed up\b|\bspeak faster\b":     1.4,
        r"\bvery fast\b|\b2x\b|\b2 x\b|\bdouble speed\b": 2.0,
        r"\b1\.5 ?x\b":                                  1.5,
    }
    for pattern, value in mapping.items():
        if re.search(pattern, t):
            set_setting("audio_speed", float(value))
            return {
                "intent": "preference",
                "key": "audio_speed",
                "value": float(value),
                "status": f"Audio speed set to {value}× normal.",
            }
    return None


def _try_audio_volume(text: str) -> dict[str, Any] | None:
    t = text.lower()
    if not re.search(r"\b(audio|voice|narration|sound|volume)\w*\b", t):
        return None
    if re.search(r"\bmute\b|\bsilent\b", t):
        set_setting("audio_volume", 0.0)
        return {"intent": "preference", "key": "audio_volume",
                "value": 0.0, "status": "Audio muted."}
    if re.search(r"\blouder\b|\bturn up\b|\bvolume up\b", t):
        new_v = min(1.0, _SETTINGS["audio_volume"] + 0.2)
        set_setting("audio_volume", new_v)
        return {"intent": "preference", "key": "audio_volume",
                "value": new_v, "status": f"Volume set to {new_v:.0%}."}
    if re.search(r"\bquieter\b|\blower\b|\bturn down\b|\bvolume down\b", t):
        new_v = max(0.0, _SETTINGS["audio_volume"] - 0.2)
        set_setting("audio_volume", new_v)
        return {"intent": "preference", "key": "audio_volume",
                "value": new_v, "status": f"Volume set to {new_v:.0%}."}
    return None


_HANDLERS = (_try_highlight_color, _try_audio_speed, _try_audio_volume)


def parse_preference(text: str) -> dict[str, Any] | None:
    """Run all handlers in order; return the first match.  ``None`` means
    the message is not a preference and should fall through to the LLM."""
    if not text or not text.strip():
        return None
    for handler in _HANDLERS:
        try:
            result = handler(text)
        except Exception:  # noqa: BLE001 — preference handlers must never crash chat
            continue
        if result is not None:
            return result
    return None
