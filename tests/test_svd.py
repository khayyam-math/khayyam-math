"""Deterministic SVD renderer tests.

Added after the probe caught "show the singular value decomposition of a
2x2 matrix" failing on the LLM-SVG path (route=None, text outside viewBox,
overlap, vision review: "lacks orthonormal columns for U and V").  The
deterministic renderer computes and asserts A = U Σ Vᵀ and the
orthonormality of U and V, and carries the full correct matrices.
"""
from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from xml.dom import minidom

from studio.templates import svd as D


def test_routing():
    assert D.is_svd_prompt("show the singular value decomposition of a 2x2 matrix")
    assert D.is_svd_prompt("SVD of a matrix")
    assert D.is_svd_prompt("compute the singular values of this matrix")
    assert not D.is_svd_prompt("spectral decomposition of a symmetric matrix")
    assert not D.is_svd_prompt("multiply two matrices")


def test_arithmetic_asserted_and_orthonormal():
    svg, narr = D.render_svd()
    minidom.parseString(svg)
    assert 5 <= len(narr) <= 9
    # Recompute the decomposition and verify here too.
    s2 = math.sqrt(2.0)
    U = [[1.0, 0.0], [0.0, -1.0]]
    S = [[2.0 * s2, 0.0], [0.0, s2]]
    V = [[1.0 / s2, 1.0 / s2], [1.0 / s2, -1.0 / s2]]
    VT = [[V[j][i] for j in range(2)] for i in range(2)]
    A = [[2.0, 2.0], [-1.0, 1.0]]

    def mul(X, Y):
        return [[sum(X[i][k] * Y[k][j] for k in range(2)) for j in range(2)]
                for i in range(2)]
    recon = mul(mul(U, S), VT)
    assert all(abs(recon[i][j] - A[i][j]) < 1e-9
               for i in range(2) for j in range(2))
    # orthonormality
    Ut = [[U[j][i] for j in range(2)] for i in range(2)]
    I = [[1.0, 0.0], [0.0, 1.0]]
    for M in (mul(Ut, U), mul(VT, V)):
        assert all(abs(M[i][j] - I[i][j]) < 1e-12 for i in range(2)
                   for j in range(2))
    # the figure must carry the full matrices, not a truncated row
    assert svg.count("1/√2") >= 4
    assert "−1/√2" in svg and "2√2" in svg
    assert "σ₁" in svg and "U Σ Vᵀ" in svg


def test_narration_highlights_exist_in_svg():
    svg, narr = D.render_svd()
    ids = set(re.findall(r'id="([^"]+)"', svg))
    for phrase in narr:
        for ref in phrase.get("highlight", []):
            assert ref in ids, f"highlight id {ref!r} missing from SVG"


def test_no_anchor_collisions():
    svg, _ = D.render_svd()
    a = re.findall(r'<text[^>]*\sx="([\-0-9.]+)" y="([\-0-9.]+)"', svg)
    dupes = [k for k, n in Counter(a).items() if n > 1]
    assert not dupes, f"overlapping text anchors: {dupes}"


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "show the singular value decomposition of a 2x2 matrix",
        base_url="", model="", api_key=""))
    assert r.get("template") == "svd"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("svd", r) == []
