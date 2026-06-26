"""Deterministic eigenvalue/eigenvector geometry renderer tests.

Added after the probe caught "explain eigenvalues and eigenvectors
geometrically": the LLM figure was fine but the narration falsely claimed
eigenvectors "do not rotate" (wrong for negative eigenvalues, which flip
them).  The deterministic renderer asserts A·v = λv and states the
relationship precisely.
"""
from __future__ import annotations

import asyncio
import re
from xml.dom import minidom

from studio.templates import eigen_geometry as E


def test_routing_geometric_intent_only():
    assert E.is_eigen_geometry_prompt(
        "explain eigenvalues and eigenvectors geometrically")
    assert E.is_eigen_geometry_prompt("what is an eigenvector, intuitively")
    assert E.is_eigen_geometry_prompt(
        "show the geometric meaning of eigenvalues")
    # NOT the spectral-theorem route
    assert not E.is_eigen_geometry_prompt("explain the spectral theorem")
    # NOT a bare compute request (no geometric/conceptual cue)
    assert not E.is_eigen_geometry_prompt(
        "compute the eigenvalues of [[2,1],[1,2]]")
    assert not E.is_eigen_geometry_prompt("draw a circle")


def test_eigenrelation_asserted_and_narration_correct():
    svg, narr = E.render_eigen_geometry()
    minidom.parseString(svg)
    assert 5 <= len(narr) <= 9
    # Re-verify A v = lambda v for the drawn example.
    A = [[2.0, 1.0], [1.0, 2.0]]

    def mv(m, v):
        return (m[0][0] * v[0] + m[0][1] * v[1],
                m[1][0] * v[0] + m[1][1] * v[1])
    for lam, v in [(3.0, (1.0, 1.0)), (1.0, (1.0, -1.0))]:
        Av = mv(A, v)
        assert abs(Av[0] - lam * v[0]) < 1e-9 and abs(Av[1] - lam * v[1]) < 1e-9
    # generic vector must genuinely rotate off its line
    u, Au = (2.0, 0.0), mv(A, (2.0, 0.0))
    assert abs(Au[1] * u[0] - Au[0] * u[1]) > 1e-6

    # The corrected claim must be present, and the FALSE blanket claim absent.
    joined = " ".join(p["speak"] for p in narr).lower()
    assert "reversed if the eigenvalue is negative" in joined
    assert "scalar multiple of itself" in joined
    # must not assert the unconditional falsehood the probe flagged
    assert "eigenvectors do not rotate" not in joined
    assert "eigenvectors don't rotate" not in joined


def test_narration_highlights_exist_in_svg():
    svg, narr = E.render_eigen_geometry()
    ids = set(re.findall(r'id="([^"]+)"', svg))
    for phrase in narr:
        for ref in phrase.get("highlight", []):
            assert ref in ids, f"highlight id {ref!r} missing from SVG"


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "explain eigenvalues and eigenvectors geometrically",
        base_url="", model="", api_key=""))
    assert r.get("template") == "eigen_geometry"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("eigen_geometry", r) == []
