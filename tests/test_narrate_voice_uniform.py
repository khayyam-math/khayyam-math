"""Voice-uniformity test for the narration synthesiser.

Regression for 2026-06-07: a "Draw a sphere and prove the formula for
the volume of a sphere" turn played with TWO different narrator voices
because phrase 5 of 8 timed out on OpenAI TTS and silently fell back
to piper, while phrases 1-4 and 6-8 stayed on OpenAI.  The fix
re-synthesises the full script with piper whenever any phrase falls
back, so a single voice is used end-to-end.
"""
from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from sevim import narrate


def _make_wav(path: Path, duration_s: float = 0.1) -> None:
    sample_rate = 22050
    frames = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * frames)


def test_one_phrase_openai_failure_triggers_full_piper_resynth(tmp_path, monkeypatch):
    """When OpenAI is requested but one phrase fails over to piper,
    every phrase is re-synthesised with piper so the final audio has
    a single voice throughout.
    """
    monkeypatch.setenv("SEVIM_TTS_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    call_log: list[tuple[int, str]] = []  # (call-number, backend-used)
    openai_fail_index = 2  # 0-based; phrase 3 of 4 fails openai
    call_counter = {"n": 0}

    def stub_synth_phrase(text: str, out_path: Path, backend: str) -> str:
        idx = int(out_path.stem.split("_")[-1])
        call_counter["n"] += 1
        # First pass: openai requested.  Phrase `openai_fail_index`
        # fails over to piper (mimics the production behaviour); the
        # others succeed with openai.
        if backend == "openai":
            actual = "piper" if idx == openai_fail_index else "openai"
            _make_wav(out_path)
            call_log.append((call_counter["n"], actual))
            return actual
        # Second pass (the recovery): backend == "piper" requested
        # directly, write a piper wav.
        _make_wav(out_path)
        call_log.append((call_counter["n"], "piper"))
        return "piper"

    script = [
        {"speak": f"Phrase {i}", "highlight": [f"id_{i}"]}
        for i in range(4)
    ]
    out_wav = tmp_path / "out.wav"

    with patch.object(narrate, "voice_available", return_value=True), \
         patch.object(narrate, "_synthesize_phrase", side_effect=stub_synth_phrase):
        narrate.synthesize_script(script, str(out_wav))

    # 4 phrases × first pass + 4 phrases × recovery pass = 8 calls.
    assert len(call_log) == 8, call_log
    # First 4 calls: mixed voices (3 openai + 1 piper).
    first_pass_backends = [b for _, b in call_log[:4]]
    assert first_pass_backends.count("openai") == 3, first_pass_backends
    assert first_pass_backends.count("piper") == 1, first_pass_backends
    # Second 4 calls: all piper (the uniformity recovery).
    second_pass_backends = [b for _, b in call_log[4:]]
    assert all(b == "piper" for b in second_pass_backends), second_pass_backends


def test_all_openai_success_no_resynth_with_piper(tmp_path, monkeypatch):
    """Sanity check: when OpenAI succeeds for every phrase, we do NOT
    redo synth with piper.  Voice stays openai throughout."""
    monkeypatch.setenv("SEVIM_TTS_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    backends_seen: list[str] = []

    def stub_synth_phrase(text: str, out_path: Path, backend: str) -> str:
        backends_seen.append(backend)
        _make_wav(out_path)
        return "openai" if backend == "openai" else "piper"

    script = [{"speak": "Phrase A", "highlight": None},
              {"speak": "Phrase B", "highlight": None}]
    out_wav = tmp_path / "out.wav"

    with patch.object(narrate, "voice_available", return_value=True), \
         patch.object(narrate, "_synthesize_phrase", side_effect=stub_synth_phrase):
        narrate.synthesize_script(script, str(out_wav))

    # All calls should have used openai backend; no piper recovery pass.
    assert backends_seen == ["openai", "openai"], backends_seen
