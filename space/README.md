---
title: Khayyam Math — interactive demo
emoji: ➗
colorFrom: indigo
colorTo: yellow
sdk: gradio
sdk_version: "4.44.1"
app_file: app.py
pinned: true
license: mit
short_description: Live narrated math figures from a one-line prompt.
tags:
  - math
  - math-education
  - svg
  - figure-generation
  - diagram
  - lean4
  - z3
  - visualized-math-teaching
---

# Khayyam Math — interactive demo

Type a math prompt, get a custom SVG figure plus a phrase-timed
narrated walkthrough. Powered by the
[`khayyam-math`](https://github.com/khayyam-math/khayyam-math) Python
package; same engine as the production deployment at
**[khayyammath.com](https://khayyammath.com)**.

## ⚠️ Bring your own key

This Space is hosted on the free CPU tier and **does not provide any
API keys**. Paste your own OpenAI key (or HuggingFace read token for
the Qwen path) in the form. Neither key leaves your browser session
and the Space stores nothing — the `app.py` source is in this very
repo (Files tab), so you can verify.

## ⚠️ Qwen on free CPU is unusably slow

The local `qwen` provider loads Qwen 2.5-7B (~14 GB) plus our LoRA
adapter (~80 MB) and runs inference on CPU — each call takes 5–10
minutes. The OpenAI provider is the recommended path on this Space.
Upgrade to a T4 GPU in Settings → Hardware to make the Qwen path
practical (~\$15–40/month with sleep enabled).

## Source

- Engine: [github.com/khayyam-math/khayyam-math](https://github.com/khayyam-math/khayyam-math)
- Fine-tuned Qwen 2.5-7B v5.1: [model card](https://huggingface.co/khayyam-math/khayyam-math-qwen2.5-7b-v5.1)
- Full live product: [khayyammath.com](https://khayyammath.com)

MIT licence.
