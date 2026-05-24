"""Khayyam Math — interactive Gradio demo.

The visitor supplies THEIR OWN OpenAI API key (or HF token for the
Qwen path).  The Space stores nothing — no `OPENAI_API_KEY` Space
secret, no logging of any key, no persistence across requests.  Each
call constructs a one-shot client from the in-memory key the visitor
just typed, runs the inference, returns the result, discards the
client.

Hardware: free CPU.  The OpenAI path is fast because inference happens
at OpenAI; the local Qwen path is unusably slow on CPU and is left in
the UI only as a placeholder until the Space is upgraded to a GPU
tier.
"""
from __future__ import annotations

import json
import re
import traceback
from html import escape

import gradio as gr

from khayyam_math import KhayyamMath


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

QWEN_MODEL_ID  = "khayyam-math/khayyam-math-qwen2.5-7b-v5.1"
DEFAULT_OPENAI = "gpt-4o"

EXAMPLES = [
    ["Solve x^2 - 5x + 6 = 0"],
    ["Show the unit circle with 30, 45 and 60 degrees marked"],
    ["Visualise the chain rule for f(x) = sin(x^2)"],
    ["Draw a Venn diagram for A union B intersect C"],
    ["Compute the determinant of the 3x3 matrix [[1,2,3],[0,1,4],[5,6,0]]"],
    ["Illustrate the Pythagorean theorem with a 3-4-5 triangle"],
]

