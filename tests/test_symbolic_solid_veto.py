"""The symbolic route must not swallow named-solid geometry.

Field report 2026-07-09, complaint "The sphere is not drawn": the
prompt "Prove the formula for the volume of a sphere of radius R …
Show the integral setup V = ∫…" reached ``/studio`` and came back as a
matplotlib equation card — algebraically correct, and with no sphere
anywhere on it.  ``is_symbolic_prompt`` is a pure substring test and
the word "integral" was enough to claim the prompt, several routes
before ``volume_of_sphere`` ever got a look.

These tests pin both directions: named-solid geometry falls through,
and ordinary calculus still gets the exact-symbolic treatment.
"""
from __future__ import annotations

import pytest

from studio.templates.symbolic_route import is_symbolic_prompt


# Reached the symbolic route only via a keyword; the real deliverable
# is a picture of the solid, which the geometry templates draw.
GEOMETRY = [
    "Prove the formula for the volume of a sphere of radius R using "
    "calculus. Show the integral setup V = int_{-R}^{R} pi(R^2 - x^2) dx",
    "derive the volume of a sphere by integrating disks",
    "surface area of a sphere by integration",
    "use an integral to find the volume of a cone",
    "find the volume of a cylinder with an integral",
    "compute the surface area of a torus using a double integral",
]

# Genuine symbolic-math work with no named solid to draw — the exact
# SymPy answer is the best available output and must keep the route.
SYMBOLIC = [
    "Compute the integral of x^2 from 0 to 1",
    "find the derivative of sin(x)*exp(x)",
    "hessian of f(x,y) = x^2 + y^2",
    "classify the critical points of f(x,y) = x^3 - 3xy",
    "evaluate the limit of sin(x)/x as x approaches 0",
    # No NAMED solid, so there is no geometry template to fall through
    # to; vetoing this one would trade an exact answer for LLM-SVG.
    "Find the volume of the solid of revolution obtained by rotating "
    "y = x^2 about the x-axis using an integral",
]

# Never touched the symbolic route to begin with.
NOT_SYMBOLIC = [
    "draw the unit circle",
    "prove the volume of a sphere",     # no calculus keyword at all
    "what is a vector space",
]


@pytest.mark.parametrize("prompt", GEOMETRY)
def test_named_solid_geometry_falls_through(prompt):
    assert is_symbolic_prompt(prompt) is False, (
        "symbolic route would render an equation card with no solid on it"
    )


@pytest.mark.parametrize("prompt", SYMBOLIC)
def test_ordinary_calculus_keeps_the_symbolic_route(prompt):
    assert is_symbolic_prompt(prompt) is True


@pytest.mark.parametrize("prompt", NOT_SYMBOLIC)
def test_unrelated_prompts_unaffected(prompt):
    assert is_symbolic_prompt(prompt) is False


def test_sphere_volume_reaches_its_own_template():
    """End of the chain: the prompt must land on volume_of_sphere.

    Every gate that runs BEFORE the template router has to decline, or
    the sphere never gets drawn no matter how good the template is.
    """
    from studio.templates.matplotlib_route import is_matplotlib_prompt
    from studio.templates.sphere_area import is_sphere_surface_area_prompt
    from studio.templates.router import render_template

    prompt = GEOMETRY[0]
    assert not is_symbolic_prompt(prompt)
    assert not is_matplotlib_prompt(prompt)
    assert not is_sphere_surface_area_prompt(prompt)   # area ≠ volume

    rendered = render_template("volume_of_sphere",
                               {"radius": 1.0, "title": ""})
    assert rendered is not None
    svg, narration = rendered
    assert '<circle id="sphere"' in svg, "the template drew no sphere"
    assert narration
