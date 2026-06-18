"""Tests for the GPT-5 / o-series payload adapter.

GPT-5 models reject `max_tokens` and `temperature != 1`; reasoning variants
also bill hidden reasoning tokens, so a small budget can starve the output.
The adapter normalises payloads so the same code can target gpt-4o,
gpt-5.3-chat-latest (fast generation) and gpt-5.5 (reasoning review).
"""
from __future__ import annotations

from studio.express import _adapt_payload_for_model as adapt


def test_gpt4o_unchanged():
    p = {"model": "gpt-4o", "max_tokens": 1800, "temperature": 0.2}
    out = adapt(p)
    assert out == p
    assert "max_tokens" in out and out["temperature"] == 0.2


def test_gpt4o_mini_unchanged():
    p = {"model": "gpt-4o-mini", "max_tokens": 1100, "temperature": 0.3}
    assert adapt(p) == p


def test_chat_latest_renames_and_drops_temperature():
    p = {"model": "gpt-5.3-chat-latest", "max_tokens": 16384,
         "temperature": 0.2, "messages": []}
    out = adapt(p)
    assert "max_tokens" not in out
    assert out["max_completion_tokens"] == 16384
    assert "temperature" not in out
    # chat tune is NOT a reasoning model -> no reasoning_effort injected
    assert "reasoning_effort" not in out


def test_reasoning_model_sets_effort_and_floors_budget():
    p = {"model": "gpt-5.5", "max_tokens": 1200, "temperature": 0.0,
         "messages": []}
    out = adapt(p)
    assert "max_tokens" not in out and "temperature" not in out
    assert out["reasoning_effort"] == "low"          # bounded latency
    assert out["max_completion_tokens"] == 4096       # floored so output isn't starved


def test_reasoning_model_keeps_large_budget():
    p = {"model": "gpt-5.5", "max_tokens": 16384}
    out = adapt(p)
    assert out["max_completion_tokens"] == 16384


def test_does_not_mutate_input():
    p = {"model": "gpt-5.5", "max_tokens": 1200, "temperature": 0.0}
    _ = adapt(p)
    assert p["max_tokens"] == 1200 and p["temperature"] == 0.0
