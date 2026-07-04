---
name: project_self_aware_refinement_2026_06_16
description: "Self-aware conversational refinement: corrections now carry the topic + circuit-break repeated failures instead of stateless regeneration"
metadata:
  node_type: memory
  type: project
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-16: user complaint — "when you ask the system for correction, it doesn't get it and gives another complete answer with the problem again; the system is not doing a complete conversation, not self-aware and self-constructive." (The clique→VC transcript: 5 complaints "I need nodes! two graphs with nodes!" → 5 identical LLM-SVG failures.) User chose **"Full self-constructive loop"** scope.

Root cause (diagnosed via Explore): corrections were stateless. On a Case B/C refinement the routing_prompt = the chat-LLM's paraphrase of the CORRECTION ALONE, which lost the original subject, so deterministic renderers couldn't match and it fell to LLM-SVG which redrew the same defect. `is_narrow_targeted_edit` only sets `_refining` for narrow edits ("make it red"); complaints get `_refining=False` (deterministic eligible) but the topic was missing so nothing matched. No per-session failure memory, no strategy change on repeat.

Fix = `studio/refinement.py` (new) + wiring (commit `41110af`, deployed). Three layers:
- **L1 topic carry-forward** (`carry_topic`): on a Case B/C correction, prepend the prior figure's genesis prompt (`context_canvases[i]["prompt"]` = `pc.genesis_prompt`) to the routing string. Wired in `express.py` right after routing_prompt is set (~line 2629). PROVEN: "I need nodes!" + carried "reduce clique to vertex cover" → `is_clique_vertex_cover_prompt` matches → clique_vertex_cover renderer fires, 0 retries, 10 node circles. Flag `SEVIM_REFINE_TOPIC_CARRY` (default on).
- **L2 circuit breaker** (`Tracker`, `escalation_directive`): per-session in-memory count of consecutive complaints about the same subject signature; at ≥`SEVIM_REFINE_ESCALATE_AFTER` (default 2) inject `escalation_note` into express's refinement LLM context ("your attempts were REJECTED, change approach, or honestly draw the closest version"). Wired in `app.py::_execute_tool` (computes complaint/escalation before `express_figure`, passes new `escalation_note=` param; records outcome after). Flag `SEVIM_REFINE_CIRCUIT_BREAKER` (default on). `is_dissatisfaction()` = broad complaint-cue regex.
- **L3 self-constructive learning (ADMIN-GATED, safe)**: every refinement outcome (subject sig, route, complaint) logged to new telemetry table `refinement_outcomes` via `record_refinement_outcome`; `recurring_refinement_failures(min_count)` mines recurring (subject, failed-route) pairs for operator review. Live routing-hint READ (`preferred_route_hint`/`preferred_route_for`) is OFF by default (`SEVIM_REFINE_HINTS=off`) and promotion stays admin-gated — system NEVER autonomously rewrites its own routing (honors [[feedback]] "never autonomously self-modify"). The admin promotion UI is the remaining follow-up; data accumulates now.

`tests/test_refinement.py` (8 tests). 558 tests pass. express_figure got a new `escalation_note` param (internal recursive callers pass None). See [[project_session_2026_06_16]] (clique_vertex_cover renderer this references), [[feedback_deterministic_routes_no_fallback]].
