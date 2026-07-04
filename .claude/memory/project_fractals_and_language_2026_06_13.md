---
name: project_fractals_and_language_2026_06_13
description: "10 deterministic fractal renderers + language-drift fix (English input → German output bug)"
metadata:
  node_type: memory
  type: project
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-13: a Russian tester (fractal geometry for natural objects, German-based) reported two issues on khayyammath.com: (1) fractal figures (Sierpinski carpet, Koch snowflake, Menger sponge) came out "not visual enough" — text-heavy LLM-SVG sketches that failed vision review; (2) the site "switched to German" for his English input. User: "solve these problems permanently" (the tester will try similar problems).

**Fractals — `studio/templates/fractals.py` (commits a2c8f18 + 6317cb5, deployed).** Fractals are exact recursions, so deterministic renderers beat the LLM. 10 renderers: koch (snowflake, depth-4 + iterations), sierpinski_triangle, sierpinski_carpet, menger (isometric drilled cube — visible faces are i=3/j=3/k=3, NOT i=0/k=0 which overlap the top), mandelbrot + julia (escape-time, run-length-encoded rows via `_rle`), barnsley (IFS point cloud, 9000 green dots, seed 20240613), cantor, dragon (Heighway, turn = `((i&-i)<<1)&i`), pythagoras (recursive squares; perpendicular for "up" is (dy,-dx) since screen-y is down — getting this wrong grows it off-canvas). Routed via `which_fractal`, flag `SEVIM_FRACTAL_ROUTE` (default on), before the stats routes. `tests/test_fractals.py`.

**Language drift fix — `studio/app.py` chat loop (~line 1156).** The detector (`studio/language.py:detect_language`) is CORRECT: "Sierpinski carpet"/"Koch's Snowflake"/"Menger sponge fractal" all → `en` (no stopword/diacritic hits → English default). The bug was that the chat loop only pinned the OUTPUT language when it was NON-English (`if _chat_lang not in ("en","und","")`), so an English prompt got NO "stay in English" constraint and the LLM could drift to a language from earlier session context (→ German for a German-context user). FIX: always pin the CURRENT message's detected language, INCLUDING English (`not in ("und","")`), with a tailored "don't switch" clause. Output now follows the message in hand, never session history. (Deterministic fractal narration is English regardless.) 516 tests pass. See [[feedback_narration_word_accurate]], [[project_session_2026_05_31_overnight]] (language matching), [[project_reduction_overlap_fix_2026_06_12]].
