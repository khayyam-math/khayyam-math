"""Wiring test for the force-visual route in ``_stream_vllm_chat``.

``test_visual_intent`` pins the detector.  This file pins what the
detector is *for*: that an explicit "draw me X" actually reaches the
LLM as a pinned ``tool_choice``, and that the canvas gets drawn even
if the provider ignores the pin and streams prose back.

Both the LLM call and the (expensive) express pipeline are faked —
this is about control flow, not figure quality.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import studio.app as app


def _sse(chunks: list[dict]) -> bytes:
    """Encode chat-completions deltas as an SSE body."""
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return (body + "data: [DONE]\n\n").encode()


_TEXT_ONLY = _sse([
    {"choices": [{"delta": {"content": "Sure! The unit circle is "}}]},
    {"choices": [{"delta": {"content": "the set of points at distance 1."},
                  "finish_reason": "stop"}]},
])


def _install_fake_llm(monkeypatch, body: bytes, captured: list[dict]):
    """Point studio.app's httpx at a transport that records payloads."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=body,
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*a, **kw):
        kw.pop("transport", None)
        return real_client(*a, transport=transport, **kw)

    monkeypatch.setattr(app.httpx, "AsyncClient", factory)


def _install_fake_tool(monkeypatch, calls: list[dict]):
    """Replace the express pipeline with a recorder."""
    async def fake_execute_tool(name, args, **kwargs):
        calls.append({"name": name, "args": args})
        return {"canvas_id": "cnv_test", "title": "t", "svg": "<svg/>"}

    monkeypatch.setattr(app, "_execute_tool", fake_execute_tool)


async def _drain(req) -> list[dict]:
    return [ev async for ev in app._stream_vllm_chat(req, user="u@example.com")]


def _req(text: str):
    return app.ChatReq(user=text, history=[], session_id="sess_test")


def test_explicit_ask_pins_tool_choice(monkeypatch):
    payloads: list[dict] = []
    calls: list[dict] = []
    _install_fake_llm(monkeypatch, _TEXT_ONLY, payloads)
    _install_fake_tool(monkeypatch, calls)

    asyncio.run(_drain(_req("Draw the unit circle with sin and cos marked")))

    assert payloads, "no LLM call was made"
    assert payloads[0]["tool_choice"] == {
        "type": "function", "function": {"name": "sevim_express"},
    }


def test_prose_reply_to_explicit_ask_still_draws(monkeypatch):
    """The bug being fixed: model answers in text, canvas stays empty."""
    payloads: list[dict] = []
    calls: list[dict] = []
    _install_fake_llm(monkeypatch, _TEXT_ONLY, payloads)
    _install_fake_tool(monkeypatch, calls)

    asyncio.run(_drain(_req("Show me how the chain rule works")))

    assert calls, "explicit visual ask produced no figure"
    assert calls[0]["name"] == "sevim_express"
    # The synthesised call carries the user's literal message, so the
    # deterministic template classifier still sees the real phrasing.
    assert "chain rule" in calls[0]["args"]["prompt"]


def test_pin_draws_exactly_once(monkeypatch):
    """A pinned tool_choice must not become an endless redraw loop."""
    payloads: list[dict] = []
    calls: list[dict] = []
    _install_fake_llm(monkeypatch, _TEXT_ONLY, payloads)
    _install_fake_tool(monkeypatch, calls)

    events = asyncio.run(_drain(_req("Plot y = x^3 - 2x")))

    assert len(calls) == 1, "figure was drawn more than once"
    assert json.loads(events[-1]["data"])["stop_reason"] == "express_complete"


def test_clarifying_followup_stays_chat_only(monkeypatch):
    payloads: list[dict] = []
    calls: list[dict] = []
    _install_fake_llm(monkeypatch, _TEXT_ONLY, payloads)
    _install_fake_tool(monkeypatch, calls)

    asyncio.run(_drain(_req("why is that true?")))

    assert payloads[0]["tool_choice"] == "auto"
    assert calls == [], "a clarifying follow-up must not force a redraw"


def test_kill_switch_restores_auto(monkeypatch):
    monkeypatch.setenv("SEVIM_FORCE_VISUAL_ROUTE", "0")
    payloads: list[dict] = []
    calls: list[dict] = []
    _install_fake_llm(monkeypatch, _TEXT_ONLY, payloads)
    _install_fake_tool(monkeypatch, calls)

    asyncio.run(_drain(_req("Draw the unit circle")))

    assert payloads[0]["tool_choice"] == "auto"
    assert calls == []
