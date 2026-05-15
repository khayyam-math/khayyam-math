# Contributing to Khayyam Math

Thanks for being here. This project exists to make math learning
better through AI-generated voice-narrated figures. Contributions in
any form — code, docs, templates, bug reports, ideas — are welcome.

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
| **More deterministic templates** — Fourier series, slope fields, vector-addition diagrams, Riemann sums, more linalg ops | medium | See `studio/templates/matrix.py` as the pattern. Each template is a pure-Python function returning `(svg_string, narration_list)`. |
| **Localisation** — Arabic, French, Spanish, Hindi narration voices | medium | piper-TTS supports many languages; add a voice config and a per-locale narration template. |
| **Better Graphviz coverage** — Turing-machine `tape` visualisation, recursion-tree templates | medium | Extend `studio/templates/graphviz_route.py`. |
| **LaTeX scrubber rules** — more LaTeX commands → Unicode mappings | easy | `_LATEX_REPLACEMENTS` in `studio/express.py`. PRs welcome. |
| **CP-SAT objective tuning** — better weights for overlap vs displacement vs label-anchor pinning | hard | `studio/layout_planner.py`. Requires running the screenshot-audit harness on a held-out prompt set. |
| **Real layout-quality scorer corpus** — human-labelled (good, bad) layout pairs | hard | Current corpus is synthetic perturbation + filtered telemetry. Human labels would meaningfully improve the +2 pp pass-rate win. |
| **TikZ → SVG route** — for publication-quality math figures | hard | Compile TikZ to PDF via `pdflatex` in a container, then `pdf2svg`. Replaces some of the LLM-SVG path for paper-quality output. |
| **matplotlib server-side route** — for function plots (`graph y = sin x from 0 to 2π`) | medium | Server-side Python exec of matplotlib code; export to SVG. |

## Code style

- **Python 3.12+**. We use `from __future__ import annotations`.
- **No mocks in tests**. Integration tests hit real subsystems (the
  CP-SAT solver, the Graphviz binary, the test parser). See
  `tests/test_graphviz_route.py` for the pattern.
- **Comments only when the *why* is non-obvious.** Don't comment
  what the code does — name it well instead. Comment WHY a design
  decision was made when it would surprise a reader.
- **No `*args` / `**kwargs` passthroughs** in public APIs. Be
  explicit about what each function takes.
- **Tests pass before the PR**: `.venv/bin/python -m pytest -q` must
  exit 0.
- **One concern per PR**: don't bundle a typo fix with a 2000-LOC
  refactor.

## Pull-request flow

1. Fork → branch from `main` (`feat/foo` or `fix/bar`)
2. Implement + add tests
3. Push, open a PR against `arashkermaniprojects/khayyam-math:main`
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
