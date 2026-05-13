"""Template-based figure generation.

A template is a Python function that takes structured input
(matrices as lists, equations as expressions, etc.) and produces
deterministic SVG + narration script — bypassing the LLM-pixel-
placement step that's responsible for ~80% of the layout bugs we
keep patching.

The LLM still picks WHICH template to call and WHAT values to fill
in, but never picks coordinates.  Layout is hand-tuned per template
and tested against the canvas viewer; no critic retries needed.

This is a spike (1 template, matrix_multiplication) to validate the
approach before committing to a 10-template library.
"""
from studio.templates.matrix import (
    matrix_multiplication,
    matrix_transpose,
    matrix_determinant,
    matrix_inverse,
    system_of_equations,
)

__all__ = [
    "matrix_multiplication",
    "matrix_transpose",
    "matrix_determinant",
    "matrix_inverse",
    "system_of_equations",
]
