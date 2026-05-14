"""SVG → SceneGraph parser.

Pure-Python, uses only `xml.etree.ElementTree` and `re`. Designed to
be cheap (no headless browser, no Cairo rasterisation) so it can run
inline in the express loop and on every record of the corpus generator.

Bbox computation is *approximate*: good enough for a GNN that reasons
in 256-bin quantised coordinates. We handle the transforms we actually
emit (`translate(x,y)`, `translate(x y)`, `matrix(...)`, `scale(s)`,
`scale(sx sy)`) and ignore the rest with a recorded warning.

Element-id heuristics:

- An ``id`` attribute means the LLM (or template code) wanted to
  reference this element by name. Anchored = potential narration
  highlight target. We mark `is_narration_anchor=True` when an id
  starts with one of the prefixes used by the matrix templates and
  the express system prompt (``cell_``, ``matrix_``, ``op_``,
  ``vector_``, ``solution``, ``title``, ``dim_error``, ``axis_``,
  ``point_``, ``highlight_``, …). The model uses this flag to keep
  those elements pinned to their original positions.
- Elements with ``data-caption="1"`` or ids starting with ``caption_``
  are marked `is_caption=True`.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable

from .schema import (
    EDGE_RELATIONS, NODE_TYPES, EdgeFeatures, NodeFeatures, SceneGraph,
)

# All SVG elements live in this namespace.
_SVG_NS = "{http://www.w3.org/2000/svg}"

_NARRATION_ANCHOR_PREFIXES = (
    "cell_", "matrix_", "matrices_", "op_", "vector_", "axis_",
    "point_", "title", "highlight_", "solution", "dim_error",
    "singular_error", "det_", "adj_", "inv_", "row_", "col_",
)

_CAPTION_PREFIXES = ("caption_", "label_", "annot_")

_PROTECTED_PREFIXES = ("title", "axis_label_", "matrix_outer_")


# --------------------------------------------------------------------
# transforms
# --------------------------------------------------------------------

# An affine transform expressed as (a, b, c, d, e, f) where
#   x' = a*x + c*y + e
#   y' = b*x + d*y + f
# (the SVG-spec convention). Identity is (1, 0, 0, 1, 0, 0).
_Affine = tuple[float, float, float, float, float, float]
_IDENTITY: _Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _compose(outer: _Affine, inner: _Affine) -> _Affine:
    """Composition: apply `inner` first, then `outer`."""
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


_TRANSFORM_RE = re.compile(
    r"(translate|matrix|scale)\s*\(([^)]*)\)", re.IGNORECASE,
)


def _parse_transform(s: str | None) -> _Affine:
    if not s:
        return _IDENTITY
    out: _Affine = _IDENTITY
    for kind, args in _TRANSFORM_RE.findall(s):
        nums = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if kind.lower() == "translate":
            tx = nums[0] if nums else 0.0
            ty = nums[1] if len(nums) > 1 else 0.0
            out = _compose(out, (1.0, 0.0, 0.0, 1.0, tx, ty))
        elif kind.lower() == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            out = _compose(out, (sx, 0.0, 0.0, sy, 0.0, 0.0))
        elif kind.lower() == "matrix":
            if len(nums) >= 6:
                out = _compose(
                    out,
                    (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5]),
                )
        # rotate / skew not yet supported; figures we emit don't use them.
    return out


def _apply(t: _Affine, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = t
    return (a * x + c * y + e, b * x + d * y + f)


# --------------------------------------------------------------------
# bbox per element
# --------------------------------------------------------------------

def _local_bbox(
    elem: ET.Element, transform: _Affine,
) -> tuple[float, float, float, float] | None:
    """Best-effort axis-aligned bbox in canvas coordinates.

    Returns None when the element is invisible / has no geometry of its
    own (most ``<g>`` elements — their bbox is computed as the union of
    their children's bboxes by the recursive walker, not here).
    """
    tag = elem.tag.replace(_SVG_NS, "")
    try:
        if tag == "rect":
            x = float(elem.get("x") or 0)
            y = float(elem.get("y") or 0)
            w = float(elem.get("width") or 0)
            h = float(elem.get("height") or 0)
            corners = [
                _apply(transform, x, y),
                _apply(transform, x + w, y),
                _apply(transform, x, y + h),
                _apply(transform, x + w, y + h),
            ]
            return _corners_bbox(corners)
        if tag in ("circle", "ellipse"):
            cx = float(elem.get("cx") or 0)
            cy = float(elem.get("cy") or 0)
            if tag == "circle":
                r = float(elem.get("r") or 0)
                rx, ry = r, r
            else:
                rx = float(elem.get("rx") or 0)
                ry = float(elem.get("ry") or 0)
            corners = [
                _apply(transform, cx - rx, cy - ry),
                _apply(transform, cx + rx, cy - ry),
                _apply(transform, cx - rx, cy + ry),
                _apply(transform, cx + rx, cy + ry),
            ]
            return _corners_bbox(corners)
        if tag == "line":
            x1 = float(elem.get("x1") or 0)
            y1 = float(elem.get("y1") or 0)
            x2 = float(elem.get("x2") or 0)
            y2 = float(elem.get("y2") or 0)
            corners = [
                _apply(transform, x1, y1),
                _apply(transform, x2, y2),
            ]
            return _corners_bbox(corners)
        if tag in ("polyline", "polygon"):
            pts = (elem.get("points") or "").strip()
            coords = [float(x) for x in re.split(r"[\s,]+", pts) if x]
            corners = []
            for i in range(0, len(coords) - 1, 2):
                corners.append(_apply(transform, coords[i], coords[i + 1]))
            return _corners_bbox(corners) if corners else None
        if tag in ("text", "tspan"):
            x = float(elem.get("x") or 0)
            y = float(elem.get("y") or 0)
            text = "".join(elem.itertext()).strip()
            fs = float(elem.get("font-size") or 14)
            # Rough text bbox: width = 0.55 × font-size × char-count,
            # height = 1.1 × font-size, anchored top-left at (x, y - fs).
            w = 0.55 * fs * max(1, len(text))
            h = 1.1 * fs
            corners = [
                _apply(transform, x, y - fs),
                _apply(transform, x + w, y - fs),
                _apply(transform, x, y - fs + h),
                _apply(transform, x + w, y - fs + h),
            ]
            return _corners_bbox(corners)
        if tag == "path":
            # Pull out every numeric run in the `d` attribute. Treat
            # adjacent pairs as candidate (x, y) points. Wildly
            # approximate, but adequate for "is this path on-canvas?".
            d = elem.get("d") or ""
            nums = [float(x) for x in re.findall(
                r"-?\d+\.?\d*", d,
            )]
            corners = []
            for i in range(0, len(nums) - 1, 2):
                corners.append(_apply(transform, nums[i], nums[i + 1]))
            return _corners_bbox(corners) if corners else None
        if tag == "image":
            x = float(elem.get("x") or 0)
            y = float(elem.get("y") or 0)
            w = float(elem.get("width") or 0)
            h = float(elem.get("height") or 0)
            corners = [
                _apply(transform, x, y),
                _apply(transform, x + w, y + h),
            ]
            return _corners_bbox(corners)
    except (ValueError, TypeError):
        return None
    return None


def _corners_bbox(
    corners: list[tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    if not corners:
        return None
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    x0, y0 = min(xs), min(ys)
    x1, y1 = max(xs), max(ys)
    return (x0, y0, x1 - x0, y1 - y0)


def _union(
    a: tuple[float, float, float, float] | None,
    b: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if a is None:
        return b
    if b is None:
        return a
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0 = min(ax, bx)
    y0 = min(ay, by)
    x1 = max(ax + aw, bx + bw)
    y1 = max(ay + ah, by + bh)
    return (x0, y0, x1 - x0, y1 - y0)


# --------------------------------------------------------------------
# id flags
# --------------------------------------------------------------------

def _is_narration_anchor(elem_id: str | None) -> bool:
    if not elem_id:
        return False
    return any(elem_id.startswith(p) for p in _NARRATION_ANCHOR_PREFIXES)


def _is_caption(elem: ET.Element, elem_id: str | None) -> bool:
    if elem.get("data-caption") == "1":
        return True
    if elem_id and any(elem_id.startswith(p) for p in _CAPTION_PREFIXES):
        return True
    return False


def _is_protected(elem_id: str | None) -> bool:
    if not elem_id:
        return False
    return any(elem_id.startswith(p) for p in _PROTECTED_PREFIXES)


def _node_type(tag: str) -> str:
    t = tag.replace(_SVG_NS, "")
    return t if t in NODE_TYPES else "other"


# --------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------

@dataclass
class ParseResult:
    """Container for parser output + recoverable warnings."""
    graph: SceneGraph
    warnings: list[str]


def parse_svg(svg_text: str) -> ParseResult:
    """Parse an SVG string into a SceneGraph.

    Returns a `ParseResult` rather than raising on minor issues —
    corpus generation needs to be forgiving (many LLM-emitted SVGs
    have small spec violations we can still learn from).
    """
    warnings: list[str] = []
    nodes: list[NodeFeatures] = []
    edges: list[EdgeFeatures] = []
    auto_ix = 0

    def _next_anon_id(prefix: str) -> str:
        nonlocal auto_ix
        auto_ix += 1
        return f"__anon_{prefix}_{auto_ix}"

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        warnings.append(f"parse_error: {exc}")
        # Fall back to empty scene graph rather than failing — the
        # exporter will filter these out.
        return ParseResult(
            graph=SceneGraph(
                nodes=[], edges=[], viewbox=(0, 0, 900, 600),
                canvas_w=900, canvas_h=600,
            ),
            warnings=warnings,
        )

    # viewBox + canvas size
    vb = root.get("viewBox")
    if vb:
        parts = [float(x) for x in re.split(r"[\s,]+", vb.strip()) if x]
        if len(parts) == 4:
            viewbox = (parts[0], parts[1], parts[2], parts[3])
        else:
            warnings.append(f"bad_viewBox: {vb!r}")
            viewbox = (0.0, 0.0, 900.0, 600.0)
    else:
        viewbox = (0.0, 0.0, 900.0, 600.0)

    def _int_or(d: str | None, fallback: float) -> int:
        try:
            return int(float(d)) if d else int(fallback)
        except (ValueError, TypeError):
            return int(fallback)

    canvas_w = _int_or(root.get("width"), viewbox[2])
    canvas_h = _int_or(root.get("height"), viewbox[3])

    # Recursive walk: capture per-element bbox in canvas coordinates,
    # accumulating transforms.

    def _walk(
        elem: ET.Element,
        parent_id: str | None,
        top_level_id: str | None,
        transform: _Affine,
    ) -> NodeFeatures | None:
        tag = elem.tag
        if tag in (f"{_SVG_NS}desc", f"{_SVG_NS}metadata",
                   f"{_SVG_NS}defs", f"{_SVG_NS}style",
                   f"{_SVG_NS}title"):
            # Decorative / non-geometry; skip but don't warn.
            return None
        local_t = _parse_transform(elem.get("transform"))
        cur_t = _compose(transform, local_t)
        elem_id = elem.get("id") or _next_anon_id(
            _node_type(tag),
        )

        bbox = _local_bbox(elem, cur_t)

        # Recurse — children produce their own NodeFeatures + edges.
        child_features: list[NodeFeatures] = []
        for child in list(elem):
            if not child.tag.startswith(_SVG_NS):
                continue
            child_feat = _walk(
                child,
                parent_id=elem_id,
                top_level_id=top_level_id or elem_id,
                transform=cur_t,
            )
            if child_feat is not None:
                child_features.append(child_feat)

        # For groups, the bbox is the union of children's bboxes
        # (in canvas coords — children already have transforms applied).
        if tag == f"{_SVG_NS}g" or bbox is None:
            for cf in child_features:
                bbox = _union(bbox, cf.bbox)
        if bbox is None:
            # Truly empty element — invisible. Drop it so the GNN
            # doesn't burn capacity on noise nodes.
            return None

        # Text content + font features.
        text_content = ""
        font_size = 0.0
        if tag in (f"{_SVG_NS}text", f"{_SVG_NS}tspan"):
            text_content = "".join(elem.itertext()).strip()
            try:
                font_size = float(elem.get("font-size") or 14)
            except (ValueError, TypeError):
                font_size = 14.0
        try:
            stroke_width = float(elem.get("stroke-width") or 0)
        except (ValueError, TypeError):
            stroke_width = 0.0

        node = NodeFeatures(
            id=elem_id,
            type=_node_type(tag),
            bbox=bbox,
            text=text_content,
            font_size=font_size,
            stroke_width=stroke_width,
            parent_id=parent_id,
            top_level_group_id=top_level_id,
            is_narration_anchor=_is_narration_anchor(elem.get("id")),
            is_caption=_is_caption(elem, elem.get("id")),
            is_protected=_is_protected(elem.get("id")),
            raw_attrs={},
        )
        nodes.append(node)

        # parent_of edge
        if parent_id is not None:
            edges.append(EdgeFeatures(
                src_id=parent_id, dst_id=elem_id, relation="parent_of",
            ))
        # sibling edges among this element's children
        for i, ci in enumerate(child_features):
            for cj in child_features[i + 1:]:
                edges.append(EdgeFeatures(
                    src_id=ci.id, dst_id=cj.id, relation="sibling_of",
                ))
        return node

    if root.tag == f"{_SVG_NS}svg":
        for child in list(root):
            if child.tag.startswith(_SVG_NS):
                _walk(child, parent_id=None, top_level_id=None,
                      transform=_IDENTITY)
    else:
        warnings.append(f"unexpected_root: {root.tag!r}")

    # Narration-co-anchor edges: any two anchor nodes that share a
    # top_level_group_id are likely highlighted together.
    anchors_by_top = {}
    for n in nodes:
        if n.is_narration_anchor and n.top_level_group_id:
            anchors_by_top.setdefault(n.top_level_group_id, []).append(n)
    for group_nodes in anchors_by_top.values():
        for i, a in enumerate(group_nodes):
            for b in group_nodes[i + 1:]:
                edges.append(EdgeFeatures(
                    src_id=a.id, dst_id=b.id,
                    relation="narration_co_anchor",
                ))

    graph = SceneGraph(
        nodes=nodes, edges=edges, viewbox=viewbox,
        canvas_w=canvas_w, canvas_h=canvas_h,
    )
    return ParseResult(graph=graph, warnings=warnings)


def iter_node_ids(graph: SceneGraph) -> Iterable[str]:
    yield from (n.id for n in graph.nodes)
