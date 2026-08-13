"""Tests for the GPT-5 / o-series payload adapter.

GPT-5 models reject `max_tokens` and `temperature != 1`, and they all bill
hidden reasoning tokens, so a small budget can starve the output.  Since the
non-reasoning `*-chat-latest` tunes were shut down (2026-08-10), the
interactive path gets its speed from `reasoning_effort: "none"` instead of
from the model choice.  The adapter normalises payloads so the same code can
target gpt-4o, gpt-5.6-luna (fast generation) and gpt-5.5 (reasoning review).
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


def test_hot_path_model_renames_drops_temperature_and_disables_reasoning():
    p = {"model": "gpt-5.6-luna", "max_tokens": 16384,
         "temperature": 0.2, "messages": []}
    out = adapt(p)
    assert "max_tokens" not in out
    assert out["max_completion_tokens"] == 16384
    assert "temperature" not in out
    # interactive path: reasoning off, so the ~7 s figure budget holds
    assert out["reasoning_effort"] == "none"


def test_hot_path_model_budget_not_floored():
    """With reasoning off there are no hidden tokens to starve the output,
    so a deliberately small budget must survive."""
    out = adapt({"model": "gpt-5.6-luna", "max_tokens": 700})
    assert out["max_completion_tokens"] == 700


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


def test_explicit_effort_is_respected():
    """A call site that asks for reasoning on a hot-path model keeps it."""
    out = adapt({"model": "gpt-5.6-luna", "max_tokens": 900,
                 "reasoning_effort": "medium"})
    assert out["reasoning_effort"] == "medium"
    assert out["max_completion_tokens"] == 4096   # reasoning on -> floored


def test_no_reasoning_set_is_env_configurable(monkeypatch):
    monkeypatch.setenv("SEVIM_GPT5_NO_REASONING_MODELS", "gpt-5.4-mini")
    assert adapt({"model": "gpt-5.4-mini"})["reasoning_effort"] == "none"
    assert adapt({"model": "gpt-5.6-luna"})["reasoning_effort"] == "low"


def test_does_not_mutate_input():
    p = {"model": "gpt-5.5", "max_tokens": 1200, "temperature": 0.0}
    _ = adapt(p)
    assert p["max_tokens"] == 1200 and p["temperature"] == 0.0


def test_httpx_interceptor_normalises_gpt5_payloads():
    """The process-wide shim must rewrite a GPT-5 payload on the way out,
    so a route file that forgot to call adapt_payload can't 500."""
    import asyncio
    import httpx
    from studio import model_compat
    model_compat.install()

    captured = {}

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    async def _fake_send(self, request, **kw):  # low-level, unpatched
        import json as _j
        captured.update(_j.loads(request.content))
        return httpx.Response(200, json=_FakeResp().json(), request=request)

    async def go():
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"choices": [{"message": {"content": "{}"}}]}))
        async with httpx.AsyncClient(transport=transport) as c:
            # a GPT-5 payload as a route file would build it
            await c.post("https://api.openai.com/v1/chat/completions",
                         json={"model": "gpt-5.6-luna",
                               "max_tokens": 700, "temperature": 0.2,
                               "messages": []})
        # the request that actually went out should be normalised
    asyncio.run(go())
    # Re-run capturing the outbound body via a transport that records it.
    seen = {}

    def _record(req):
        import json as _j
        seen.update(_j.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    async def go2():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_record)) as c:
            await c.post("https://api.openai.com/v1/chat/completions",
                         json={"model": "gpt-5.6-luna",
                               "max_tokens": 700, "temperature": 0.2,
                               "messages": []})
    asyncio.run(go2())
    assert "max_tokens" not in seen
    assert seen.get("max_completion_tokens") == 700
    assert "temperature" not in seen

    # gpt-4o payload must pass through untouched
    seen.clear()

    async def go3():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_record)) as c:
            await c.post("https://api.openai.com/v1/chat/completions",
                         json={"model": "gpt-4o", "max_tokens": 700,
                               "temperature": 0.2, "messages": []})
    asyncio.run(go3())
    assert seen.get("max_tokens") == 700 and seen.get("temperature") == 0.2