_SVG_RE = re.compile(r"<svg[\s\S]*?</svg>", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Inference handler
# ---------------------------------------------------------------------------

def generate(prompt: str, provider: str, user_openai_key: str,
             user_hf_token: str) -> tuple[str, str, str]:
    """Return (svg_html, narration_md, debug_json) for the Gradio UI."""
    if not prompt or not prompt.strip():
        return ("", "_(enter a prompt above and click_ **Generate**_)_", "")

    # Refuse to run if the visitor hasn't supplied the required secret
    # for their chosen provider.  This Space NEVER carries either secret
    # on the server side.
    if provider == "openai" and not (user_openai_key or "").strip():
        return (
            "<div style='padding:1em;color:#b00'>"
            "You must paste your own OpenAI API key in the box above. "
            "This Space does not provide one and will not run otherwise."
            "</div>",
            "_(no narration — provide an OpenAI key to use this provider)_",
            "",
        )
    if provider == "qwen" and not (user_hf_token or "").strip():
        return (
            "<div style='padding:1em;color:#b00'>"
            "Loading the Qwen LoRA adapter from Hugging Face needs a read "
            "token. Paste yours in the 'Your HF token' box above."
            "</div>",
            "_(no narration — provide an HF token to use this provider)_",
            "",
        )

    # Build a fresh client per call.  No global key state, nothing
    # cached, no secret survives this function's return.
    try:
        if provider == "openai":
            client = KhayyamMath(
                provider="openai",
                model=DEFAULT_OPENAI,
                api_key=user_openai_key.strip(),
            )
        else:
            client = KhayyamMath(
                provider="qwen",
                model=QWEN_MODEL_ID,
                hf_token=user_hf_token.strip(),
            )
        result = client.generate(prompt.strip())
    except Exception as exc:  # noqa: BLE001 — surfaced to the user
        tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        return (
            f"<div style='padding:1em;color:#b00'>Error: {escape(tb)}</div>",
            "_(no narration)_",
            "",
        )
    finally:
        # Drop the client reference promptly so the API key reference
        # falls out of scope.  Python's GC will collect on the next pass.
        try:
            del client
        except NameError:
            pass

    svg = result.svg or ""
    if not svg and result.raw:
        m = _SVG_RE.search(str(result.raw))
        if m:
            svg = m.group(0)

    svg_html = svg or (
        "<div style='padding:1em;color:#888;font-style:italic'>"
        "Model emitted no SVG. See debug output below for details."
        "</div>"
    )

    if result.narration:
        bullet = lambda p: f"- {p.get('speak', '').strip()}"  # noqa: E731
        narration_md = "\n".join(
            bullet(p) for p in result.narration if p.get("speak")
        )
    else:
        narration_md = "_(no narration emitted)_"

    debug = {
        "provider":          result.provider,
        "model":             result.model,
        "problem_statement": result.problem_statement,
        "solution":          result.solution,
        "math_claims":       result.math_claims,
    }
    return svg_html, narration_md, json.dumps(debug, indent=2)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

INTRO = """
# Khayyam Math — interactive demo

Type a math prompt; the system emits a structured figure spec
(SVG + phrase-timed narration + symbolic math claims). Source code,
model card, and the full production deployment:

- 🌐 Live product: **[khayyammath.com](https://khayyammath.com)** *(no key needed; uses our infra)*
- 📦 Source: [github.com/khayyam-math/khayyam-math](https://github.com/khayyam-math/khayyam-math)
- 🤗 Fine-tuned Qwen 2.5-7B v5.1: [model card](https://huggingface.co/khayyam-math/khayyam-math-qwen2.5-7b-v5.1)

### ⚠️ Bring your own key

This Space is hosted on the free CPU tier and **does not provide
any API keys**. You must paste your own:

- **OpenAI key** — required for the GPT-4o provider. Get one at
  [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
  Each call costs you a few US cents at GPT-4o rates.
- **HuggingFace read token** — required for the Qwen provider while
  the model repo is private. Get one at
  [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
  (role: Read).

Neither key leaves your browser session and the Space stores
nothing — every call constructs a one-shot client, runs the
inference, and discards the client. We never log, persist, or
transmit your key anywhere except to the provider's own API
endpoint.

### ⚠️ Qwen on free CPU is unusably slow

The local `qwen` provider has to download Qwen 2.5-7B (~14 GB) and
the LoRA adapter (~80 MB) on first call, then run inference on CPU
— each call takes **5–10 minutes**. Treat the option as "available
but parked" until the Space is upgraded to a T4 GPU tier (this is
a one-click change in `Settings → Hardware` for the Space owner;
cost is roughly \\$15–40/month with sleep enabled). **The OpenAI
provider is the recommended path on this Space.**
"""

with gr.Blocks(title="Khayyam Math — interactive demo",
               theme=gr.themes.Soft()) as demo:
    gr.Markdown(INTRO)

    with gr.Row():
        with gr.Column(scale=2):
            prompt_box = gr.Textbox(
                label="Math prompt",
                placeholder="e.g. Solve x^2 - 5x + 6 = 0",
                lines=2,
            )
            provider_box = gr.Radio(
                label="Provider",
                choices=["openai", "qwen"],
                value="openai",
                info=("openai = GPT-4o (recommended on CPU Space). "
                      "qwen = our fine-tuned Qwen 2.5-7B v5.1 LoRA "
                      "(unusably slow on CPU — needs GPU)."),
            )
            openai_key_box = gr.Textbox(
                label="Your OpenAI API key",
                placeholder="sk-...",
                type="password",
                info=("Required for the 'openai' provider. Get one at "
                      "https://platform.openai.com/api-keys. Not stored."),
            )
            hf_token_box = gr.Textbox(
                label="Your HuggingFace read token",
                placeholder="hf_... (optional unless using 'qwen')",
                type="password",
                info=("Required for the 'qwen' provider while the model "
                      "is private. Role 'Read' is enough. Not stored."),
            )
            go_btn = gr.Button("Generate", variant="primary")
            gr.Examples(
                examples=EXAMPLES,
                inputs=[prompt_box],
                label="Try one of these (you still need to type the key above)",
            )
        with gr.Column(scale=3):
            svg_out = gr.HTML(label="Figure")
            narration_out = gr.Markdown(label="Narration")
            with gr.Accordion("Debug — raw structured fields", open=False):
                debug_out = gr.Code(language="json", label="")

    go_btn.click(
        fn=generate,
        inputs=[prompt_box, provider_box, openai_key_box, hf_token_box],
        outputs=[svg_out, narration_out, debug_out],
    )

    gr.Markdown(
        """
        ---
        Built with the [`khayyam-math`](https://github.com/khayyam-math/khayyam-math)
        Python package. © 2026 Arash Kermani Kolankeh · MIT-licensed.

        **Security note:** the source for this Space is in `app.py`
        of this very repo (click the **Files** tab above) — you can
        verify that no API key is read from a server-side env var or
        secret, and that the call site forwards the key only to
        OpenAI's / HF's own endpoints.
        """
    )

if __name__ == "__main__":
    demo.launch()
