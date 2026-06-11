"""Voice narration with phrase-accurate visual highlight timing.

The host LLM hands Sevim a *script* — an ordered list of phrases, each
optionally tagged with the canvas element it talks about.  This module:

  1. Synthesises each phrase to its own WAV (via OpenAI TTS or piper).
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

Backends
--------
``SEVIM_TTS_BACKEND`` selects the synthesiser:
  * ``openai``  — OpenAI's TTS endpoint (``tts-1-hd`` by default).
                  Higher voice quality, costs money (~$0.036 per turn
                  at typical phrase length × 15 phrases).
  * ``piper``   — local piper-tts, free, fast, lower quality.  Voice
                  model lives at ``SEVIM_VOICE_MODEL`` (default
                  ``~/.local/share/sevim/voices/en_US-lessac-medium.onnx``).
  * ``auto``    — (default) prefer ``openai`` when ``OPENAI_API_KEY``
                  is set, fall back to ``piper`` otherwise.

When OpenAI is selected, the model + voice are tunable via
``SEVIM_TTS_MODEL`` (default ``tts-1-hd``) and ``SEVIM_TTS_VOICE``
(default ``alloy``).  Within a single script every phrase uses the
same backend so sample-rate / channel format stays uniform.
"""
from __future__ import annotations

import os
import sys
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


# ---------------------------------------------------------------------------
# Backend selection — OpenAI vs piper.
# ---------------------------------------------------------------------------

_OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
_DEFAULT_OPENAI_MODEL = "tts-1-hd"   # quality > speed; ~1-2 s/phrase
_DEFAULT_OPENAI_VOICE = "alloy"      # clear neutral voice for tutoring


def _tts_backend() -> str:
    return os.environ.get("SEVIM_TTS_BACKEND", "auto").lower()


def _resolved_backend() -> str:
    """Materialise ``auto`` into ``openai`` or ``piper`` based on key
    availability.  Once chosen the value is sticky for the call."""
    chosen = _tts_backend()
    if chosen == "auto":
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        return "piper"
    return chosen


def _openai_synthesize_wav(text: str, out_path: Path) -> None:
    """POST text to OpenAI's /v1/audio/speech and write the WAV bytes
    to ``out_path``.  Raises on HTTP / network failure so the caller
    can fall back to piper.
    """
    import httpx
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    payload = {
        "model": os.environ.get("SEVIM_TTS_MODEL", _DEFAULT_OPENAI_MODEL),
        "voice": os.environ.get("SEVIM_TTS_VOICE", _DEFAULT_OPENAI_VOICE),
        "input": text,
        "response_format": "wav",
    }
    # A short phrase synthesises in ~1-3 s.  The old 45 s ceiling meant a
    # single hung request stalled the WHOLE parallel batch for 45 s (the
    # batch waits on its slowest phrase) before falling back — the dominant
    # cause of a "narration took ~60 s" turn.  Cap aggressively; the caller
    # retries once on failure.  Override via SEVIM_TTS_TIMEOUT_S.
    timeout_s = float(os.environ.get("SEVIM_TTS_TIMEOUT_S", "12"))
    with httpx.Client(timeout=timeout_s) as c:
        r = c.post(
            _OPENAI_TTS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"OpenAI TTS HTTP {r.status_code}: {r.text[:200]}"
            )
        out_path.write_bytes(r.content)


