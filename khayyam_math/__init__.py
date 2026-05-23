"""khayyam_math — the public Python API for Khayyam Math.

Programmatic access to the figure-generation engine that powers
https://khayyammath.com.  One call:

    from khayyam_math import KhayyamMath
    client = KhayyamMath(provider="qwen")
    svg = client.generate_figure("Solve x^2 - 5x + 6 = 0")

The ``provider`` argument switches between back-ends:

  * ``"openai"``      — calls the OpenAI API (default model: gpt-4o).
                       Requires ``OPENAI_API_KEY``.  Lightweight install.
  * ``"qwen"``        — loads the fine-tuned LoRA adapter from
                       Hugging Face and runs locally with
                       ``transformers`` + ``peft``.  Requires the
                       ``[qwen]`` extra (``pip install
                       khayyam-math[qwen]``) and a GPU for usable
                       throughput.
  * ``"qwen-vllm"``   — talks to a remote vLLM endpoint serving the
                       same adapter via the OpenAI-compatible API.
                       Lightweight install; ideal for production.

All three back-ends share the same ``generate_figure(prompt) -> str``
contract, so your application code does not care which one is in use.
"""
from .client import KhayyamMath, GenerationResult
from .version import __version__

__all__ = ["KhayyamMath", "GenerationResult", "__version__"]
