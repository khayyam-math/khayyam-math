# SeVim plugin — session state

**Last update:** 2026-05-09
**Status:** Heavy iteration day on perf + UX + Studio.  Repo now hosted
at `git@github.com:arashkermaniprojects/sevim-plugin.git`; rollback
tag `success_1` marks the morning's known-good baseline.

---

## 2026-05-09 session — what landed

Commits (newest first):

* `a6f1302` — viewer: narration-synced fade-in (elements appear at
  their phrase's start_s, not on a fixed 0.4 s SMIL stagger) + MCP
  tool descriptions push toward sevim_apply
* `ae7f090` — studio: stream Anthropic so chat fills incrementally
* `b220967` — studio: bump max_tokens 4096 → 16384, surface max_tokens
* `6bbde91` — fix: sevim_apply now handles add_caption ops
* `b32a226` — fix: default transition phrase even when sevim_open omits it
* `2291656` — studio: defensive init so localStorage replay can't break send button
* `761a7dc` — studio: unify TTS, accept prelude/transition, persist conversation
* `6ed4268` — all-piper audio: one voice, autoplay-aggressive, no Play button
* `b779217` — fix: caption reroute when requested margin is too narrow
* `593a1dc` — fix: regression in T1 — port-stability change broke MCP startup
* `bd890c7` — T2.1: auto-open Studio in the browser
* `fa0510b` — T3: realtime bridge stub + canvas-state digest
* `e01bdc5` — T2: Sevim Studio — direct-to-Anthropic voice tutor (skeleton)
* `42b1c65` — T1: stable port, intro-in-open, Web Speech, click-anywhere unlock
* `b7fe880` — D: route missing canvas_id to most-recent user canvas
* `ae2dd8d` — C: prevent point-label and caption overlaps in math figures
* `47f64ff` — B: preheat spaCy + piper + cairosvg on a background thread
* `1e58ccf` — A: stop blink-cascade on every re-render
* `d26e8ac` — success_1: working baseline before perf tuning

Architectural new piece: **`studio/`** — a standalone tutor surface
that talks directly to the Anthropic Messages API with sevim tools
wired as function-call definitions.  Bypasses Claude Code's giant
system prompt; TTFT drops from ~100 s to ~1-3 s.  Runs as a separate
process via `sevim-studio` (or `python -m studio`).  **Currently OFF**
per user instruction — see *Open work* below.

---

## Open work — what's next

1. **Test the latest fix** (commit a6f1302) end-to-end via Claude Code:
   in the test window, `/exit` then `claude`, send a sevim-using
   prompt (e.g. *"reduce 3SAT to hamiltonian path"*).  Two things to
   verify:
   * Tool call sequence has NO trailing `sevim_add_caption` stragglers
     — captions should land in `sevim_apply`'s ops list.
   * Canvas elements with a narration phrase pointing at them stay
     hidden until that phrase plays — figure builds synchronized with
     the audio.
2. **Studio is off.** User said "stop using anthropic API for now."  Do
   NOT relaunch `sevim-studio`.  Studio code is intact and ready for
   future use; just don't spawn a new process.
3. Possible follow-ups (not urgent):
   * Edge-through-label collisions (edges crossing point labels) —
     out-of-scope for fix C; would need an edge-routing pass.
   * Autoplay reliability on fresh ports — Chrome MEI improves over
     time; can also try AudioContext.resume() for more permissive
     gating.

---

## Earlier status (preserved from 2026-05-08)

MCP plugin layer functional; geometric layout, captions in
margins, fade-in animation, browser auto-open, and voice narration all
end-to-end verified. Local Claude Code integration registered and
healthy. claude.ai web (HTTP transport) code-ready, awaiting tunnel.

---

## What this project is now

Sevim has been pivoted from a Lyceum-internal diagram backend into a
standalone MCP plugin that connects to host LLMs (Claude Code, Claude
Desktop, claude.ai web, ChatGPT Apps SDK, Gemini CLI, Cursor, Zed). The
host LLM calls Sevim's tools to build live diagrams as it explains
math/geometry/concepts; the user watches the canvas update in their
browser as the conversation unfolds.

GitHub remote was disconnected in preparation for a new repo.

---

## What was built this session

### MCP plugin layer (new)
- `mcp_server/__init__.py`, `mcp_server/server.py`, `mcp_server/__main__.py`
  — FastMCP server, two transports (stdio + streamable-http), runs
  uvicorn for the viewer in a daemon thread.
- 12 tools: `sevim_open`, `sevim_describe`, `sevim_add_node`,
  `sevim_add_edge`, `sevim_add_caption`, `sevim_remove`, `sevim_apply`
  (batched), `sevim_render`, `sevim_review` (PNG for vision feedback),
  `sevim_narrate`, `sevim_vocabulary`, `sevim_list_canvases`,
  `sevim_close`.

### Canvas state (new)
- `service/canvas.py` — `Canvas` dataclass + `CanvasRegistry` shared by
  MCP tools and the viewer. Per-canvas state: SceneGraph, SVG, warnings,
  appear-step indices, narration manifest, browser-opened flag.

### Live viewer (new + extended)
- `service/app.py` extended with canvas routes:
  `GET /canvas/<id>/view` (HTML), `/svg`, `/state`, `/events` (SSE),
  `/narration.wav`, `/narration.json`.
- `service/static/canvas.html` — auto-updating viewer with SSE; audio
  player and play overlay for narration; CSS for `.sevim-highlight`
  (yellow stroke + pulsing drop-shadow).

### Geometric layout (new)
- `sevim/s4_geo_layout.py` — coordinate-honouring layout. Triggers when
  `Canvas.math_mode=True`. Reads `meta["x"]/["y"]`, `meta["cx"]/["cy"]/["r"]`;
  scales to canvas px aspect-preserving, flips y so up is up. Edge
  endpoints set to shape centres so S5 draws straight lines.

### Captions (new primitive)
- `caption` added to `Primitive` Literal in `sevim/ir.py` and to
  `MATH_PRIMITIVES`.
- `_render_caption` in `sevim/s5_render.py` renders soft rounded box
  with optional dashed leader line back to the math anchor.
- `_place_captions_in_margins` in `s4_geo_layout.py` pushes captions to
  the canvas margin (above/below/left/right/auto/overlay), stacks
  multiples per margin without overlap, records leader endpoints.
- Anchors honoured: `auto` (pick margin with most room), `above`,
  `below`, `left`, `right`, `overlay` (in-figure escape hatch).

### Closed-loop visual feedback
- L1 — `sevim/geo_check.py`: algorithmic critic. Catches
  `lies_on_violation`, `duplicate_coords` (excluding caption-vs-anchor
  shared coords), `circle_missing_radius`, `math_mode_no_coords`,
  `dangling_edge`. Warnings ride on every mutation tool's result.
- L2 — `sevim_review` MCP tool returns the canvas as a PNG via
  `cairosvg`. Vision-capable hosts see the figure and can self-correct.

### Fade-in animation
- `Canvas.animate=True` (set via `sevim_open(animate=True)`). Each
  added node/edge/caption gets a stagger index in `Canvas.appear_steps`.
- `_apply_fade_in` in `service/canvas.py` post-processes the SVG via
  regex; injects SMIL `<animate opacity="0→1">` with `begin="N*0.4s"`
  on each element bearing a `data-nid` / `data-eid`. `fill="freeze"`
  preserves the final state. Pure SMIL; no JavaScript required for
  the animation.

### Voice narration
- `sevim/narrate.py` — piper-tts based per-phrase synthesis. Each
  phrase synthesised separately; durations read from WAV headers;
  concatenated with 0.18s gaps. Manifest carries exact `[start_s,
  end_s)` per phrase plus the highlight target.
- Default voice: `~/.local/share/sevim/voices/en_US-lessac-medium.onnx`
  (61 MB, downloaded from huggingface.co/rhasspy/piper-voices).
  Override via `SEVIM_VOICE_MODEL` env var.
- Audio served at `/canvas/<id>/narration.wav`; manifest at
  `/canvas/<id>/narration.json`. Viewer JS uses `audio.timeupdate` to
  toggle `.sevim-highlight` class on the `<g data-nid|eid>` matching
  the active phrase's target. Browsers block autoplay → viewer surfaces
  a "▶ Play narration" overlay; one click unlocks for the session.

### Browser auto-open
- `sevim_open(auto_open=True)` (default) calls `webbrowser.open(view_url, new=2)`
  once per canvas. Tracked via `Canvas.browser_opened`. Suppress with
  `SEVIM_NO_BROWSER=1`.

### User-level guidance
- `~/.claude/CLAUDE.md` written with workflow guidance: when to use
  sevim, math_mode vs concept-diagram, structured vs describe, the
  warnings + sevim_review feedback loop, auto-open behaviour.
- FastMCP server-level `instructions` mirror the same guidance so
  hosts that don't load CLAUDE.md still get the workflow hints.

---

## What works (verified)

- `claude mcp list` shows ✓ Connected
- 12 tools registered and callable via JSON-RPC stdio
- HTTP transport works (`--transport http` + JSON-RPC over POST `/mcp`,
  session-id management, ports 7777 viewer / 8765 MCP)
- Geometric layout produces correct figures (cos90 unit circle test
  saved as `/tmp/cos90_v4.png`)
- Captions placed in margins with leader lines, no figure overlap
- L1 critic catches all 5 deliberate-mistake cases; zero false positives
  on a clean figure (caption-vs-anchor coord-share is now exempt)
- L2 review returns `[TextContent, ImageContent]` with a real PNG
- Fade-in injection: 15 elements → 15 `<animate>`, byte-identical SVG
  when animate=False
- Narration generation: 7-phrase script → 25 s of audio via piper at
  ~20× realtime; manifest matches WAV duration
- 173/175 existing tests pass (the 2 failures are pre-existing S2
  extraction-coverage gaps, unrelated to anything added this session)
- Browser auto-open verified by stubbing `webbrowser.open` (1 call per
  new canvas, no relaunch on re-open of same id, suppressed when
  `auto_open=False` or `SEVIM_NO_BROWSER=1`)

---

## How to resume

1. **Refresh Claude Code's MCP subprocess.** Whenever you change code
   under `mcp_server/`, `service/`, or `sevim/`, the MCP subprocess is
   stale until the next `claude` invocation. Run `/exit` then `claude`
   in the user-facing terminal.

2. **Smoke the integration.** In the new session, ask:
   `What sevim tools do you have? Especially sevim_narrate?`
   If `sevim_narrate` shows up, the subprocess is fresh.

3. **Drive a math figure.** Prompt:
   ```
   Use sevim with voice narration to explain why cos(90°)=0.
   Open a math_mode canvas (animate=True), build the unit-circle figure,
   then call sevim_narrate with a script of short phrases — each
   phrase highlighting the figure element it talks about.
   ```
   The browser should auto-open; figure builds; narration plays with
   matching elements pulsing yellow.

---

## Known limitations / open issues

- **Caption auto-placement is heuristic.** All same-margin captions
  share one stacking strip; long captions or many on one side can still
  spill or crowd. Real fix: collision-aware push-out from preferred
  anchor with margin fallback. Future work.
- **Animation timing isn't synced to narration.** `animate=True` uses
  fixed 0.4s steps; `sevim_narrate` uses real WAV durations. They run
  independently. Natural follow-up: drive fade-in *from* the narration
  manifest so each shape appears the moment its phrase starts.
- **Phrase-accurate, not literal-word-accurate.** `sevim_narrate`
  highlights switch at phrase boundaries, not on individual words. To
  go finer requires piper's phoneme alignment output.
- **Pre-existing Sevim layout bug** (`sevim/s4_layout.py:287`): `KeyError: 0`
  on certain math-relation graphs (Sugiyama level-0 empty). Canvas
  survives via try/except and surfaces `last_error`, but the SVG
  doesn't update on the offending mutation. Easy fix:
  `by_level.get(L-1, [])` instead of `by_level[L-1]`.
- **HTTP transport unauthenticated.** Anyone with the tunnel URL can
  call tools. Acceptable for short-lived dev tunnels; needs OAuth
  before any stable deployment (FastMCP supports it via `auth=`).
- **No persistence.** Canvases live in process memory; closing the MCP
  server discards them. Easy v1.1: pickle the registry on shutdown.
- **2 pre-existing test failures** in S2 extraction
  (`test_render_per_relation::test_equals_renders_solid_with_equals_label`
  and `test_university_extension::test_natural_transformation_relation`)
  — extraction coverage gaps, unrelated to anything done this session.

---

## Runtime knobs

| Variable | Default | Effect |
|---|---|---|
| `SEVIM_HTTP_HOST` | `127.0.0.1` | Viewer bind host |
| `SEVIM_HTTP_PORT` | `7777` | Viewer bind port (auto-fallback if taken) |
| `SEVIM_MCP_HOST` | `127.0.0.1` | HTTP-transport MCP bind host |
| `SEVIM_MCP_PORT` | `8765` | HTTP-transport MCP bind port |
| `SEVIM_NO_BROWSER` | unset | Set `=1` to disable auto-open (headless / CI / SSH) |
| `SEVIM_VOICE_MODEL` | `~/.local/share/sevim/voices/en_US-lessac-medium.onnx` | Piper ONNX path |
| `SEVIM_DATA_DIR` | `~/.local/share/sevim/canvases` | Per-canvas asset root |

---

## File inventory (added/changed this session)

```
mcp_server/__init__.py       (new)
mcp_server/__main__.py       (new) — entry point, two transports
mcp_server/server.py         (new) — 12 FastMCP tools

service/canvas.py            (new) — Canvas, CanvasRegistry, fade-in
service/app.py               (modified) — canvas + narration routes
service/static/canvas.html   (new) — viewer with audio + highlight CSS

sevim/s4_geo_layout.py       (new) — geometric layout pass
sevim/geo_check.py           (new) — L1 algorithmic critic
sevim/narrate.py             (new) — piper-tts per-phrase synthesis

sevim/ir.py                  (modified) — added `caption` primitive
sevim/s3_map.py              (modified) — caption sizing
sevim/s5_render.py           (modified) — caption renderer, geo-mode axes,
                                          geo-mode circle (outline-only)

pyproject.toml               (modified) — bumped to 0.2.0; added fastapi,
                                          uvicorn, mcp, sse-starlette,
                                          cairosvg, piper-tts; added
                                          sevim-mcp script entry

~/.claude/CLAUDE.md          (new, user-scope) — workflow guidance
~/.claude.json               (modified by `claude mcp add`) — MCP registration
~/.local/share/sevim/voices/en_US-lessac-medium.onnx (downloaded, 61 MB)
~/.local/share/sevim/voices/en_US-lessac-medium.onnx.json (downloaded, 5 KB)
```

---

## Diagnostic commands

```bash
# MCP registration health
claude mcp list

# Confirm the test narration WAV plays through system audio
aplay /home/ara/.local/share/sevim/canvases/n1/narration.wav

# Run the test suite (skip the 2 pre-existing failures)
uv run python -m pytest tests/ -q \
  --deselect tests/test_render_per_relation.py::test_equals_renders_solid_with_equals_label \
  --deselect tests/test_university_extension.py::test_natural_transformation_relation

# Smoke the MCP server out-of-band
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  | uv run python -m mcp_server
```
