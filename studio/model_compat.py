"""GPT-5 / o-series API compatibility shim — installed process-wide.

GPT-5 and o-series models differ from gpt-4o at the API boundary: they
reject `max_tokens` (require `max_completion_tokens`) and any `temperature`
other than the default 1, and the reasoning variants bill hidden reasoning
tokens against the completion budget (so a small budget can starve the
visible output to empty).

The codebase builds chat-completions payloads in well over a dozen places
(express.py plus every template route: fdl, graphviz, matplotlib, symbolic,
process, panels, sequential, algorithm_trace, graph_homomorphism,
figure_ground_truth, router, …) and app.py.  Patching each by hand is how a
missed site reaches production as a 500 ("Unsupported parameter:
'max_tokens'").  Instead we normalise centrally: `adapt_payload` does the
transform, and `install()` monkeypatches `httpx.AsyncClient.post`/`.stream`
so EVERY outbound request whose JSON body targets a GPT-5/o-series model is
fixed automatically — current sites and any added later.

The patch is tightly guarded: it only touches a JSON dict whose `model`
names a GPT-5/o-series model, so gpt-4o / gpt-4o-mini calls and any
non-OpenAI httpx traffic pass through untouched.
"""
from __future__ import annotations

import os
from typing import Any

_GPT5_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# OpenAI shut down the whole `*-chat-latest` line on 2026-08-10 (gpt-5-,
# gpt-5.1-, gpt-5.2-, gpt-5.3-chat-latest all 404 with
# "has been deprecated").  Those were the non-reasoning chat tunes, so the
# old "is it a reasoning model?" test — `"chat-latest" not in model` — now
# answers yes for every model that still exists.  Reasoning is no longer
# something we can dodge by model choice; it has to be switched off per
# call, and `reasoning_effort: "none"` does exactly that on the whole
# GPT-5.4/5.5/5.6 line.
#
# It matters on the hot path: the same Pythagoras SVG prompt measured
# 5.7 s on gpt-5.6-luna with reasoning off against 13.5 s at effort=low,
# and the interactive tutor budgets ~7 s for a figure.  So the models we
# put on the live generation/review path run with reasoning off, and only
# the escalated final-retry auditor pays for it.
_NO_REASONING_DEFAULT = "gpt-5.6-luna"


def is_gpt5_family(model: str) -> bool:
    return (model or "").lower().startswith(_GPT5_PREFIXES)


def reasoning_effort_for(model: str) -> str:
    """The `reasoning_effort` a given model should default to.

    Hot-path models (`SEVIM_GPT5_NO_REASONING_MODELS`, a comma-separated
    list, default `gpt-5.6-luna`) get `none`; everything else gets
    `SEVIM_GPT5_REASONING_EFFORT` (default `low`).
    """
    raw = os.environ.get("SEVIM_GPT5_NO_REASONING_MODELS",
                         _NO_REASONING_DEFAULT)
    hot = {m.strip().lower() for m in raw.split(",") if m.strip()}
    if (model or "").lower() in hot:
        return "none"
    return os.environ.get("SEVIM_GPT5_REASONING_EFFORT", "low")


def adapt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise a chat-completions payload for the model it targets.

    No-op for gpt-4o / gpt-4o-mini.  For GPT-5 / o-series:
      • rename `max_tokens` → `max_completion_tokens`;
      • drop `temperature` (only the default 1 is supported);
      • pin `reasoning_effort` per `reasoning_effort_for()` — `none` on the
        interactive path, `low` elsewhere — unless the caller set one;
      • when reasoning is actually on, floor `max_completion_tokens` so the
        hidden reasoning tokens can't starve the visible output.
    """
    if not isinstance(payload, dict):
        return payload
    model = (payload.get("model") or "").lower()
    if not is_gpt5_family(model):
        return payload
    p = dict(payload)
    if "max_tokens" in p:
        p["max_completion_tokens"] = p.pop("max_tokens")
    p.pop("temperature", None)
    p.setdefault("reasoning_effort", reasoning_effort_for(model))
    if p["reasoning_effort"] != "none":
        mct = p.get("max_completion_tokens")
        if isinstance(mct, int) and mct < 4096:
            p["max_completion_tokens"] = 4096
    return p


def _should_adapt(body: Any) -> bool:
    return (isinstance(body, dict)
            and isinstance(body.get("model"), str)
            and is_gpt5_family(body["model"]))


def install() -> None:
    """Monkeypatch httpx so every GPT-5-bound chat payload is normalised.
    Idempotent and safe to call multiple times."""
    import httpx
    if getattr(httpx.AsyncClient, "_sevim_gpt5_patched", False):
        return
    _orig_post = httpx.AsyncClient.post
    _orig_stream = httpx.AsyncClient.stream

    async def _post(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        if _should_adapt(kwargs.get("json")):
            kwargs["json"] = adapt_payload(kwargs["json"])
        return await _orig_post(self, url, *args, **kwargs)

    def _stream(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        if _should_adapt(kwargs.get("json")):
            kwargs["json"] = adapt_payload(kwargs["json"])
        return _orig_stream(self, method, url, *args, **kwargs)

    httpx.AsyncClient.post = _post           # type: ignore[method-assign]
    httpx.AsyncClient.stream = _stream       # type: ignore[method-assign]
    httpx.AsyncClient._sevim_gpt5_patched = True  # type: ignore[attr-defined]


# Install on import so any module that imports this (express, app) activates
# the shim for the whole process.
install()
