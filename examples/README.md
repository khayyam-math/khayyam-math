# Examples

## `three_tiers.py`: the three ways to use Khayyam Math, and where the data comes from

```bash
python examples/three_tiers.py
```

Runs anywhere. **Tier 3 always works** (no key, no network). Tiers 1 and 2
run only if `OPENAI_API_KEY` is set, otherwise they explain themselves and
skip.

There are two distinct "libraries" in this package, and the difference is the
whole story:

| Tier | How you call it | What runs | Where the figure comes from |
|---|---|---|---|
| **1: thin client** | `from khayyam_math import KhayyamMath`, then `KhayyamMath().generate(prompt)` | One model call, then SVG + narration are parsed from the reply. No deterministic routing, no retry loop, no TTS. | The model **you** configure. Default is OpenAI **gpt-4o** via **your** `OPENAI_API_KEY` (i.e. `api.openai.com`). |
| **2: full engine** | `from studio.express import express_figure` | The actual pipeline khayyammath.com runs: a deterministic route cascade, then an LLM-SVG fallback, then vision review/retry and layout repair. | A **local deterministic template** when one matches the prompt, otherwise the model you configured (gpt-4o by default). |
| **3: deterministic / offline** | `express_figure(..., api_key="")` | A local template only (graph via Graphviz, plot via matplotlib, NP-proof, reduction, and so on). | **Pure local code.** No model, no key, no network. |

### Key facts the script demonstrates

- **The information is not served from our server.** The package never calls
  `khayyammath.com` to generate a figure. The only mention of that URL in the
  whole `khayyam_math` package is a docstring. Tier 3 proves a figure can be
  produced with an empty API key and zero network.
- **The default "intelligence" is OpenAI's gpt-4o**, running on OpenAI's
  servers, billed to your key. Figure assembly, layout repair, and math
  verification (SymPy, Z3, ortools, Graphviz, matplotlib) all run **locally
  in-process**.
- **The fine-tuned Qwen model is opt-in and self-hosted.** Use
  `KhayyamMath(provider="qwen")` (downloads the LoRA from Hugging Face, runs
  on your own GPU) or `provider="qwen-vllm"` (talks to a vLLM endpoint you
  run). Our production fine-tuned model on AWS is not reachable by the package.

### Switching backends (Tier 1)

```python
from khayyam_math import KhayyamMath

KhayyamMath()                                              # OpenAI gpt-4o (needs OPENAI_API_KEY)
KhayyamMath(provider="qwen")                               # local LoRA on your GPU ([qwen] extra)
KhayyamMath(provider="qwen-vllm",
            base_url="http://localhost:8000/v1")           # remote vLLM you host
```

### Not shown in the script (but available)

- **Studio web app + live canvas viewer:** `python -m studio` (or `sevim-studio`)
- **MCP server for Claude / Cursor:** `sevim-mcp`

Neither contacts `khayyammath.com` to generate figures.
