"""Smoke tests that don't require any provider credentials or weights.

The full integration tests live as opt-in suites; this file just
verifies the parser and the provider-dispatch glue.
"""
import os
import pytest

from khayyam_math import KhayyamMath, GenerationResult, __version__
from khayyam_math.client import _parse_response


def test_version_exported():
    assert isinstance(__version__, str) and __version__


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        KhayyamMath(provider="nope")


def test_openai_needs_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("KHAYYAM_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        KhayyamMath(provider="openai")


def test_vllm_needs_base_url(monkeypatch):
    monkeypatch.delenv("KHAYYAM_VLLM_URL", raising=False)
    with pytest.raises(ValueError, match="base_url"):
        KhayyamMath(provider="qwen-vllm")


def test_parser_plain_json():
    out = _parse_response('{"svg": "<svg/>", "narration": []}')
    assert out["svg"] == "<svg/>"


def test_parser_fenced_block():
    raw = '```json\n{"svg": "<svg/>"}\n```'
    assert _parse_response(raw)["svg"] == "<svg/>"


def test_parser_substring_fallback():
    raw = 'Sure, here is the spec:\n{"svg": "<svg/>"}\nLet me know if…'
    assert _parse_response(raw)["svg"] == "<svg/>"


def test_parser_invalid_returns_empty():
    assert _parse_response("not json at all") == {}


def test_generation_result_defaults():
    r = GenerationResult()
    assert r.svg == ""
    assert r.narration == []
    assert r.math_claims == []
