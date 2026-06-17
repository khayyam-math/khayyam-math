"""Sphere surface-area renderer + semantic-routing tests.

Added after the live system answered "calculate the area of a sphere" with
the VOLUME figure — the template router matched the token "sphere" and never
distinguished area (4πr²) from volume (4/3 πr³).
"""
from __future__ import annotations

import asyncio
import math
import re
from xml.dom import minidom

from studio.templates import sphere_area as S


def test_routing_area_yes_volume_no():
    assert S.is_sphere_surface_area_prompt("calculate the area of a sphere")
    assert S.is_sphere_surface_area_prompt("surface area of a sphere")
    assert S.is_sphere_surface_area_prompt("what is the area of the sphere")
    # the exact confusion: a VOLUME prompt must NOT hit the area route
    assert not S.is_sphere_surface_area_prompt("volume of a sphere")
    assert not S.is_sphere_surface_area_prompt(
        "prove the volume of a sphere is 4/3 pi r^3")
    assert not S.is_sphere_surface_area_prompt("area of a triangle")


def test_area_formula_and_worked_example_asserted():
    svg, narr = S.render_sphere_surface_area(3.0)
    minidom.parseString(svg)
    assert 4 <= len(narr) <= 9
    # explicit definition statement + the correct AREA formula (not volume)
    assert "A = 4 π r²" in svg
    assert "4/3" in svg            # only in the "not the volume" contrast
    area = 4.0 * math.pi * 9
    assert abs(area - 36 * math.pi) < 1e-9
    assert f"{area:.2f}" in svg     # ≈ 113.10
    # it must NOT present the volume as the answer
    assert "113.10" in svg


def test_narration_highlights_exist_in_svg():
    svg, narr = S.render_sphere_surface_area()
    ids = set(re.findall(r'id="([^"]+)"', svg))
    for phrase in narr:
        for ref in phrase.get("highlight", []):
            assert ref in ids, f"highlight id {ref!r} missing from SVG"


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "calculate the area of a sphere", base_url="", model="", api_key=""))
    assert r.get("template") == "sphere_surface_area"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("sphere_surface_area", r) == []
