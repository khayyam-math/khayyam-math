"""Unit + smoke tests for the Graphviz route."""
from __future__ import annotations

import pytest

from studio.templates.graphviz_route import (
    GRAPHVIZ_SYSTEM_PROMPT, extract_dot_from_response,
    is_graphviz_binary_available, is_graphviz_prompt,
    render_graphviz, suggest_engine,
)


# ── classifier ────────────────────────────────────────────────────


@pytest.mark.parametrize("prompt, expected", [
    ("show a DFA for L = (a|b)*ab", True),
    ("draw a Turing machine that decides L = {0^n 1^n}", True),
    ("Hasse diagram for the divisibility lattice on 12", True),
    ("binary search tree containing [3,1,4,1,5,9]", True),
    ("show the Petersen graph", True),
    ("DAG of operations in a small Bayesian network", True),
    ("illustrate the Pythagorean theorem", False),
    ("matrix inverse of [[1,2],[3,4]]", False),
    ("graph y = sin x from 0 to 2pi", False),
    ("Venn diagram for A union B", False),
])
def test_classifier(prompt, expected):
    assert is_graphviz_prompt(prompt) is expected


@pytest.mark.parametrize("prompt, expected_engine", [
    ("show the Petersen graph", "circo"),
    ("Cayley graph of D_4", "circo"),
    ("DAG with 5 nodes", "dot"),
    ("binary tree of depth 3", "dot"),
    ("state diagram for L = a*b*", "dot"),
])
def test_engine_suggestion(prompt, expected_engine):
    assert suggest_engine(prompt) == expected_engine


# ── DOT extraction ────────────────────────────────────────────────


def test_extract_fenced_dot():
    text = """Here you go:

```dot
strict digraph G {
  a -> b;
}
```
"""
    out = extract_dot_from_response(text)
    assert out is not None
    assert "digraph G" in out
    assert "a -> b" in out


def test_extract_raw_dot():
    text = "strict digraph G { q0 -> q1 [label=\"a\"]; }"
    out = extract_dot_from_response(text)
    assert out == text


def test_extract_rejects_non_dot():
    assert extract_dot_from_response("Sorry, I can't help.") is None
    assert extract_dot_from_response("") is None
    assert extract_dot_from_response(None) is None


def test_system_prompt_mentions_dot():
    assert "DOT" in GRAPHVIZ_SYSTEM_PROMPT
    assert "shape=" in GRAPHVIZ_SYSTEM_PROMPT
    assert "label=" in GRAPHVIZ_SYSTEM_PROMPT


# ── render (binary-dependent) ─────────────────────────────────────


_HAS_DOT = is_graphviz_binary_available()


@pytest.mark.skipif(not _HAS_DOT, reason="graphviz `dot` binary not installed")
def test_render_simple():
    dot = """strict digraph G {
  rankdir=LR;
  node [shape=circle];
  a -> b [label="x"];
  b -> c [label="y"];
}"""
    svg = render_graphviz(dot, engine="dot")
    assert svg is not None
    assert "<svg" in svg
    assert "</svg>" in svg
    # The labels should appear in the rendered output.
    assert ">a<" in svg or "<text>a" in svg or "text>a" in svg


@pytest.mark.skipif(not _HAS_DOT, reason="graphviz `dot` binary not installed")
def test_render_state_diagram_with_accepting_state():
    dot = """strict digraph G {
  rankdir=LR;
  node [shape=circle, style=filled, fillcolor=lightblue];
  start [shape=point, fillcolor=black];
  q0; q1;
  q2 [shape=doublecircle];
  start -> q0;
  q0 -> q1 [label="a"];
  q1 -> q2 [label="b"];
}"""
    svg = render_graphviz(dot, engine="dot")
    assert svg is not None
    assert "<svg" in svg
    # The doublecircle accepting state should leave a marker in the SVG.
    assert "ellipse" in svg or "circle" in svg


@pytest.mark.skipif(not _HAS_DOT, reason="graphviz `dot` binary not installed")
def test_render_rejects_bad_dot():
    svg = render_graphviz("this is not dot syntax {}{}{", engine="dot")
    assert svg is None


@pytest.mark.skipif(not _HAS_DOT, reason="graphviz `dot` binary not installed")
def test_render_unknown_engine_falls_back():
    dot = "strict digraph G { a -> b; }"
    svg = render_graphviz(dot, engine="not-a-real-engine")
    assert svg is not None
