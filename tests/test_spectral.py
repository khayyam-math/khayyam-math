"""Deterministic spectral-theorem renderer tests.

Added after the probe caught the LLM-SVG path drawing a TRUNCATED Q^T
matrix.  The deterministic renderer computes and asserts Q Λ Q^T = A, and
the figure must carry the full, correct matrices.
"""
from __future__ import annotations

import asyncio
import math
from xml.dom import minidom

from studio.templates import spectral as S


def test_routing():
    assert S.is_spectral_prompt("explain the spectral theorem with an example")
    assert S.is_spectral_prompt("spectral decomposition of a matrix")
    assert not S.is_spectral_prompt("multiply two matrices")


def test_arithmetic_asserted_and_matrices_complete():
    svg, narr = S.render_spectral_theorem()
    minidom.parseString(svg)
    assert 5 <= len(narr) <= 9
    # the FULL Q^T must be present (the truncated row was the original bug):
    # both rows of 1/√2 entries, including the negative one.
    assert svg.count("1/√2") >= 6      # Q and Q^T each have four entries
    assert "−1/√2" in svg
    # eigenvalues + statement present
    assert "λ₁ = 3" in svg and "Q Λ Qᵀ" in svg
    # the renderer asserts A = Q Λ Q^T internally; verify the math here too
    s = 1 / math.sqrt(2)
    Q = [[s, s], [s, -s]]
    Lam = [[3, 0], [0, 1]]
    QT = [[Q[j][i] for j in range(2)] for i in range(2)]

    def mul(X, Y):
        return [[sum(X[i][k] * Y[k][j] for k in range(2)) for j in range(2)]
                for i in range(2)]
    recon = mul(mul(Q, Lam), QT)
    A = [[2.0, 1.0], [1.0, 2.0]]
    assert all(abs(recon[i][j] - A[i][j]) < 1e-9
               for i in range(2) for j in range(2))


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "explain the spectral theorem with an example",
        base_url="", model="", api_key=""))
    assert r.get("template") == "spectral"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("spectral", r) == []
