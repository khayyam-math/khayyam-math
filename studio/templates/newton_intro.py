"""Route vague "Newton's method" prompts to a correct deterministic figure.

A user reported (2026-05-31) that the Newton's-method figure's "tangents
are not tangent lines".  Diagnosis: the existing, correct deterministic
renderer ``studio.templates.newton.newton_method`` only fires when the
prompt pins a concrete function f and starting value x₀ (the LLM template
router extracts them).  A VAGUE prompt — "explain Newton's method", "show
how Newton's method finds a root" — supplies no f/x₀, so the figure path
fell through to LLM-SVG, which draws tangent-shaped lines that don't
actually touch the curve.

Fix: when a Newton prompt carries no concrete function, render the
canonical worked example (a root of x² − 2 = 0, i.e. √2, from x₀ = 2)
through the SAME exact renderer.  Prompts that DO pin a function are left
to the template router, which already handles them correctly.
"""
from __future__ import annotations

import re
from typing import Any

from studio.templates.newton import newton_method

# Default worked example: √2 as the positive root of x² − 2.
_DEFAULT_F = "x**2 - 2"
_DEFAULT_X0 = 2.0
_DEFAULT_TITLE = "Newton's Method: a root of x² − 2 (the value √2)"

# A "concrete function" looks like f(x) = …, an explicit power x^2 / x**2,
# or a named function call sqrt(/sin(/cos(/exp(/log(/tan(.  When the prompt
# contains one of these we DEFER to the template router (it extracts the
# real f/x₀); otherwise we own the prompt with the canonical example.
_HAS_FUNCTION = re.compile(
    r"f\s*\(\s*x\s*\)\s*=|f\s*=\s*[^.;]*x|"
    r"\bx\s*\^\s*\d|\bx\s*\*\*\s*\d|"
    r"\b(?:sqrt|sin|cos|tan|exp|log|ln)\s*\(",
    re.I,
)

_NEWTON = re.compile(
    r"newton'?s?\s*[- ]?\s*(?:method|raphson|iteration)|newton-raphson", re.I)


def is_newton_intro_prompt(prompt: str) -> bool:
    """True for a Newton's-method prompt that does NOT pin a concrete f."""
    p = prompt or ""
    if not _NEWTON.search(p):
        return False
    return not _HAS_FUNCTION.search(p)


def render_newton_intro() -> tuple[str, list[dict]]:
    """The canonical √2 example, drawn by the exact deterministic renderer."""
    return newton_method(_DEFAULT_F, _DEFAULT_X0, n_iter=4, title=_DEFAULT_TITLE)


async def generate_newton_intro_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_newton_intro()
