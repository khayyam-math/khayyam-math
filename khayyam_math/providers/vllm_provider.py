"""Remote vLLM back-end.

Talks to a vLLM server running the OpenAI-compatible HTTP API.  This
is what production uses to serve the fine-tuned Qwen adapter at scale
(see the ``vllm serve --enable-lora`` command in the model card).

Requires only the ``openai`` SDK; no torch / transformers /
peft needed locally.
"""
from __future__ import annotations

from typing import Optional


class VLLMProvider:
    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        api_key: str = "EMPTY",
    ) -> None:
        if not base_url:
            raise ValueError(
                "qwen-vllm provider needs base_url=...  or "
                "KHAYYAM_VLLM_URL env var pointing at a running vLLM "
                "server, e.g. http://localhost:8000/v1"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "The vLLM provider needs the 'openai' package.  "
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
        )
        return (resp.choices[0].message.content or "").strip()
