# Sevim — figure runtime for tool-using LLMs

> A **structured spec → live canvas + audio narration** runtime.
> The LLM (Claude, GPT-4o, Gemini, local Qwen via vLLM) builds figures
> by calling tools; Sevim handles deterministic layout, SVG rendering,
> piper-TTS narration, and the live browser viewer.

## What Sevim is (v0.3+)

A figure runtime with three things in the box:

1. **A structured tool surface** (`sevim_open`, `sevim_plan`, `sevim_apply`,
   `sevim_narrate`, …) exposed via MCP for Claude Code / ChatGPT / Gemini,
   and via the Studio web app for direct OpenAI / vLLM use.
2. **Deterministic layout + SVG rendering**: coordinate-honoring layout
   (`s4_geo_layout`), Sugiyama for concept diagrams (`s4_layout`),
   constraint-based caption placement, label-collision avoidance,
   overlap-detector critic (`overlap`).
3. **Phrase-timed audio narration**: piper TTS synthesises a WAV and a
   manifest of `(phrase, start_s, highlight_id)`; the canvas viewer
   reveals + highlights elements in lockstep with the spoken track.

## What Sevim was (v0.1, v0.2)

Earlier versions included an in-house NLP pipeline (S1 parse → S2
relation extraction → S2b LLM improvement → S3 map → S4 layout →
S5 render) that ingested English sentences and produced diagrams.
That layer was removed in v0.3 — modern tool-using LLMs produce
structured graph specs directly with much higher quality than the
in-house extractor delivered, and the NLP code (~10K lines,
sentence-transformers + spaCy deps) was no longer pulling its weight.

If you need the NLP pipeline, pin to `0.2.x` or check out the `v0.2`
git tag.

## Quick start

### Use it via Claude Code (MCP)

```bash
pip install -e .

# Register the MCP server with Claude Code (user scope)
claude mcp add sevim --scope user -- \
  uv run --directory /path/to/sevim_plugin python -m mcp_server

# Verify
claude mcp list
```

Then ask Claude Code to draw something — it'll call `sevim_open`,
`sevim_plan`, `sevim_apply`, `sevim_narrate` on its own and the live
canvas auto-opens in your browser.

### Use it via Studio (any OpenAI-compatible LLM)

```bash
# OpenAI gpt-4o
SEVIM_STUDIO_BACKEND=vllm \
SEVIM_VLLM_URL=https://api.openai.com/v1 \
SEVIM_VLLM_MODEL=gpt-4o \
OPENAI_API_KEY=sk-... \
sevim-studio

# Local vLLM with Qwen2.5
# (vLLM must be started with --enable-auto-tool-choice --tool-call-parser hermes)
SEVIM_STUDIO_BACKEND=vllm \
SEVIM_VLLM_URL=http://127.0.0.1:8000/v1 \
SEVIM_VLLM_MODEL=Qwen/Qwen2.5-32B-Instruct-AWQ \
sevim-studio

# Original Anthropic backend
SEVIM_STUDIO_BACKEND=anthropic ANTHROPIC_API_KEY=sk-ant-... sevim-studio
```

Open `http://127.0.0.1:7777/studio` (or whatever `SEVIM_HTTP_PORT`
resolves to). Type a prompt; the figure renders in the right pane and
the narration speaks when you click on the canvas.

## The tool API

| Tool | Purpose |
|---|---|
| `sevim_open(math_mode, animate, prelude, …)` | Create a canvas. `prelude` is the 50–150 word problem statement spoken on open. |
| `sevim_plan(nodes, edges, layout)` | Compute math-coordinate positions for a structured graph (`auto` / `constraint_clusters` / `radial`). Removes coordinate-picking from the LLM's job. |
| `sevim_apply(canvas_id, ops)` | Batched mutation: `add_node`, `add_edge`, `add_caption`, `remove`. Each `add_node` carries the `(x, y)` from `sevim_plan`. |
| `sevim_narrate(canvas_id, script)` | Phrase-timed narration. Each phrase has `speak` text + an optional `highlight` id pointing at a node, edge, or caption. |
| `sevim_review(canvas_id)` | Renders the current canvas as PNG for vision-feedback. |
| `sevim_render` / `sevim_vocabulary` / `sevim_list_canvases` / `sevim_close` | Operational helpers. |

The standard 4-call workflow:

```
sevim_open(prelude="...")               # canvas + prelude audio
sevim_plan(nodes, edges)                # → math coordinates
sevim_apply(ops)                        # commit nodes/edges/captions
sevim_narrate(script)                   # phrase-timed walkthrough
```

## Architecture

```
                ┌──────────────┐
   LLM tool ───▶│ MCP / Studio │──┐
                └──────────────┘  │
                                  ▼
                         ┌─────────────────┐
                         │  service/       │   FastAPI: /canvas/{id}/view,
                         │  ├─ canvas.py   │   /state, /events (SSE),
                         │  └─ app.py      │   /intro.wav, /narration.wav
                         └────────┬────────┘
                                  │
                         ┌────────┴────────┐
                         │  sevim/         │   geometric_layout (s4_geo),
                         │  ├─ ir.py       │   render_svg (s5_render),
                         │  ├─ s4_geo_…    │   plan_layout, narrate,
                         │  ├─ s5_render   │   overlap critic
                         │  ├─ plan.py     │
                         │  └─ narrate.py  │
                         └─────────────────┘
```

## Persistence

Every canvas writes `state.json` (graph + manifest + metadata) and the
audio WAVs (`intro.wav`, `narration.wav`) to
`~/.local/share/sevim/canvases/<canvas_id>/`. URLs survive Studio
restarts — the registry restores from disk on first hit.

## Testing

```bash
uv run python -m pytest
```

The suite covers caption-overlap regressions, the layout planner, the
canvas API contract, and the math-label render path. CI-friendly: no
GPU, no API keys, no piper required.

## License + citation

See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
