"""Phase-4 NP-completeness renderer tests (fully deterministic, no LLM)."""
from __future__ import annotations

from xml.dom import minidom

from studio.templates.np_completeness import (
    is_np_completeness_prompt, render_np_completeness,
)


_VC_SPEC = {
    "problem_x": "Vertex Cover",
    "definition_x": "Given a graph G and integer k, is there a set of at "
                    "most k vertices covering every edge?",
    "np_certificate": "a proposed vertex set of size at most k",
    "known_problem_y": "3-SAT",
    "reduction": "From a 3-SAT formula build a graph with a gadget per "
                 "variable and per clause; set k accordingly.",
    "equivalence": "the 3-SAT formula is satisfiable  ⟺  the graph has a "
                   "vertex cover of size at most k",
}


def test_renders_valid_xml_with_title():
    svg, narration = render_np_completeness(_VC_SPEC)
    assert "<svg" in svg
    minidom.parseString(svg)  # raises if invalid XML
    assert "Vertex Cover is NP-Complete" in svg
    assert "3-SAT" in svg
    assert len(narration) >= 4


def test_no_text_outside_viewbox():
    """Every <text> y is within the 0..640 viewBox (deterministic layout
    means we can assert legibility, not just validity)."""
    import re
    svg, _ = render_np_completeness(_VC_SPEC)
    ys = [float(m) for m in re.findall(r'<text[^>]*y="([0-9.]+)"', svg)]
    assert ys and all(0 <= y <= 640 for y in ys), ys
    xs = [float(m) for m in re.findall(r'<text[^>]*x="([0-9.]+)"', svg)]
    assert all(0 <= x <= 900 for x in xs), xs


def test_missing_fields_still_complete():
    # Only the problem name — every other field falls back gracefully.
    svg, narration = render_np_completeness({"problem_x": "Clique"})
    minidom.parseString(svg)
    assert "Clique is NP-Complete" in svg
    assert "NP-complete" in svg  # conclusion present


def test_classifier_positive():
    for p in ["prove that vertex cover is np-complete",
              "show the partition problem is NP-complete",
              "prove 3-SAT is np complete by reduction"]:
        assert is_np_completeness_prompt(p), p


def test_classifier_negative():
    for p in ["what is NP-completeness?",          # definition, not a proof
              "multiply two matrices",
              "explain the spectral theorem"]:
        assert not is_np_completeness_prompt(p), p
