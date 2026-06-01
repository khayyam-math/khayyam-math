# Contributing to Khayyam Math

Thanks for being here. This project exists to make math learning
better through AI-generated voice-narrated figures. Contributions in
any form — code, docs, templates, bug reports, ideas — are welcome.

## Before you start — read the architecture

If this is your first PR, please read at least these two docs before
you start coding:

1. **[ARCHITECTURE.md](ARCHITECTURE.md)** — the top-down map of every
   subsystem, with Mermaid diagrams of the request lifecycle, the
   ten-route pipeline, the refinement model, the math-correctness
   chain, and the deploy topology.
2. **[docs/PIPELINE.md](docs/PIPELINE.md)** — every figure route in
   detail, with "how to add a new template" and "how to add a new
   FDL primitive" recipes.

The other deep-dives are referenced from the README's
[**New here?**](README.md#new-here-start-with-these-docs) section.

## Quickstart for contributors

```bash
# 1. fork + clone
git clone https://github.com/YOUR-USERNAME/khayyam-math
cd khayyam-math

# 2. dev setup (Python 3.12+, uv)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# 3. system dependencies
sudo apt-get install -y graphviz   # macOS: brew install graphviz

# 4. env: copy template, plug in your OpenAI key
cp .env.example .env
$EDITOR .env

# 5. run the test suite
.venv/bin/python -m pytest -q

# 6. boot the local studio
.venv/bin/python -m studio
open http://127.0.0.1:8765/studio
```

## What we want help with (concrete asks)

These are listed roughly in order of "easy first issue" → "hardest":

| Area | Difficulty | Notes |
|---|---|---|
| **More deterministic templates** — Fourier series, slope fields, vector-addition diagrams, Riemann sums, more linalg ops | medium | See `studio/templates/matrix.py` and `studio/templates/newton.py` as patterns. Each template is a pure-Python function returning `(svg_string, narration_list)`. Recipe in [docs/PIPELINE.md](docs/PIPELINE.md#how-to-add-a-new-template). |
| **More FDL primitives** — `Asymptote`, `InflectionPoint`, `LocalExtremum`, `ParametricCurve` | medium | Extend `studio/templates/fdl.py`. Recipe in [docs/PIPELINE.md](docs/PIPELINE.md#how-to-add-a-new-fdl-primitive). |
| **More structural critic rules** — anything the vision LLM unreliably catches but a Python check can decide | easy-medium | `_structural_review` in `studio/express.py`. Recipe in [docs/MATH_CORRECTNESS.md](docs/MATH_CORRECTNESS.md#how-to-add-a-verifier-rule). |
| **Localisation voices** — Arabic, French, Spanish, Hindi narration voices | medium | The text-side language localiser already handles translation + digits-as-words for non-English. Piper-TTS supports many languages; add a voice config keyed by language code. |
| **Better Graphviz coverage** — Turing-machine `tape` visualisation, recursion-tree templates | medium | Extend `studio/templates/graphviz_route.py`. |
| **LaTeX scrubber rules** — more LaTeX commands → Unicode mappings | easy | `_LATEX_REPLACEMENTS` in `studio/express.py`. PRs welcome. |
| **More refinement-cue / narrow-edit patterns** — for `_REFINEMENT_CUE_RE` / `_NARROW_EDIT_PATTERNS` | easy | When you find a user phrasing that should be treated as a complaint/elaboration/narrow-edit and isn't, add the regex. |
| **Real layout-quality scorer corpus** — human-labelled (good, bad) layout pairs | hard | Current corpus is synthetic perturbation + filtered telemetry. Human labels would meaningfully improve the +2 pp pass-rate win. |
| **TikZ → SVG route** — for publication-quality math figures | hard | Compile TikZ to PDF via `pdflatex` in a container, then `pdf2svg`. Replaces some of the LLM-SVG path for paper-quality output. |

## Code style

- **Python 3.12+**. We use `from __future__ import annotations`.
- **No mocks in tests**. Integration tests hit real subsystems (the
  CP-SAT solver, the Graphviz binary, the SymPy verifier). See
  `tests/test_graphviz_route.py` for the pattern.
- **Comments only when the *why* is non-obvious.** Don't comment
  what the code does — name it well instead. Comment WHY a design
  decision was made when it would surprise a reader.
- **No `*args` / `**kwargs` passthroughs** in public APIs. Be
  explicit about what each function takes.
- **Tests pass before the PR**:
  `.venv/bin/python -m pytest tests/ studio/ khayyam_math/tests/ -q`
  must exit 0 (275 tests on `main`).
- **One concern per PR**: don't bundle a typo fix with a 2000-LOC
  refactor.

### Project-specific rules new contributors often get wrong

- **Always deploy via `infra/deploy.sh`**, never bare `cdk deploy`.
  The wrapper preserves HTTPS / ACM / Route 53 config that bare CDK
  would otherwise drop. See [docs/DEPLOY.md](docs/DEPLOY.md).
- **No fallback to LLM-drawn SVG from a deterministic route.** Every
  per-domain template needs a canonical fallback within the
  deterministic path. If your template can't render the args it
  was given, return `None` so the router falls through to FDL — do
  not have the template itself call gpt-4o to "freehand" a figure.
- **Narrate the IDEA, never the picture.** The narration must NOT
  say "we see…", "on the left…", "the figure shows…", "A is
  connected to B…". The eye already recognises components; the
  audio must add math CONTENT. The structural critic flags
  boilerplate openers.
- **Match the user's language.** Don't hardcode English in any
  user-facing string the LLM produces. The post-processor
  `localise_narration` handles non-English prompts.
- **Length follows the question, not a sentence cap.** A "why?" /
  "how does X work?" question deserves a 3-6 paragraph answer
  with reasoning + formulas + worked example. See the
  SYSTEM_PROMPT.
- **Refinement turns: read [docs/REFINEMENT.md](docs/REFINEMENT.md)
  first.** The three-case (A / B / C) model is non-obvious and
  changing it incorrectly silently regresses every multi-turn
  session.

## Pull-request flow

1. Fork → branch from `main` (`feat/foo` or `fix/bar`)
2. Implement + add tests
3. Push, open a PR against `khayyam-math/khayyam-math:main`
4. CI runs the test suite + a small Playwright UX audit
5. A maintainer reviews; expect a turnaround of 1-3 days
6. On approval: squash-merge into main

## Issue templates

We have three (`.github/ISSUE_TEMPLATE/`):

- **Bug** — something rendered wrong, missing, or off-canvas
- **Template idea** — a math-operation family you'd like to see
  rendered deterministically (matrix transpose, etc.)
- **Architecture / large change** — discuss before coding; we may
  have context

## What we WON'T accept

- Anything that **regresses the live demo**'s quality on the
  Playwright audit set (`scripts/audit_studio_screenshots.py`)
- Hard-coded API keys, secrets, or AWS account IDs in any committed
  file (`.env` is gitignored; keep it there)
- Adding a new heavy native dependency without prior discussion
  (we already carry graphviz, cairo, piper, ortools — be parsimonious)
- LLM-generated PR descriptions / commit messages that don't reflect
  actual work done. Be honest about what your change does.

## Talk to us before you start (anything substantial)

If your contribution is larger than ~200 lines of code, open an
issue or a discussion FIRST so we agree on the design. This saves
you from having a PR rejected after you've written the whole thing.

## Code of conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Short version: be
kind, be honest, no harassment. We follow Contributor Covenant 2.1.

## Recognition

Every accepted PR gets you a line in `CONTRIBUTORS.md` (auto-generated
on each release). For larger contributions — a new template family,
a route, a research result — we'll list you as a co-author on the
next paper revision if you want that recognition.
