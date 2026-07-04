---
name: API code is for Sevim's Python, not for Claude directly
description: Sevim's internal Python may call the Anthropic/Claude API; the assistant must not invoke the API or claude-api skill itself.
type: feedback
originSessionId: 642ead67-921c-4124-aa55-4900c8fb2d61
---
Sevim's Python codebase is allowed to use the Anthropic/Claude API code internally (e.g., extractors, narration, review pipelines). The assistant (Claude Code) must NOT call the Claude API directly, invoke the `claude-api` skill, or write/run code that hits the API on its own behalf when working in this project.

**Why:** The user wants API usage to flow through Sevim's own Python entrypoints — that's where keys, caching, and orchestration live. Side-channel API calls from the assistant bypass that and create cost/leakage/duplication.

**How to apply:** When a task in this repo would benefit from a model call, route it through an existing Sevim Python tool/CLI rather than calling the API yourself. Don't auto-trigger the `claude-api` skill. If something genuinely needs new API integration, propose adding it inside Sevim's Python and let the user decide — don't bolt on ad-hoc API calls from shell or scratch scripts.
