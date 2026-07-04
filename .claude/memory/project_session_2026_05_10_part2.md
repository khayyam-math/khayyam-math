---
name: "2026-05-10 afternoon — production polish session (PRs 13-19)"
description: After the initial AWS launch, a single afternoon session shipped 7 follow-up PRs against the live khayyammath.com deployment, ending with one in-progress fix for canvas-viewer 404s on stale cids after task replacement.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
## What shipped this afternoon (PRs 13-19, all live on https://khayyammath.com)

| PR | Effect |
|----|--------|
| 13 | Typo-domain redirect: `khayyamath.com` (one M) registered + 301-redirected to canonical `khayyammath.com`. CDK now takes `SEVIM_REDIRECT_DOMAINS=` (comma-separated); per-domain ACM cert + Route 53 A-alias + ALB host-header listener rule. |
| 14 | Conversational chat. Express system prompt no longer forces `sevim_express` on every turn — chat-only replies for follow-up Q&A about the existing figure. Also dropped the unconditional `"And now please look at the diagram."` transition (`service/canvas.py:412-419`); now only prepended when `transition_text` is explicitly set. |
| 15 | Public landing page at `GET /` (`service/static/landing.html` — hero with embedded animated SVG demo, How-it-works, Examples, FAQ). SEO: `<title>`, meta description+keywords, canonical, Open Graph, Twitter Card, JSON-LD `WebApplication`. `/robots.txt` allow / + disallow `/studio` `/canvas/*`; `/sitemap.xml` lists `/`. |
| 16 | OpenAI `tts-1-hd` voice replacing piper. `sevim/narrate.py` got `_tts_backend()` selector with `auto` (prefer OpenAI when key present). `SEVIM_TTS_MODEL` (default `tts-1-hd`), `SEVIM_TTS_VOICE` (default `alloy`). Cost ~$0.036/turn at typical phrase length. |
| 17 | Mobile canvas-not-showing fix — bundle of three sub-fixes:<br>• **Chunked WAV read** (`readframes(65536)` loop) — OpenAI's WAV header has bogus `nframes=2147483647` (INT32_MAX placeholder); `readframes(2147483647)` was pre-allocating 4.3 GB → MemoryError on 2 GB Fargate. Read in chunks instead.<br>• **Parallel TTS** via `ThreadPoolExecutor(12)` — 11 phrases drop from ~22s sequential → ~3.7s. Code in `sevim/narrate.py:synthesize_script`.<br>• **SSE `ping=15`** keepalive on the chat EventSourceResponse so long tool calls don't get cancelled.<br>Plus diagnostic logging in `studio/app.py` chat-loop tool-failure path: `repr(exc) + traceback.print_exc(file=_sys.stderr)` (was `str(exc)` which is empty for `CancelledError` etc.) and honest "Sorry — couldn't generate that figure" instead of always-fake "(figure built)". |
| 18 | Refinement narration emits ONLY new phrases on follow-ups. The express system prompt + `_build_user_content` REFINEMENT MODE block now make it explicit: SVG continues to keep all prior elements; `narration` field contains ONLY phrases describing this turn's change. Verified end-to-end via `/tmp/sevim-ux/two_turn.py`: turn-2 narration was 2 phrases / 6.86s with zero overlap with turn-1's 8 phrases / 38.45s. |
| 19 | Two fixes:<br>• **Explicit-play audio.** Removed document-wide `click/keydown/touchstart` autoplay listeners + `intro.canplay` autoplay attempt + `tryPlayChain()` calls in state-update path. Audio now ONLY starts when user clicks the header `▶ Play narration` button (always visible once the WAV loads) or the corner overlay pill. Verified via `/tmp/sevim-ux/no_autoplay.py`: clicking page background does NOT start audio.<br>• **Question-specific first phrase.** Express prompt got "FIRST NARRATION PHRASE" section banning generic openings (`Now let's…`, `Let's see…`, `First, let's…`, `OK so…`, `And now please look at the diagram.`) with three worked examples. Verified: turn-1 first phrase is now `"In any triangle, the three interior angles always add up to π radians — here's why."` |

## Cost cap raise (deploy br1pjzzmr, also live)

`SEVIM_COST_DAILY_MAX_USD=10.00` added to ECS task env (was default $1). User was hitting $1.05 cap; new cap is $10/day per session_id. ~100 turns/day at the real per-turn cost (~$0.10 with gpt-4o + tts-1-hd + vision audit).

## In-progress / unfinished — canvas viewer 404 after task replacement

User report: `{"detail":"canvas 'express_36ce461140e5ce51' not found"}`.

Root cause: `service.canvas.REGISTRY` is **in-memory only**. Each ECS task replacement (rolling deploy, scale-down, container crash) wipes it. The user's iframe URL `/canvas/<id>/view` then 404s on a still-valid id.

