---
name: feedback_fix_root_cause_over_gate_bypass
description: "When a deploy is blocked, user prefers fixing the root cause over bypassing the quality gate, even for unrelated/pre-existing blockers"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7b5611d5-76e1-4124-8388-5dc5cf3a2293
---

When the pre-deploy quality gate (or verifier) blocks a deploy, do NOT
reach for `SEVIM_SKIP_QUALITY_GATE=1` / `SEVIM_SKIP_VERIFIER=1`. On
2026-06-08, offered "bypass the gate once (blocker is pre-existing and
unrelated to your fixes)" vs "fix it properly", the user chose **fix it
properly** twice in a row (first the chat-tool-invocation flake, then the
math-verifier false-negative).

**Why:** the user treats the gate as a real correctness contract, not
red tape; a green gate is the bar for shipping. Even a blocker that is
provably orthogonal to the change at hand is treated as a bug to fix now.

**How to apply:** when the gate fails, diagnose to root cause and fix it,
then re-run the gate clean. Only propose a bypass as a genuine emergency
(e.g. site-down hotfix) and let the user decide. See [[feedback_deploy_wrapper]]
and [[feedback_direct_commit_and_deploy]] for the deploy mechanics.
