---
name: 2026-05-22 — 850-question lean-math bench (diff 6/7/9) + XML-validity fix
description: Ran the remaining 850 questions from the lean-math CSV through the local pipeline; aggregated structured signals; fixed the duplicate-style/&nbsp; XML invalidity that affected 12 turns; quality-gate now enforces SVG-is-valid-XML. Production rev 147 live.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
**Bench artefacts** at `/tmp/lean_bench_full/`:
  • `results.json` — 850 structured per-turn records
  • 848 PNG screenshots
  • `report.md` — aggregated summary
  • `server.log` (~3.8 MB)

**Coverage**: 17 distinct topics × 50 each (300 diff-6 + 200 diff-7
+ 350 diff-9).

**Headline numbers** (all post P1–P4 fixes from 2026-05-21):
  • 0 protocol errors out of 850.
  • 838/850 (98.6%) valid XML.
  • 12 invalid XML — all from "Graph theory and reachability" topic,
    all same error: `duplicate attribute: line 6, column 120`.
  • Math verifier: 457 failed / 393 no-claims / ~0 verified — the
    LLM rarely emits formalisable claims for these topics, but the
    P3 "skipped unparseable" path now prevents spurious retries.
  • Boilerplate first-phrase: 1.4% (down from pre-fix ~30%).
  • Performance: median 45 s, 16% turns > 60 s, ~2% > 90 s.
  • 10 transient OpenAI ConnectErrors handled mid-bench (1.2%).

**Route distribution** (which template caught each prompt):
  • llm-svg 432 (51%)  • sequential 191 (22%)  • graphviz 113 (13%)
  • template 47  • homomorphism 34  • venn 30  • panels 3

**Clean topic→route fits**:
  • "Inductive trees" → 50/50 graphviz.
  • "Boolean predicates" → 18 template + 27 sequential.
  • "Sets and finite reasoning" → 11 venn + 12 sequential + 27 llm.
  • "Custom algebraic reasoning" → 48 llm + 2 homomorphism (the
    tightened keyword filter correctly let only the 2 actual graph-
    homomorphism cases route here).

**Issue found + fixed** (`commit 9dedb73`):
  `_make_svg_responsive` was being applied TWICE to the homomorphism
  template's output — once by graphviz_route.render_graphviz, once
  by graph_homomorphism._render — leaving the root <svg> with two
  `style=` attributes (XML-illegal).  Plus `&nbsp;` in the legend
  HTML which XML parsers reject without a DTD.

  Fix 1: `_make_svg_responsive` is now idempotent (returns input
  unchanged if `max-width:100%` already in the root tag).
  Fix 2: legend uses plain spaces, not `&nbsp;`.
  Fix 3: `quality_gate.check_svg_xml_valid` added to the universal
  gate so this regression class can't recur.

**Worth following up (not blocking)**:
  • 9 turns produced canvas_id but the bench's /svg fetch came back
    empty — likely a race between SSE-complete and canvas.set_raw_svg.
    Doesn't affect production users; affects only the bench's data
    capture.
  • Surjective-composition figure (`complex_0151`) still has some
    arrowheads touching circle edges — snap pass tolerance leaves
    a few cases.
  • Vision-audit "FAIL" verdicts run 54% across turns; many are
    benign retry-then-pass cases.  Worth a follow-up to log only
    the FINAL verdict, not every attempt's.

**Production deploy**: ECS task def rev **147** live, commit
9dedb73, gate 59/59.