The data IS still durable:
- `canvases` table on RDS Postgres (svg + narration_json + title) — written by `tel.record_canvas()` in `studio/app.py:_execute_tool`.
- WAV files on S3 (`sevimstack-canvasbucket-…`) — uploaded by `service/canvas.py:Canvas.narrate()` and `intro()`.

But the viewer's `service/app.py` endpoints all do `REGISTRY.get(cid)` and 404 on KeyError without falling back.

**Plan to finish (next session):**

1. Add a helper `_get_or_rehydrate(cid)` in `service/app.py`. Try `REGISTRY.get(cid)`. On `KeyError`, query `tel.query("SELECT svg, narration_json, title FROM canvases WHERE canvas_id = ?", (cid,))`. If found, call `REGISTRY.open(canvas_id=cid, math_mode=True, animate=False, width=900, height=620)` to materialise a fresh Canvas, then `c.set_raw_svg(svg)`, set `c.genesis_prompt`, set `c.narration_manifest` (NOTE: telemetry stores the SCRIPT not the manifest with timings — see caveat below). Return the rehydrated canvas. If the DB also doesn't have it, raise the 404.

2. Replace `try: c = REGISTRY.get(cid) except KeyError: raise HTTPException(404, …)` blocks in:
   - `canvas_view` (line 178-179)
   - `canvas_svg` (line 193-194)
   - `canvas_narration_wav` (line 223)
   - `canvas_intro_wav` (line 234)
   - `canvas_narration_manifest` (line 245)
   - `canvas_state` (line 258-259)
   - `canvas_events` (the SSE one)

   …with calls to the new helper.

3. **Caveat — narration manifest**: `tel.record_canvas(narration=narration)` stores the *script* (phrases + highlight ids) but NOT the timing manifest (`{start_s, end_s}` per phrase). The manifest is computed inside `synthesize_script` from measured WAV durations. Two options:
   - **(a)** Also persist the manifest. Add a `narration_manifest_json` column to `canvases` and write it from `_execute_tool` (the `narrate_out` returned by `c.narrate()` IS the manifest).
   - **(b)** Recompute timings on rehydration by re-reading the WAV from S3. Slower (one S3 GET per rehydration) but no schema change.

   Option (a) is cleaner. Adds ~1KB/canvas to the table, negligible. Backfill: existing rows just have `narration_manifest_json = NULL` and the rehydration falls back to script-only (no highlight sync, but figure + audio still play).

4. **SSE events on rehydrated canvases**: the `/canvas/<id>/events` endpoint streams live updates via `Canvas.bus`. A rehydrated canvas has an empty bus. Fine — these are static historical canvases, no live updates expected. The endpoint can return an immediately-closed stream.

## Useful state for continuation

- AWS account: `332504859695`, region `us-east-1`, profile `sevim`.
- ECS cluster: `SevimStack-ClusterEB0386A7-v4AtWGHqxGkN`
- ECS service: `SevimStack-Service9571FDD8-VxcEhl318ICU`
- App log group: `SevimStack-AppLogsC5DF83A6-67go79B0ttdt`
- Hosted zones: `khayyammath.com.` (Z0798668111KS8AKCI6HZ), `khayyamath.com.` (Z08562441UX1S8537QM6T)
- Auth secret cached at `/tmp/sevim-ux/.auth_secret` (read with `cat` for forge_cookie tests).
- ALB IP for local --resolve testing: `100.49.103.32` (DNS may have rotated; use `dig +short @8.8.8.8 khayyammath.com`).
- Re-deploy: `cd infra && export SEVIM_DOMAIN=khayyammath.com SEVIM_REDIRECT_DOMAINS=khayyamath.com AWS_PROFILE=sevim AWS_REGION=us-east-1 CDK_DEFAULT_ACCOUNT=332504859695 CDK_DEFAULT_REGION=us-east-1 PATH="/home/ara/.npm-global/bin:$PATH" && cdk deploy SevimStack --require-approval never`
- **Cache-bust trick**: if a `cdk deploy` reuses a stale ECR image (happened twice today, when killed deploys left a partial asset published), append `# build-rev: $(date +%s)` to any source file to force a fresh asset hash.

## Smoke-test scripts (kept on disk)

- `/tmp/sevim-ux/audit.py` — multi-viewport screenshot audit (iPhone 14 Pro, Pixel 7, iPad Mini, desktop).
- `/tmp/sevim-ux/mobile_repro.py` — iPhone 14 Pro flow: log in via forged cookie, click first chip, wait for canvas, dump iframe contents.
- `/tmp/sevim-ux/two_turn.py` — Turn 1 + refinement Turn 2; validates narration delta-only behaviour.
- `/tmp/sevim-ux/no_autoplay.py` — confirms audio doesn't start on background clicks; only on Play button.

All four use the host-resolver-rules trick to bypass stale local DNS:
`browser = await p.chromium.launch(args=[f"--host-resolver-rules=MAP khayyammath.com {ALB_IP}"])`
