"""Voice narration with phrase-accurate visual highlight timing.

The host LLM hands Sevim a *script* — an ordered list of phrases, each
optionally tagged with the canvas element it talks about.  This module:

  1. Synthesises each phrase to its own WAV via piper-tts.
  2. Reads each phrase's exact duration from its WAV header.
  3. Concatenates the phrases into a single audio file with a tiny
     silence gap between them (so the cadence sounds natural).
  4. Returns a manifest mapping ``[start_s, end_s)`` for each phrase to
     its highlight target.

Because each phrase is timed by its own WAV — not estimated from word
count — the highlight schedule is exact: when the audio cursor hits
``phrase.start_s``, that phrase's audio is genuinely starting, and any
highlight on that phrase becomes active at that instant.  No drift, no
estimation, no need for forced alignment.

Voice model
-----------
Default path: ``~/.local/share/sevim/voices/en_US-lessac-medium.onnx``.
Override via the env var ``SEVIM_VOICE_MODEL``.  The companion JSON
config must sit next to the .onnx (piper expects ``<model>.onnx.json``).
"""
from __future__ import annotations

import os
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Voice loader (cached per-process — model load is a few hundred ms).
# ---------------------------------------------------------------------------

_DEFAULT_VOICE = (
    Path.home() / ".local" / "share" / "sevim" / "voices"
    / "en_US-lessac-medium.onnx"
)

_voice_cache: dict[str, Any] = {}
_voice_lock = threading.Lock()


def _voice_path() -> Path:
    override = os.environ.get("SEVIM_VOICE_MODEL")
    return Path(override) if override else _DEFAULT_VOICE


def _load_voice():
    path = str(_voice_path())
    with _voice_lock:
        if path in _voice_cache:
            return _voice_cache[path]
        from piper import PiperVoice  # imported lazily — heavy
        voice = PiperVoice.load(path)
        _voice_cache[path] = voice
        return voice


def voice_available() -> bool:
    """True iff the configured voice model file exists on disk."""
    return _voice_path().is_file()


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

# Inserted between phrases — small enough to feel continuous, large
# enough to give the listener time to register a highlight transition.
_PHRASE_GAP_S = 0.18


@dataclass
class PhraseTiming:
    """One phrase's place in the final audio."""
    text: str
    start_s: float
    end_s: float
    highlight: str | None  # canvas node id ('n_x') or edge id ('e_…') or None


def synthesize_script(
    script: list[dict],
    out_wav_path: str,
    phrase_gap_s: float = _PHRASE_GAP_S,
) -> dict:
    """Synthesise a multi-phrase script and produce a timing manifest.

    Args:
        script: Ordered list of phrase dicts.  Each phrase is
            ``{"speak": str, "highlight": Optional[str]}``.  ``speak``
            is the text to say; ``highlight`` is the canvas element id
            that should light up while this phrase is being spoken
            (e.g. ``"n_unit_circle"``, ``"e_connects_n_o_n_p"``).
        out_wav_path: Where to write the concatenated WAV.
        phrase_gap_s: Silence inserted between phrases (seconds).

    Returns:
        ``{"duration_s": float, "phrases": [PhraseTiming dicts]}``
        suitable for serialising to JSON and consuming in the viewer.

    Raises:
        FileNotFoundError: if the configured voice model is missing.
        ValueError: if ``script`` is empty or contains an empty phrase.
    """
    if not script:
        raise ValueError("script must contain at least one phrase")
    if not voice_available():
        raise FileNotFoundError(
            f"Sevim voice model not found at {_voice_path()}.  "
            "Download a piper voice (e.g. en_US-lessac-medium.onnx + .json) "
            "and place both files there, or set SEVIM_VOICE_MODEL to point "
            "at an existing model."
        )

    voice = _load_voice()
    phrases_dir = Path(out_wav_path).with_suffix(".phrases")
    phrases_dir.mkdir(parents=True, exist_ok=True)

    timings: list[PhraseTiming] = []
    cursor_s = 0.0
    sample_rate = None
    sample_width = None
    channels = None
    audio_frames: list[bytes] = []
    gap_silence: bytes = b""

    try:
        for i, entry in enumerate(script):
            text = (entry.get("speak") or "").strip()
            if not text:
                raise ValueError(
                    f"script[{i}] has no `speak` text — every phrase must "
                    f"speak something"
                )
            highlight = entry.get("highlight")
            tmp_path = phrases_dir / f"phrase_{i:03d}.wav"
            with wave.open(str(tmp_path), "wb") as wav:
                voice.synthesize_wav(text, wav)
            with wave.open(str(tmp_path), "rb") as wav:
                if sample_rate is None:
                    sample_rate = wav.getframerate()
                    sample_width = wav.getsampwidth()
                    channels = wav.getnchannels()
                    silence_frames = int(phrase_gap_s * sample_rate)
                    gap_silence = b"\x00" * (
                        silence_frames * sample_width * channels
                    )
                duration = wav.getnframes() / wav.getframerate()
                audio_frames.append(wav.readframes(wav.getnframes()))

            timings.append(PhraseTiming(
                text=text,
                start_s=cursor_s,
                end_s=cursor_s + duration,
                highlight=highlight,
            ))
            cursor_s += duration + phrase_gap_s

        # Concatenate to the final WAV.
        with wave.open(out_wav_path, "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(sample_width)
            out.setframerate(sample_rate)
            for i, frames in enumerate(audio_frames):
                out.writeframes(frames)
                if i < len(audio_frames) - 1:
                    out.writeframes(gap_silence)
    finally:
        # Cleanup per-phrase WAVs; only the concatenated file is kept.
        for f in phrases_dir.glob("phrase_*.wav"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            phrases_dir.rmdir()
        except OSError:
            pass

    # Drop the trailing gap from the reported duration so the
    # last phrase's end_s aligns with the actual audio end.
    total_duration = max(0.0, cursor_s - phrase_gap_s)

    return {
        "duration_s": total_duration,
        "phrases": [
            {
                "text": t.text,
                "start_s": t.start_s,
                "end_s": t.end_s,
                "highlight": t.highlight,
            }
            for t in timings
        ],
    }
