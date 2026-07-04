---
name: 2026-05-09 session — perf + Studio + narration-synced fade-in
description: Day-long iteration on Sevim performance, audio UX, Studio web app, and overlap fixes. Open question is whether the latest commit (narration-synced fade-in + tighter MCP tool descriptions) actually eliminates trailing sevim_add_caption stragglers and lands synced animation.
type: project
originSessionId: 6408e0cf-e5b1-4987-ac17-aa54621abddb
---
## Summary

Long iteration day pivoting around three problems:

1. **Latency**: time-to-first-audio in Claude Code path was ~100 s, dominated by Anthropic's Opus 4.7 first-token latency on Claude Code's giant system prompt.
2. **Audio quality / autoplay**: prelude was on Web Speech (different voice, too fast), narration on piper, voices mismatched; autoplay click-prompt kept appearing.
3. **Overlap regressions**: clustered point labels, captions clamped over the figure when their requested margin was too narrow.

## What landed (commits)

`success_1` is the rollback tag.  Latest commit: **`a6f1302`** — narration-synced fade-in + MCP tool descriptions push toward `sevim_apply`.  Full chain: A → B → C → D → 1 → 2 → 3 → T1 → T2 → T2.1 → T3 → fixes through `b32a226` → studio streaming through `ae7f090` → final `a6f1302`.  See `SESSION_STATE.md` for the full ordered list with one-line descriptions.

## Architecture additions

* **`studio/`** — new standalone tutor web app (`/studio` routes mounted into the existing FastAPI app, plus `studio/__main__.py` as a console script `sevim-studio`).  Talks directly to Anthropic Messages API with sevim tools as function-call definitions.  Streams responses via SSE.  Bypasses Claude Code's giant system prompt; TTFT ~1-3 s instead of ~100 s.

## Constraints to honor in next session

* **Studio is currently OFF.**  User said "stop using anthropic API for now."  Do NOT relaunch `sevim-studio` or any process that hits the API.
* `feedback_api_use.md` rule: assistant must NOT invoke `claude-api` skill or call the Claude API directly.  Sevim's Python (e.g. `studio/app.py`, `s2b_improve.py`) is allowed; the agent itself is not.

## Open question for next session

Did the latest commit (`a6f1302`) actually fix the two remaining issues?
1. Are trailing `sevim_add_caption` calls gone (model now folds all captions into `sevim_apply`)?
2. Do canvas elements appear synchronized with their narration phrase (not on a fixed 0.4 s stagger)?

To verify: ask the user to `/exit` + `claude` in the test window, send *"reduce 3SAT to hamiltonian path"*, then look at the JSONL session log for the tool-call pattern, AND watch the canvas in the browser to see whether elements pop in with the audio.  No new code needed unless those tests reveal regressions.

## Repo state

* Hosted at `git@github.com:arashkermaniprojects/sevim-plugin.git`
* Latest pushed: `a6f1302` on `main`
* Rollback points: `git reset --hard success_1` (morning baseline) or `git revert <hash>` for individual fixes
* Tests: 173/173 passing (with the 2 known pre-existing test_render_per_relation + test_university_extension failures deselected)

## How to resume

Read `SESSION_STATE.md` first — it has the chronological commit list and a more detailed open-work block.  Then follow the test plan in "Open question for next session" above.
