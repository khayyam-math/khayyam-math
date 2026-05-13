"""Prompt → template router.

A tiny gpt-4o-mini classifier decides whether a user prompt maps to
one of our deterministic templates and extracts the structured
arguments (matrices, equations) as JSON.

Returns either a ``(template_name, kwargs)`` tuple ready to call, or
None when no template matches — caller falls back to the LLM-SVG
path.

Cost: one ~150-token classifier call per prompt at gpt-4o-mini's
pricing ≈ $0.0001/prompt.  Latency ≈ 600-1200 ms (the model is fast
on short prompts).  Both numbers are negligible vs the 30-90 s the
LLM-SVG path takes — even when the router MISSES, we've only paid a
small fixed overhead.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional, Tuple

import httpx

log = logging.getLogger("template_router")


_ROUTER_SYSTEM = """You are a classifier for math-figure prompts.  Decide if the prompt is asking for one of these specific matrix operations.  If yes, extract the matrix data.  If no, return null.

Supported templates:

  matrix_multiplication — "multiply A and B", "A * B", "show A times B"
    args: {"a": [[...]], "b": [[...]]}

  matrix_transpose — "transpose A", "A^T", "show the transpose of A"
    args: {"a": [[...]]}

  matrix_determinant — "determinant of A", "compute det(A)", "find |A|"
    args: {"a": [[...]]}   (must be square)

  matrix_inverse — "inverse of A", "compute A^-1", "show how to invert A"
    args: {"a": [[...]]}   (must be square)

  system_of_equations — "solve 2x+3y=8, x-y=1", "Ax = b system"
    args: {"coeffs": [[...]], "rhs": [...]}

  state_diagram — "DFA accepting strings ending in 1", "draw a 3-state
    automaton that recognizes …", "state machine for …", "FSA for …"
    args: {
      "states": [{"id": "q0", "label": "q0", "initial": true|false,
                  "accept": true|false}, ...],
      "transitions": [{"source": "q0", "target": "q1", "label": "a"}, ...]
    }
    For "show a DFA / state machine for X", infer the states +
    transitions that recognize the described language.  At LEAST one
    state should have "initial": true and at least one should have
    "accept": true unless the prompt explicitly says otherwise.

Rules:
1. Only return a template if the prompt CLEARLY maps to one of the above.  Vague matrix questions like "explain matrix multiplication" without specific matrices should return null — those need the LLM-SVG path for an illustrative figure.
2. If the prompt names matrices but doesn't give entries (e.g. "multiply two 2x2 matrices A and B" without saying what A and B contain), invent small simple entries (1-9, no zeros except where natural) so the template renders something meaningful.
3. System-of-equations: parse linear equations into coeffs (each row = one equation's coefficients in variable order) and rhs (right-hand side).  Variables appearing once on the left of "=" with their coefficients; rhs is whatever's on the right.

Respond with ONLY a JSON object in one of these shapes (no prose, no markdown):
  {"template": "matrix_multiplication", "args": {"a": [[1,2],[3,4]], "b": [[5,6],[7,8]]}}
  {"template": null}
"""


async def classify_prompt(
    prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    timeout_s: float = 8.0,
) -> Optional[Tuple[str, dict[str, Any]]]:
    """Ask gpt-4o-mini whether this prompt fits a known template.

    Returns ``(template_name, args)`` or None.  Any error (network,
    parse, unknown template) returns None so the caller falls back
    to the LLM-SVG path.
    """
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _ROUTER_SYSTEM},
            {"role": "user", "content": prompt.strip()},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers, json=payload,
            )
        if r.status_code != 200:
            log.warning("router: non-200 from %s: %d", model, r.status_code)
            return None
        data = r.json()
        content = data["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
        log.warning("router: request failed: %s: %s", type(exc).__name__, exc)
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        log.warning("router: returned non-JSON: %s", content[:120])
        return None
    template = parsed.get("template")
    if not template or not isinstance(template, str):
        return None
    args = parsed.get("args")
    if not isinstance(args, dict):
        return None
    return template, args


# Map template name → callable.  The callables are imported lazily
# so test runs that monkey-patch matrix.py see the patched version.
_DISPATCH: dict[str, Callable[..., tuple[str, list[dict]]]] = {}


def _build_dispatch() -> None:
    global _DISPATCH
    if _DISPATCH:
        return
    from studio.templates import (
        matrix_multiplication, matrix_transpose,
        matrix_determinant, matrix_inverse, system_of_equations,
        state_diagram,
    )
    _DISPATCH = {
        "matrix_multiplication": matrix_multiplication,
        "matrix_transpose": matrix_transpose,
        "matrix_determinant": matrix_determinant,
        "matrix_inverse": matrix_inverse,
        "system_of_equations": system_of_equations,
        "state_diagram": state_diagram,
    }


def render_template(name: str, args: dict[str, Any]
                    ) -> Optional[tuple[str, list[dict]]]:
    """Run the named template with the extracted args.  Returns
    ``(svg, narration)`` on success, None on unknown template /
    bad args / template-internal validation failure."""
    _build_dispatch()
    fn = _DISPATCH.get(name)
    if fn is None:
        return None
    try:
        return fn(**args)
    except (TypeError, ValueError, KeyError) as exc:
        log.warning("template %s rejected args: %s: %s",
                    name, type(exc).__name__, exc)
        return None
