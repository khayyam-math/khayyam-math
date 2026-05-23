"""OpenAI back-end.

Default model is ``gpt-4o``; override with the ``model`` constructor
argument or the ``KHAYYAM_MODEL`` env var.  Requires the ``openai``
SDK, which ships with the base ``khayyam-math`` install (no ML extras
needed).
"""
from __future__ import annotations

from typing import Optional


class OpenAIProvider:
    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        # Argument validation BEFORE the heavy import so users get the
        # clearer error message.
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set.  Pass api_key=... to "
                "KhayyamMath(...) or export OPENAI_API_KEY."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "The OpenAI provider needs the 'openai' package.  "
                "Install with: pip install khayyam-math"
            ) from exc

        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return (resp.choices[0].message.content or "").strip()
