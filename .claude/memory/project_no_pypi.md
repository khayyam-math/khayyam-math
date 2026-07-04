---
name: Khayyam Math will NOT be published to PyPI
description: Decision (2026-05-23): pip package is too thin to ship as the flagship install surface — direct people to git+ install or `git clone + python -m studio` instead
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---

**Decision:** the `khayyam-math` Python package (currently 0.4.1)
will not be published to PyPI.  Install path is
`pip install git+https://github.com/khayyam-math/khayyam-math` and
the full product experience is the self-hosted Studio service
(`git clone + python -m studio` — runs on `http://127.0.0.1:8765/studio`,
same code as khayyammath.com).

**Why:** the pip package only wraps the LLM call (provider switch
across OpenAI / fine-tuned Qwen / vLLM).  It does NOT include any of
the deterministic-rendering or verifier stack that makes
khayyammath.com what it is — Graphviz route, matplotlib route,
Plotly route, per-domain templates, CP-SAT layout planner,
vision-audit retry loop, SymPy + Z3 + Lean + Mathlib verifier chain,
Piper / OpenAI narration audio, phrase-timed canvas viewer.  All of
that lives in `studio/`, `service/`, `sevim/` — used only when the
Studio service runs as a process, not by importing
`khayyam_math.KhayyamMath`.

A learner who `pip install`-ed and called `client.generate(...)`
would get a strictly weaker figure than what they'd see by visiting
khayyammath.com.  Publishing this to PyPI as the flagship 'install
Khayyam Math' surface would set the wrong expectation.

**How to apply when you resume:** don't propose publishing to PyPI
again unless either (a) the package is refactored to lift the
deterministic rendering + verifier stack into the pip-installable
surface, OR (b) the package is explicitly reframed as 'developer
client for embedding the model in other apps' rather than as the
canonical way to use Khayyam Math.

Task #136 in this session is the historical trace.
