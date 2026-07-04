---
name: "OpenAI API key location"
description: Where the user's shared OpenAI API key lives on this machine — and how Sevim picks it up.
type: reference
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The user's OpenAI API key is **not** in the shell environment of Claude Code sessions. It lives in `.env` files inside `~/Documents/Programming/agentic_systems/` projects:

- `agentic_systems/TextVis-Holographic/.env` (most recent, 2026-04-04)
- `agentic_systems/TextVis 3D/.env` (same key)
- `agentic_systems/Interactive Video Lecture Creator/.env` (same key)
- `agentic_systems/Video Lecture Generator/.env` (older, different key)

The first three share an identical `OPENAI_API_KEY=sk-pro…` (sha1 of value: 9ae59e7c…). Treat any of those three as the canonical source if it needs to be re-imported.

For Sevim specifically, the key has been copied into `sevim_plugin/.env` (gitignored, perm 600), alongside:
```
SEVIM_VLLM_URL=https://api.openai.com/v1
SEVIM_VLLM_MODEL=gpt-4o
```
`service/start.sh` sources `.env` automatically, so launching the server picks up the OpenAI config without extra flags. Studio's `/api/studio/info` endpoint confirms reachability with `"remote":"openai","api_key_configured":true,"vllm_reachable":true`.

The same `ANTHROPIC_API_KEY` is also in those `.env` files, but per user instruction Sevim uses OpenAI — do not switch backends without being asked.
