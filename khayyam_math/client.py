"""Provider-agnostic client.

Picks the right back-end based on the ``provider`` argument (or the
``KHAYYAM_PROVIDER`` env var) and forwards calls to it.  All providers
return a :class:`GenerationResult` whose ``.svg``, ``.narration``,
``.raw`` attributes are normalised across back-ends.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .prompts import DEFAULT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class GenerationResult:
    """Normalised output across all providers.

    Attributes:
      svg:        Extracted SVG string (may be empty if the model
                  emitted a malformed response).
      narration:  List of ``{"speak": str, "highlight": [str, ...]}``
                  phrases parsed from the response.
      problem_statement: Verbatim restatement of the problem as the
                  model understood it.
      solution:   The model's worked-out answer text.
      math_claims: List of ``{"a": str, "b": str}`` symbolic identity
                  claims the figure depends on (downstream code can
                  feed these to SymPy / Z3 / Lean for verification).
      raw:        Full JSON dict the model emitted.
      provider:   Which back-end produced this result.
      model:      Which model id was used.
    """
    svg: str = ""
    narration: list[dict[str, Any]] = field(default_factory=list)
    problem_statement: str = ""
    solution: str = ""
    math_claims: list[dict[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_DEFAULT_MODELS = {
    "openai":    "gpt-4o",
    "qwen":      "khayyam-math/khayyam-math-qwen2.5-7b-v4",
    "qwen-vllm": "khayyam-v4",
}


class KhayyamMath:
    """One-class entry point.  All provider differences live in the
    ``providers/`` subpackage; callers should never need to know which
    back-end is being used."""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        device: str = "auto",
        **kwargs: Any,
    ) -> None:
        # Provider precedence:  arg > env > "openai" default.
        provider = provider or os.environ.get("KHAYYAM_PROVIDER") or "openai"
        if provider not in _DEFAULT_MODELS:
            raise ValueError(
                f"Unknown provider {provider!r}.  "
                f"Choose one of: {sorted(_DEFAULT_MODELS)}."
            )

        model = (
            model
            or os.environ.get("KHAYYAM_MODEL")
            or _DEFAULT_MODELS[provider]
        )

        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

        # Lazy backend construction so that ``import khayyam_math`` is
        # cheap even when the heavy ML extras aren't installed.
        if provider == "openai":
            from .providers.openai_provider import OpenAIProvider
            self._backend = OpenAIProvider(
                model=model,
                api_key=api_key or os.environ.get("OPENAI_API_KEY"),
                base_url=base_url,
                **kwargs,
            )
        elif provider == "qwen":
            from .providers.qwen_provider import QwenProvider
            self._backend = QwenProvider(model=model, device=device, **kwargs)
        elif provider == "qwen-vllm":
            from .providers.vllm_provider import VLLMProvider
            self._backend = VLLMProvider(
                model=model,
                base_url=base_url or os.environ.get("KHAYYAM_VLLM_URL"),
                api_key=api_key or os.environ.get("KHAYYAM_VLLM_KEY", "EMPTY"),
                **kwargs,
            )

    # -- public surface ----------------------------------------------------

    def generate(
        self,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> GenerationResult:
        """Run the prompt through the active provider and parse the
        response into a :class:`GenerationResult`."""
        raw_text = self._backend.complete(
            system=self.system_prompt,
            user=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        parsed = _parse_response(raw_text)
        return GenerationResult(
            svg=parsed.get("svg", ""),
            narration=parsed.get("narration", []) or [],
            problem_statement=parsed.get("problem_statement", "") or "",
            solution=parsed.get("solution", "") or "",
            math_claims=parsed.get("math_claims", []) or [],
            raw=parsed,
            provider=self.provider,
            model=self.model,
        )

    def generate_figure(self, prompt: str, **kw: Any) -> str:
        """Convenience: return just the SVG string."""
        return self.generate(prompt, **kw).svg


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_response(text: str) -> dict[str, Any]:
    """Tolerant JSON-from-LLM parser.

    Tries the raw string first, then any ```json fenced block, then the
    largest balanced ``{...}`` substring.  Returns ``{}`` if nothing
    parses — callers should treat that as an empty/failed response.
    """
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return {}
