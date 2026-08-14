#!/usr/bin/env python3
"""Preflight: are the models this deployment pins still callable?

OpenAI retires models on its own schedule, with no signal in our source
tree.  On 2026-08-10 it shut down the whole ``*-chat-latest`` line, which
was the generation *and* per-attempt-review model here; every LLM call in
production started returning ``404 … has been deprecated`` and the site
served no figures until someone read a log.  Nothing in the test suite or
the quality gate could see it coming, because the model id was still
perfectly valid *text*.

So this check asks the API directly, before a deploy goes out:

  * the chat-role models get a real 1-token completion — the model
    *listing* is not authoritative (``gpt-5.3-chat-latest`` was still
    listed by ``GET /v1/models`` days after it started 404-ing);
  * the TTS model is checked against the listing (a synthesis call would
    cost real money for no extra signal).

Model ids are read out of whichever deployment config files exist in
the tree, so a branch that carries only one of them is covered just the
same.  Missing files are skipped, not an error.

Exit codes: 0 = all reachable (or skipped for lack of a key), 1 = at
least one model is dead.  Run standalone with::

    uv run python infra/check_models.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_FILES = (
    _ROOT / "infra" / "sevim_stack.py",
    _ROOT / "deploy" / "selfhost" / "compose.yml",
)

# SEVIM_VLLM_MODEL / SEVIM_FORCE_ACTIVE_MODEL / SEVIM_REVIEW_MODEL /
# SEVIM_REVIEW_ESCALATE_MODEL — every var whose value is a model id, in
# either the CDK dict (`"KEY": "value"`) or the compose mapping
# (`KEY: "value"`).
_MODEL_VAR = re.compile(
    r'"?(SEVIM_[A-Z0-9_]*MODEL)"?\s*:\s*"([^"]+)"')
_TTS_VARS = {"SEVIM_TTS_MODEL"}
# Vars that name a *set* of models rather than one, or that aren't models.
_SKIP_VARS = {"SEVIM_FORCE_ACTIVE_MODEL_UNSET"}

_API = "https://api.openai.com/v1"


def collect_models() -> tuple[set[str], set[str]]:
    """Return (chat_models, tts_models) pinned by the deployment configs."""
    chat: set[str] = set()
    tts: set[str] = set()
    for path in _CONFIG_FILES:
        if not path.exists():
            continue
        for var, value in _MODEL_VAR.findall(path.read_text()):
            if var in _SKIP_VARS or not value or "${" in value:
                continue
            (tts if var in _TTS_VARS else chat).add(value)
    return chat, tts


def _post(path: str, key: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{_API}{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def _listed_models(key: str) -> set[str]:
    req = urllib.request.Request(
        f"{_API}/models", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return {m["id"] for m in json.loads(r.read()).get("data", [])}


def main() -> int:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    chat, tts = collect_models()
    if not chat and not tts:
        print("[check_models] No model ids found in the deployment configs — "
              "check the config paths in this script.")
        return 1
    if not key:
        print("[check_models] ⚠️  OPENAI_API_KEY unset — skipping the live "
              f"reachability check for: {', '.join(sorted(chat | tts))}")
        return 0

    dead: list[str] = []
    for model in sorted(chat):
        status, body = _post("/chat/completions", key, {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_completion_tokens": 16,
        })
        if status == 200:
            print(f"[check_models] ✅ {model}")
            continue
        message = (body.get("error") or {}).get("message", f"HTTP {status}")
        # A 400 about parameters means the model is alive and merely fussy
        # about this probe; only "unknown/retired model" is a real failure.
        if status == 404 or "deprecat" in message.lower():
            print(f"[check_models] ❌ {model}: {message}")
            dead.append(model)
        else:
            print(f"[check_models] ✅ {model} (reachable; probe rejected: "
                  f"{message})")

    if tts:
        listed = _listed_models(key)
        for model in sorted(tts):
            if model in listed:
                print(f"[check_models] ✅ {model} (listed)")
            else:
                print(f"[check_models] ❌ {model}: not in GET /v1/models")
                dead.append(model)

    if dead:
        print()
        print("[check_models] Configured model(s) no longer usable: "
              f"{', '.join(dead)}")
        print("[check_models] Pick replacements at "
              "https://platform.openai.com/docs/deprecations and update "
              "the deployment config listed above.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
