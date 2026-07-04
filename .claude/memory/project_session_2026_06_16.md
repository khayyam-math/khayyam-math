---
name: project_session_2026_06_16
description: "2026-06-16: fixed /studio/chat 500 (stale Postgres connection) + clique->vertex-cover now drawn as two real node graphs"
metadata:
  node_type: memory
  type: project
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-16 session — two production fixes:

**1. /studio/chat 500 — stale Postgres connection (commit `82399ae`, deployed).** User hit HTTP 500. CloudWatch: `psycopg.OperationalError: the connection is closed` at `check_cost_guard → tel.session_cost → query → self._conn.cursor()`. Root cause: telemetry holds ONE long-lived `psycopg.connect(autocommit=False)` connection; RDS closes idle connections (and failover/blips sever it), and there was no reconnect. WRITES were already swallowed (`_exec` try/except → "[telemetry] write failed (silent)"), but READS (`query`) were unprotected → 500. Two-layer fix in `sevim/telemetry.py` + `studio/sessions.py`:
- `_PostgresBackend` now has `_reconnect()`; `execute()` reconnects-and-retries once on `OperationalError`/`InterfaceError` (and proactively when `self._conn.closed`); a GENUINE SQL error (other exception class) instead triggers `rollback()` to clear the aborted-transaction state that would otherwise poison the shared single connection for every later request, then re-raises. `commit()` reconnects on dead-conn too.
- `check_cost_guard` wraps `session_cost` and FAILS OPEN (returns None = allow request) on any telemetry error — a soft cost guard must never 500 chat. (`sys` import added to sessions.py.)
- `tests/test_telemetry_reconnect.py` (5 tests, fake psycopg). REUSABLE LESSON: the single shared autocommit=False Postgres connection is fragile — any new long-lived read path must tolerate reconnect; a raw SQL error poisons the whole connection until rollback.

**2. "reduce clique to vertex cover" gave text/schematic, not graphs (commit `eb2ba14`).** User conversation (pasted): repeatedly asked "I need nodes! two graphs with nodes!" for the Clique≤ₚVertexCover reduction and kept getting the generic number-free box-arrow schematic (from `reduction.py` `_parse_pair` → tier-2 schematic) or prose. Fix: `studio/templates/clique_vertex_cover.py` draws TWO real node-link graphs — G with clique S={1,2,3} (green) beside complement Ḡ with vertex cover V∖S={4,5} (orange, size n−k=2), same pentagon layout, canonical n=5 instance E(G)={12,13,23,34,45}. ASSERTS S is a clique in G, Ḡ=complement, V∖S covers every Ḡ edge, |VC|=n−k. `is_clique_vertex_cover_prompt` = "clique"+"vertex cover"+(reduce/≤p/complement). Wired into express cascade BEFORE the generic reduction route, flag `SEVIM_CLIQUE_VC_ROUTE`. `tests/test_clique_vertex_cover.py`. 550 tests pass. NOTE conversational refinements ("do it on a graph") run with `_refining=True` so deterministic routes are skipped — the fix relies on the INITIAL "reduce clique to vertex cover" prompt now drawing the graph so the user never needs to keep asking. The np_completeness proof route ("prove vertex cover is NP-complete") still renders the proof-structure schematic, not a concrete graph — left as-is.

See [[project_svd_route_2026_06_15]], [[project_reduction_overlap_fix_2026_06_12]] (reduction route + renderer-first), [[feedback_deterministic_routes_no_fallback]].