def _synthesize_phrase(text: str, out_path: Path, backend: str) -> str:
    """Synthesise one phrase to ``out_path``.  Returns the backend that
    actually produced the audio (may differ from the requested one if
    OpenAI failed and we fell back to piper)."""
    if backend == "openai":
        # Retry once before giving up: a single transient timeout/5xx must
        # NOT drop this phrase to piper, because one piper phrase forces a
        # full all-piper re-synth (voice uniformity) and throws away every
        # already-synthesised OpenAI phrase.  Two short-timeout attempts
        # are still far cheaper than the old single 45 s try, and they keep
        # the narration all-OpenAI through a transient blip.
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                _openai_synthesize_wav(text, out_path)
                return "openai"
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        print(f"[narrate] OpenAI TTS failed after 2 tries ({last_exc}); "
              "falling back to piper for this phrase",
              flush=True, file=sys.stderr)
    if not voice_available():
        raise FileNotFoundError(
            f"piper voice model not found at {_voice_path()} and OpenAI "
            f"TTS unavailable.  Install a piper voice or set "
            f"OPENAI_API_KEY + SEVIM_TTS_BACKEND=openai."
        )
    voice = _load_voice()
    with wave.open(str(out_path), "wb") as wav:
        voice.synthesize_wav(text, wav)
    return "piper"


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
    # Element id(s) to highlight while this phrase plays.  The viewer
    # accepts either a single string or a list of strings, so a phrase
    # that mentions multiple things can spotlight all of them at once.
    # ``None`` / empty list means "highlight nothing for this phrase".
    highlight: str | list[str] | None


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

    backend = _resolved_backend()
    # If we picked piper (either explicitly or because no OpenAI key
    # is configured), the voice model must exist.  When using OpenAI
    # we don't need the local model at all, so this check is gated.
    if backend == "piper" and not voice_available():
        raise FileNotFoundError(
            f"piper voice model not found at {_voice_path()}.  "
            "Install one (en_US-lessac-medium.onnx + .json) or set "
            "SEVIM_TTS_BACKEND=openai with a valid OPENAI_API_KEY."
        )

    phrases_dir = Path(out_wav_path).with_suffix(".phrases")
    phrases_dir.mkdir(parents=True, exist_ok=True)

    timings: list[PhraseTiming] = []
    cursor_s = 0.0
    sample_rate = None
    sample_width = None
    channels = None
    audio_frames: list[bytes] = []
    gap_silence: bytes = b""

    # Validate every phrase up front so the parallel synth doesn't
    # spawn a futile threadpool of bad inputs.
    for i, entry in enumerate(script):
        if not (entry.get("speak") or "").strip():
            raise ValueError(
                f"script[{i}] has no `speak` text — every phrase must speak something"
            )

    # Synthesise all phrases CONCURRENTLY.  Sequential made the chat
    # endpoint look hung for 15-25 s during a typical turn, which
    # (a) was a poor UX and (b) caused the SSE connection to be
    # cancelled by the ALB before the tool result could be sent.
    # Parallelism collapses the wall time to roughly the SLOWEST
    # single-phrase synth (~1-2 s).
    from concurrent.futures import ThreadPoolExecutor
    tmp_paths: list[Path] = [
        phrases_dir / f"phrase_{i:03d}.wav" for i in range(len(script))
    ]
    max_workers = min(len(script), 12)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [
            ex.submit(_synthesize_phrase, (entry.get("speak") or "").strip(),
                      tmp_paths[i], backend)
            for i, entry in enumerate(script)
        ]
        actual_backends = [fut.result() for fut in futures]

    # Voice-uniformity check: if OpenAI TTS was requested but ANY phrase
    # fell back to piper (e.g. OpenAI HTTP timeout on phrase 5 of 8),
    # the resulting audio would mix two different voices within the
    # same narration — confusing for the listener.  Re-synthesise the
    # ENTIRE script with piper so the voice stays consistent for the
    # whole turn.  Field report 2026-06-07: a "prove the volume of a
    # sphere" turn played with two different narrator voices because
    # one phrase timed out on OpenAI and silently fell back to piper.
    if backend == "openai" and "piper" in actual_backends:
        print(
            f"[narrate] voice mixed (openai={actual_backends.count('openai')}, "
            f"piper={actual_backends.count('piper')}); re-synthesising "
            f"all {len(script)} phrases with piper for voice uniformity",
            flush=True, file=sys.stderr,
        )
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [
                ex.submit(_synthesize_phrase,
                          (entry.get("speak") or "").strip(),
                          tmp_paths[i], "piper")
                for i, entry in enumerate(script)
            ]
            for fut in futures:
                fut.result()

    try:
        for i, entry in enumerate(script):
            text = (entry.get("speak") or "").strip()
            highlight = entry.get("highlight")
            tmp_path = tmp_paths[i]
            with wave.open(str(tmp_path), "rb") as wav:
                if sample_rate is None:
                    sample_rate = wav.getframerate()
                    sample_width = wav.getsampwidth()
                    channels = wav.getnchannels()
                    silence_frames = int(phrase_gap_s * sample_rate)
                    gap_silence = b"\x00" * (
                        silence_frames * sample_width * channels
                    )
                # OpenAI's TTS WAV uses the streaming-audio placeholder
                # nframes=INT32_MAX (2147483647) instead of the real
                # frame count.  Calling wav.readframes(2147483647)
                # tries to PRE-ALLOCATE a 4.3 GB buffer and raises
                # MemoryError on a 2 GB Fargate task.  Read in 64 K
                # frame chunks until EOF and concat — the bytes-length
                # returned is authoritative for both real and bogus
                # nframes counts.
                CHUNK_FRAMES = 65536
                pieces: list[bytes] = []
                while True:
                    chunk = wav.readframes(CHUNK_FRAMES)
                    if not chunk:
                        break
                    pieces.append(chunk)
                frames_bytes = b"".join(pieces)
                actual_frames = (
                    len(frames_bytes) // (sample_width * channels)
                ) if (sample_width and channels) else 0
                duration = actual_frames / sample_rate if sample_rate else 0.0
                audio_frames.append(frames_bytes)

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
# build-rev: 1778412627
# build-rev: 1778417405
# build-rev: 1778420423
