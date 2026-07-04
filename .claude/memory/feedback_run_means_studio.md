---
name: "Run = Studio, not a blank canvas"
description: When the user asks to "run the server / open the webpage", open Studio at /studio — never sevim_open a blank canvas.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
When the user asks something generic like "run the server and open the webpage" / "start it up" / "let me see it", the right surface is **Sevim Studio** at `http://127.0.0.1:7777/studio` (or `:8003/studio` if launched via `service/start.sh`). Do NOT call `sevim_open` to spawn a blank canvas — that produces an empty viewer page the user has no use for.

**Why:** The user explicitly told me to "remove this page and address permanently — I want just to see the studio when the program is ran" after I opened a blank `scratch` canvas. Studio is the user-facing app; canvases are an internal artifact that the MCP tools create on demand during a real explanation.

**How to apply:**
- "Run the server" / "open the webpage" / "launch it" → start the server (e.g. `service/start.sh` with `SEVIM_PY=.venv/bin/python`, or `python -m studio`) and open `/studio` via `xdg-open` (or rely on `studio/__main__.py` which auto-opens).
- Only call `sevim_open` when there is an actual visual to draw (math, geometry, diagrams) per the Sevim workflow in CLAUDE.md.
- If a blank canvas got opened by mistake, `sevim_close` it.
