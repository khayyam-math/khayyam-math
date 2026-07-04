---
name: project_language_misdetect_2026_06_26
description: "Fixed: terse/imperfect-English math prompts ('Graph, y=x^2') misdetected as Spanish; root caused BOTH the Spanish answer AND the text-instead-of-visual symptom"
metadata:
  node_type: memory
  type: project
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-26: field report (user "Ahmed") — a prompt starting "Graph, ..." in imperfect English was (1) answered entirely in **Spanish** and (2) the requested **visual came back as text**. Investigated: ONE root cause, two symptoms.

**Root cause:** `studio/language.py::detect_language` counted the math variable **"y"** as a stopword. "y" is Spanish for "and"; "o" is Spanish "or". The "weak signal" rule (return non-English on a single stopword hit when English hits == 0) fired because a terse math prompt like `Graph, y = x^2` has NO English stopwords — so a lone variable flipped detection to `es`. Reproduced deterministically: `detect_language('Graph, y = x^2') == 'es'` (also `'no'` → es, since English listed only "not").

**Why it also broke the visual:** the detected language is HARD-PINNED into the chat system prompt (`app.py` ~line 1236: "Every word of your chat reply MUST be in {lang}, and any tool prompt you pass to sevim_express MUST also be in {lang}"). So the misdetection forced Spanish AND very likely derailed the model into a Spanish text explanation instead of a clean `sevim_express` draw — the "kept answering in text" symptom. (Most-likely linkage, not transcript-proven; app logs don't store verbatim prompts and had rotated. If text-instead-of-visual ever recurs on CORRECTLY-detected English, that's a separate decision-rule issue in SYSTEM_PROMPT's "too vague to draw → ask clarifying question" escape, app.py ~line 918.)

**Fix (commit `0b42097`, deployed):** in `detect_language` (1) ignore single-character tokens in the Latin stopword matching — bare variables x/y/o/a/i/n carry no language signal (`words = [w for w in re.findall(...) if len(w) > 1]`); (2) added common English words to `_STOPWORDS_EN`: "no", "graph", "function", "value(s)", "axis", "point", "line", "curve", "give", "make", "want", "need" so terse English registers positively. Legit Spanish/German/French/Russian/Persian still detect (regression-tested). `tests/test_language.py` +10 tests; 591 total pass. See [[project_session_2026_05_31_overnight]] (language-matching work), [[project_fractals_and_language_2026_06_13]] (current-message language pin).
