"""Lightweight content filter for public-facing Sevim.

A first-pass denylist: catches prompts that are clearly off-topic
(adult content, hate, self-harm, attempts to extract the system
prompt, prompt-injection patterns).  Cheap, regex-based, no external
API call — designed to be the *first* gate before we burn an OpenAI
call on a request that's clearly out of scope.

Production deployments should layer OpenAI's moderation endpoint or a
dedicated content-classifier on top.

Off by default.  Enable with ``SEVIM_CONTENT_FILTER=1``.
"""
from __future__ import annotations

import os
import re


def is_filtering() -> bool:
    return os.environ.get("SEVIM_CONTENT_FILTER", "0") not in ("0", "", "false", "no")


# Regexes are deliberately conservative — better to let edge cases
# through to the LLM than to falsely reject legitimate math queries.
_DENY_PATTERNS: list[tuple[str, str]] = [
    (r"\b(ignore|disregard).{0,30}(previous|prior|above).{0,30}(instructions?|prompt|rules?)\b",
     "prompt injection attempt"),
    (r"\b(reveal|show|print|leak).{0,30}(system|developer).{0,30}(prompt|instructions?|rules?)\b",
     "system-prompt extraction attempt"),
    (r"\b(api[\s_-]?key|openai[\s_-]?key|secret[\s_-]?key)\b",
     "request involving API keys"),
    # Adult / explicit content
    (r"\b(porn|sexual|erotic|nsfw)\b", "adult content"),
    # Self-harm
    (r"\b(suicide|self[\s-]?harm|kill myself)\b", "self-harm content"),
    # Hate speech (rough; expand as needed)
    (r"\bracial slurs?\b", "hate speech"),
]


def check_prompt(text: str) -> str | None:
    """Return ``None`` when the prompt is allowed, or a human-readable
    reason string when it should be rejected."""
    if not is_filtering():
        return None
    if not text or not text.strip():
        return None
    lowered = text.lower()
    for pattern, reason in _DENY_PATTERNS:
        if re.search(pattern, lowered):
            return f"Prompt rejected: {reason}.  Sevim is for math/concept figures."
    return None
