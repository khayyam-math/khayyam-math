"""S4-Geo Layout — coordinate-honoring layout pass for geometric figures.

Sevim's default S4 (``s4_layout.layout``) is Sugiyama: it places nodes by
graph topology, not by the geometry they represent.  That works for
concept-diagrams ("decision tree → feature space") but produces nonsense
for math figures ("place point A at angle 0 on the unit circle").

This module is the alternative.  When a canvas is in math mode and nodes
carry explicit coordinates in their ``meta`` dict, we honour those
coordinates: the canvas becomes a real Cartesian plane, points sit at
their actual positions, edges become straight lines between them, and
the y-axis is flipped so up-is-up.

Coordinate metadata recognised
------------------------------
  point:   ``meta = {"x": 1.0, "y": 0.5}``
  circle:  ``meta = {"cx": 0.0, "cy": 0.0, "r": 1.0}``
  axes:    ``meta = {"xmin": -2, "xmax": 2, "ymin": -2, "ymax": 2}``
           (always treated as figure-spanning even without coords)

Shapes without any of the above ("a label / equation / concept node")
are placed in a strip across the top of the canvas.

Edge placement
--------------
``PlacedConn.points`` is set to ``[center_of_src, center_of_dst]`` so the
existing S5 renderer draws a straight line between the two centres.  For
points (small dots), centre ≈ boundary, which is what you want.
"""
from __future__ import annotations

from .ir import PlacedConn, PlacedGraph, PlacedShape, VisualGraph, VisualShape


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def geometric_layout(
    visual: VisualGraph,
    canvas_w: float = 700,
    canvas_h: float = 440,
    pad: float = 30.0,
    label_strip_h: float = 70.0,
) -> PlacedGraph:
    """Lay out a math figure honouring coordinates in ``meta``.

    Args:
        visual: VisualGraph from S3.
        canvas_w / canvas_h: target canvas size (px).
        pad: outer margin (px).
        label_strip_h: vertical strip across the top reserved for shapes
            without coordinates (labels, equations, concept nodes).

    Returns:
        PlacedGraph ready for ``s5_render.render``.
    """
    geom_shapes: list[VisualShape] = []
    captions: list[VisualShape] = []
    label_shapes: list[VisualShape] = []
    for s in visual.shapes:
        if s.primitive == "caption":
            # Captions are placed *last*, in margins, with leader lines back
            # to their math anchor — they must not cover the figure they
            # explain.
            captions.append(s)
        elif _is_geometric(s):
            geom_shapes.append(s)
        else:
            label_shapes.append(s)

    # ---------- math-coord bounding box ----------
    xmin, xmax, ymin, ymax = _math_bbox(geom_shapes)

    # ---------- canvas region available for the figure ----------
    has_labels = bool(label_shapes)
    geom_top = pad + label_strip_h if has_labels else pad
    geom_left = pad
    geom_w = canvas_w - 2 * pad
    geom_h = canvas_h - geom_top - pad

    # Aspect-preserving scale (math units → canvas px).  Math y is up;
    # canvas y is down.
    span_x = max(xmax - xmin, 1e-6)
    span_y = max(ymax - ymin, 1e-6)
    scale = min(geom_w / span_x, geom_h / span_y)

    used_w = span_x * scale
    used_h = span_y * scale
    # Pixel offset of the math origin (0,0) on the canvas.
    ox = geom_left + (geom_w - used_w) / 2 - xmin * scale
    oy = geom_top + (geom_h - used_h) / 2 + ymax * scale

    def to_canvas(mx: float, my: float) -> tuple[float, float]:
        return (ox + mx * scale, oy - my * scale)

    # ---------- place geometric shapes ----------
    placed_shapes: list[PlacedShape] = []
    centers: dict[str, tuple[float, float]] = {}
    geom_bboxes: list[tuple[float, float, float, float]] = []  # (x, y, w, h)
    point_records: list[tuple[VisualShape, float, float]] = []  # (vs, cx, cy)

    for vs in geom_shapes:
        cx_canvas, cy_canvas, ps_x, ps_y = _place_geom_shape(
            vs, to_canvas, scale, xmin, xmax, ymin, ymax)
        placed_shapes.append(PlacedShape(shape=vs, x=ps_x, y=ps_y))
        centers[vs.nid] = (cx_canvas, cy_canvas)
        geom_bboxes.append((ps_x, ps_y, vs.width, vs.height))
        if vs.primitive == "point":
            point_records.append((vs, cx_canvas, cy_canvas))

    # ---------- C1: resolve point-label collisions ----------
    # Each point label sits at a fixed offset from its dot.  When two
    # points are coordinate-close (e.g. three vertices of a clause cluster
    # in 3SAT → 3-clique), their default "below" labels stack and become
    # unreadable.  Pick a non-colliding side per point in greedy order.
    _assign_label_sides(point_records)

    # ---------- C2: extend bboxes to include label rectangles ----------
    # Captions are placed in canvas margins computed from these bboxes.
    # Without this, the area where labels live looks empty to the
    # caption placer and a caption lands on top of the labels.
    # Each point gets its label rect added (using the side picked by C1).
    # Other labeled shapes (segments with measurement labels, etc.) get
    # a generous label rect placed nearby — too generous is fine, the
    # only failure mode is captions getting pushed slightly farther
    # out than strictly necessary.
    for vs, cx_canvas, cy_canvas in point_records:
        geom_bboxes.append(_label_bbox(vs, cx_canvas, cy_canvas))
    for vs in geom_shapes:
        if vs.primitive == "point" or not (vs.label or "").strip():
            continue
        # Roughly: the label sits along/near the shape; reserve a small
        # halo around its bbox.  The halo accounts for the label being
        # rendered just outside the shape on most primitives.
        center = centers.get(vs.nid)
        if center is None:
            continue
        cx_c, cy_c = center
        lw, lh = _label_dims(vs)
        halo = 6.0
        # Centre the label rect on the shape — coarse but safe.
        geom_bboxes.append((
            cx_c - lw / 2 - halo,
            cy_c - lh / 2 - halo,
            lw + 2 * halo,
            lh + 2 * halo,
        ))

    # ---------- place label shapes in the top strip ----------
    if label_shapes:
        # Pack left-to-right, vertical centre on the strip.
        total_w = sum(vs.width for vs in label_shapes) + 12 * (len(label_shapes) - 1)
        x_cursor = max(pad, (canvas_w - total_w) / 2)
        strip_mid_y = pad + label_strip_h / 2
        for vs in label_shapes:
            ps_x = x_cursor
            ps_y = strip_mid_y - vs.height / 2
            placed_shapes.append(PlacedShape(shape=vs, x=ps_x, y=ps_y))
            centers[vs.nid] = (ps_x + vs.width / 2, ps_y + vs.height / 2)
            x_cursor += vs.width + 12

    # ---------- place captions in margin strips with leader lines ----------
    if captions:
        caption_placements = _place_captions_in_margins(
            captions=captions,
            geom_bboxes=geom_bboxes,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            pad=pad,
            label_strip_h=label_strip_h if label_shapes else 0.0,
            to_canvas=to_canvas,
        )
        for vs, ps_x, ps_y in caption_placements:
            placed_shapes.append(PlacedShape(shape=vs, x=ps_x, y=ps_y))
            centers[vs.nid] = (ps_x + vs.width / 2, ps_y + vs.height / 2)

    # ---------- place connectors as straight lines between centres ----------
    placed_conns: list[PlacedConn] = []
    for vc in visual.connectors:
        c1 = centers.get(vc.from_nid)
        c2 = centers.get(vc.to_nid)
        if c1 is None or c2 is None:
            continue
        placed_conns.append(PlacedConn(conn=vc, points=[c1, c2]))

    return PlacedGraph(
        shapes=placed_shapes,
        conns=placed_conns,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Point-size in canvas px.  Constant — does not scale with figure size, so
# small features remain visible regardless of the math-coord range.
_POINT_PX = 18.0


def _is_geometric(s: VisualShape) -> bool:
    """True if this shape should be placed by math coordinates."""
    if s.primitive == "axes":
        return True
    m = s.meta
    return ("x" in m and "y" in m) or ("cx" in m and "cy" in m)


def _math_bbox(
    shapes: list[VisualShape],
) -> tuple[float, float, float, float]:
    """Math-coord bounding box covering every geometric shape."""
    xs: list[float] = []
    ys: list[float] = []
    for s in shapes:
        m = s.meta
        if "x" in m and "y" in m:
            xs.append(float(m["x"]))
            ys.append(float(m["y"]))
        if "cx" in m and "cy" in m:
            cx, cy = float(m["cx"]), float(m["cy"])
            r = float(m.get("r", 0.0))
            xs.extend([cx - r, cx + r])
            ys.extend([cy - r, cy + r])
        # Explicit axes extents widen the bbox so the figure has breathing room.
        if "xmin" in m:
            xs.append(float(m["xmin"]))
        if "xmax" in m:
            xs.append(float(m["xmax"]))
        if "ymin" in m:
            ys.append(float(m["ymin"]))
        if "ymax" in m:
            ys.append(float(m["ymax"]))

    if not xs or not ys:
        return -1.5, 1.5, -1.5, 1.5

    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)

    # Add a small breathing margin so labels don't kiss the edge.
    margin_x = max((xmax - xmin) * 0.15, 0.1)
    margin_y = max((ymax - ymin) * 0.15, 0.1)
    xmin -= margin_x
    xmax += margin_x
    ymin -= margin_y
    ymax += margin_y

    # Guarantee non-degenerate range.
    if xmax - xmin < 0.01:
        xmin, xmax = xmin - 0.5, xmax + 0.5
    if ymax - ymin < 0.01:
        ymin, ymax = ymin - 0.5, ymax + 0.5

    return xmin, xmax, ymin, ymax


def _place_geom_shape(
    vs: VisualShape,
    to_canvas,
    scale: float,
    xmin: float, xmax: float, ymin: float, ymax: float,
) -> tuple[float, float, float, float]:
    """Place one geometric shape; mutates vs.width/height when geometry-driven.

    Returns (centre_x, centre_y, top_left_x, top_left_y) — the centre is
    used for connector endpoints, the top-left for PlacedShape.x/y.
    """
    m = vs.meta

    if vs.primitive == "circle" and "r" in m and "cx" in m and "cy" in m:
        r_canvas = float(m["r"]) * scale
        cx, cy = to_canvas(float(m["cx"]), float(m["cy"]))
        # Override default size with geometry-driven size.
        vs.width = vs.height = 2 * r_canvas
        return cx, cy, cx - r_canvas, cy - r_canvas

    if vs.primitive == "axes":
        # Axes always span the full figure bbox.
        x_left, _ = to_canvas(xmin, ymin)
        x_right, _ = to_canvas(xmax, ymin)
        _, y_top = to_canvas(xmin, ymax)
        _, y_bot = to_canvas(xmin, ymin)
        vs.width = max(1.0, x_right - x_left)
        vs.height = max(1.0, y_bot - y_top)
        # Pin the canvas-space origin so _render_axes draws the cross
        # through the math (0, 0) instead of the bounding-box corner.
        ox_canvas, oy_canvas = to_canvas(0.0, 0.0)
        vs.meta["origin_canvas_x"] = ox_canvas
        vs.meta["origin_canvas_y"] = oy_canvas
        vs.meta.setdefault("xmin", xmin)
        vs.meta.setdefault("xmax", xmax)
        vs.meta.setdefault("ymin", ymin)
        vs.meta.setdefault("ymax", ymax)
        return (
            x_left + vs.width / 2,
            y_top + vs.height / 2,
            x_left,
            y_top,
        )

    if vs.primitive == "point":
        # Fixed pixel size so points stay visible at any zoom.
        cx, cy = to_canvas(float(m["x"]), float(m["y"]))
        vs.width = vs.height = _POINT_PX
        return cx, cy, cx - _POINT_PX / 2, cy - _POINT_PX / 2

    # NOTE: caption placement is handled separately by
    # ``_place_captions_in_margins`` because captions must not cover the
    # geometric figure — they go to the canvas margins with a leader line
    # back to their math anchor point.

    # Generic: meta has x/y → centre at that math point, keep current size.
    if "x" in m and "y" in m:
        cx, cy = to_canvas(float(m["x"]), float(m["y"]))
        return cx, cy, cx - vs.width / 2, cy - vs.height / 2

    # Fallback (shouldn't reach here because _is_geometric filters first).
    return 0.0, 0.0, 0.0, 0.0


# ---------------------------------------------------------------------------
# Caption placement — margin strips with leader lines
# ---------------------------------------------------------------------------

def _place_captions_in_margins(
    captions: list[VisualShape],
    geom_bboxes: list[tuple[float, float, float, float]],
    canvas_w: float,
    canvas_h: float,
    pad: float,
    label_strip_h: float,
    to_canvas,
) -> list[tuple[VisualShape, float, float]]:
    """Place captions in canvas margins so they never cover the figure
    AND never overlap each other.

    Strategy
    --------
    Each caption has an ``anchor`` meta (``above`` / ``below`` /
    ``left`` / ``right`` / ``auto`` / ``overlay``).  The anchor selects
    a preferred margin; ``auto`` picks the margin with most free pixels.

    Placement uses a 2-D occupancy model: a list of every rectangle
    already on the canvas (figure shapes, point labels, prior captions).
    For each new caption we walk a row × column lattice in the chosen
    margin starting from the math-anchor's projection, taking the first
    spot that doesn't overlap any blocked rect.  When a row/column is
    full we wrap deeper into the margin (further from the figure).
    When no row fits, we try alternative margins.  Only as a last resort
    do we shrink the caption — and we keep searching after shrinking.
    Final fallback places at a canvas corner.

    This replaces the prior 1-D cursor model whose canvas-edge clamp
    silently piled later captions on top of earlier ones.
    """
    # Figure bounding box (canvas-px).
    if geom_bboxes:
        fig_xmin = min(b[0] for b in geom_bboxes)
        fig_xmax = max(b[0] + b[2] for b in geom_bboxes)
        fig_ymin = min(b[1] for b in geom_bboxes)
        fig_ymax = max(b[1] + b[3] for b in geom_bboxes)
    else:
        fig_xmin, fig_xmax = pad, canvas_w - pad
        fig_ymin, fig_ymax = pad + label_strip_h, canvas_h - pad

    # Pixel separation between figure and a caption stacked next to it.
    gap = 18.0
    # Pixel separation between two stacked caption rows / columns.
    intra_gap = 6.0

    auto_margin = _pick_auto_margin(
        canvas_w, canvas_h, fig_xmin, fig_xmax, fig_ymin, fig_ymax, label_strip_h, pad,
    )

    # Blocked rects: anything we must not overlap.  Seed with figure
    # geometry; append every caption as we place it.
    blocked: list[tuple[float, float, float, float]] = list(geom_bboxes)
    # Treat the entire figure rect as a single forbidden zone too —
    # belt-and-braces against captions slipping through gaps in the
    # geom_bboxes coverage.
    blocked.append((fig_xmin, fig_ymin, fig_xmax - fig_xmin, fig_ymax - fig_ymin))

    # The renderer (_render_caption in s5_render) grows the caption rect
    # to fit wrapped text: ``h = max(vs.height, needed_h)``.  If layout
    # uses the unmodified vs.height it reserves too small a slot and the
    # rendered rect spills over into the next caption.  Reconcile here:
    # update vs.height in-place to the actual rendered height before any
    # placement decisions are made.
    for vs in captions:
        vs.height = _caption_rendered_height(vs)

    out: list[tuple[VisualShape, float, float]] = []
    for vs in captions:
        m = vs.meta
        anchor = (m.get("anchor") or "auto").lower()
        if anchor == "auto":
            anchor = auto_margin

        # Math anchor → canvas-px (used as leader endpoint and as the
        # ideal in-margin position the search prefers).
        if "x" in m and "y" in m:
            ax_px, ay_px = to_canvas(float(m["x"]), float(m["y"]))
        else:
            ax_px = (fig_xmin + fig_xmax) / 2
            ay_px = (fig_ymin + fig_ymax) / 2

        if anchor == "overlay":
            # Legacy in-figure placement — explicit LLM opt-in.
            ps_x = max(pad, min(canvas_w - vs.width - pad, ax_px - vs.width / 2))
            ps_y = max(pad, min(canvas_h - vs.height - pad, ay_px - vs.height / 2))
            out.append((vs, ps_x, ps_y))
            blocked.append((ps_x, ps_y, vs.width, vs.height))
            continue

        # Search order: requested anchor first, then alternatives sorted
        # by free pixel area in each margin.
        margin_space = {
            "above": max(0.0, fig_ymin - (pad + label_strip_h)) * canvas_w,
            "below": max(0.0, (canvas_h - pad) - fig_ymax) * canvas_w,
            "left":  max(0.0, fig_xmin - pad) * canvas_h,
            "right": max(0.0, (canvas_w - pad) - fig_xmax) * canvas_h,
        }
        norm = lambda a: ("above" if a in ("above", "top") else
                          "below" if a in ("below", "bottom") else a)
        first = norm(anchor)
        rest = sorted(
            (a for a in ("above", "below", "left", "right") if a != first),
            key=lambda a: -margin_space[a],
        )
        search_order = [first] + rest

        placed = None
        for try_anchor in search_order:
            placed = _try_place_in_margin(
                vs, try_anchor, blocked,
                fig_xmin=fig_xmin, fig_xmax=fig_xmax,
                fig_ymin=fig_ymin, fig_ymax=fig_ymax,
                canvas_w=canvas_w, canvas_h=canvas_h,
                pad=pad, label_strip_h=label_strip_h,
                gap=gap, intra_gap=intra_gap,
                ax_px=ax_px, ay_px=ay_px,
            )
            if placed:
                break

        # Last resort: shrink the caption width and try again.  Re-render
        # then wraps to more lines — better than overlapping anything.
        if placed is None:
            original_w = vs.width
            vs.width = max(60.0, original_w * 0.5)
            for try_anchor in search_order:
                placed = _try_place_in_margin(
                    vs, try_anchor, blocked,
                    fig_xmin=fig_xmin, fig_xmax=fig_xmax,
                    fig_ymin=fig_ymin, fig_ymax=fig_ymax,
                    canvas_w=canvas_w, canvas_h=canvas_h,
                    pad=pad, label_strip_h=label_strip_h,
                    gap=gap, intra_gap=intra_gap,
                    ax_px=ax_px, ay_px=ay_px,
                )
                if placed:
                    break

        if placed is None:
            # Truly out of room — drop into a canvas corner; this WILL
            # overlap, but at a deterministic spot so the failure is
            # obvious in screenshots and the regression test will catch
            # it instead of a random pile in the middle of the figure.
            ps_x = max(pad, canvas_w - vs.width - pad)
            ps_y = max(pad, canvas_h - vs.height - pad)
            placed = (ps_x, ps_y, (ps_x, ps_y))

        ps_x, ps_y, leader_from = placed

        if not _point_inside((ax_px, ay_px), ps_x, ps_y, vs.width, vs.height):
            vs.meta["leader_from_canvas"] = leader_from
            vs.meta["leader_to_canvas"] = (ax_px, ay_px)

        blocked.append((ps_x, ps_y, vs.width, vs.height))
        out.append((vs, ps_x, ps_y))

    # Post-pass: leader-endpoint dot suppression (kept from prior fix).
    for i, (vs_a, _, _) in enumerate(out):
        endpoint = vs_a.meta.get("leader_to_canvas")
        if not (isinstance(endpoint, (tuple, list)) and len(endpoint) == 2):
            continue
        ex, ey = float(endpoint[0]), float(endpoint[1])
        for j, (vs_b, bx, by) in enumerate(out):
            if i == j:
                continue
            if _point_inside((ex, ey), bx, by, vs_b.width, vs_b.height):
                vs_a.meta["leader_endpoint_suppressed"] = True
                break

    return out


def _caption_rendered_height(vs: VisualShape) -> float:
    """Predict the actual rendered height of a caption.

    s5_render._render_caption sets ``h = max(vs.height, needed_h)`` where
    ``needed_h`` depends on how many lines the wrapped label takes.  This
    helper mirrors that calculation so layout can reserve the correct
    vertical slot up-front.  Without this, captions whose text wraps to
    multiple lines silently overflow into adjacent caption slots.
    """
    from .s5_render import _wrap_label
    pad = 6.0
    lines, eff_fs = _wrap_label(
        vs.label or "",
        node_width=max(40.0, vs.width - 2 * pad),
        font_size=(vs.font_size or 14.0) * 0.95,
    )
    line_h = eff_fs * 1.25
    needed_h = line_h * len(lines) + 2 * pad
    return max(vs.height, needed_h)


def _try_place_in_margin(
    vs: VisualShape,
    anchor: str,
    blocked: list[tuple[float, float, float, float]],
    *,
    fig_xmin: float, fig_xmax: float,
    fig_ymin: float, fig_ymax: float,
    canvas_w: float, canvas_h: float,
    pad: float, label_strip_h: float,
    gap: float, intra_gap: float,
    ax_px: float, ay_px: float,
) -> tuple[float, float, tuple[float, float]] | None:
    """Search for a non-overlapping (x, y) for ``vs`` in ``anchor`` margin.

    Returns ``(ps_x, ps_y, leader_from)`` on success, ``None`` if no row
    in this margin can hold the caption without overlapping ``blocked``.
    Walks rows (or columns for left/right) starting from the edge nearest
    the figure and stepping deeper.
    """
    w, h = vs.width, vs.height

    if anchor in ("above", "top"):
        # Rows go upward from fig_ymin.
        row = 0
        while True:
            ps_y = fig_ymin - gap - h - row * (h + intra_gap)
            if ps_y < pad + label_strip_h:
                return None
            x = _find_x_for_row(w, h, ps_y, blocked, canvas_w, pad, ax_px)
            if x is not None:
                return (x, ps_y, (x + w / 2, ps_y + h))
            row += 1

    if anchor in ("below", "bottom"):
        row = 0
        while True:
            ps_y = fig_ymax + gap + row * (h + intra_gap)
            if ps_y + h > canvas_h - pad:
                return None
            x = _find_x_for_row(w, h, ps_y, blocked, canvas_w, pad, ax_px)
            if x is not None:
                return (x, ps_y, (x + w / 2, ps_y))
            row += 1

    if anchor == "left":
        col = 0
        while True:
            ps_x = fig_xmin - gap - w - col * (w + intra_gap)
            if ps_x < pad:
                return None
            y = _find_y_for_col(w, h, ps_x, blocked, canvas_h, pad, label_strip_h, ay_px)
            if y is not None:
                return (ps_x, y, (ps_x + w, y + h / 2))
            col += 1

    if anchor == "right":
        col = 0
        while True:
            ps_x = fig_xmax + gap + col * (w + intra_gap)
            if ps_x + w > canvas_w - pad:
                return None
            y = _find_y_for_col(w, h, ps_x, blocked, canvas_h, pad, label_strip_h, ay_px)
            if y is not None:
                return (ps_x, y, (ps_x, y + h / 2))
            col += 1

    return None


def _find_x_for_row(
    w: float, h: float, y: float,
    blocked: list[tuple[float, float, float, float]],
    canvas_w: float, pad: float, ideal_anchor_x: float,
) -> float | None:
    """Find an x in [pad, canvas_w-w-pad] s.t. (x, y, w, h) doesn't overlap
    ``blocked``.  Prefer x centred on ``ideal_anchor_x``; if that collides,
    walk in increasing distance from the ideal."""
    x_min = pad
    x_max = canvas_w - w - pad
    if x_max < x_min:
        return None
    ideal_x = max(x_min, min(x_max, ideal_anchor_x - w / 2))
    step = 8.0
    candidates = [ideal_x]
    cx = x_min
    while cx <= x_max:
        candidates.append(cx)
        cx += step
    candidates.append(x_max)
    candidates.sort(key=lambda c: abs(c - ideal_x))
    for x in candidates:
        if not any(_rects_overlap((x, y, w, h), b) for b in blocked):
            return x
    return None


def _find_y_for_col(
    w: float, h: float, x: float,
    blocked: list[tuple[float, float, float, float]],
    canvas_h: float, pad: float, label_strip_h: float, ideal_anchor_y: float,
) -> float | None:
    """Find a y in [pad+label_strip_h, canvas_h-h-pad] s.t. (x, y, w, h)
    doesn't overlap ``blocked``.  Prefer y centred on ``ideal_anchor_y``."""
    y_min = pad + label_strip_h
    y_max = canvas_h - h - pad
    if y_max < y_min:
        return None
    ideal_y = max(y_min, min(y_max, ideal_anchor_y - h / 2))
    step = 8.0
    candidates = [ideal_y]
    cy = y_min
    while cy <= y_max:
        candidates.append(cy)
        cy += step
    candidates.append(y_max)
    candidates.sort(key=lambda c: abs(c - ideal_y))
    for y in candidates:
        if not any(_rects_overlap((x, y, w, h), b) for b in blocked):
            return y
    return None


def _pick_auto_margin(
    canvas_w: float, canvas_h: float,
    fig_xmin: float, fig_xmax: float, fig_ymin: float, fig_ymax: float,
    label_strip_h: float, pad: float,
) -> str:
    """Pick the margin with the most free pixels for captions to stack into."""
    space_above = max(0.0, fig_ymin - (pad + label_strip_h)) * canvas_w
    space_below = max(0.0, (canvas_h - pad) - fig_ymax) * canvas_w
    space_left  = max(0.0, fig_xmin - pad) * canvas_h
    space_right = max(0.0, (canvas_w - pad) - fig_xmax) * canvas_h
    options = [
        ("below", space_below),
        ("right", space_right),
        ("above", space_above),
        ("left",  space_left),
    ]
    options.sort(key=lambda kv: -kv[1])
    return options[0][0]


def _point_inside(p, x: float, y: float, w: float, h: float) -> bool:
    px, py = p
    return x <= px <= x + w and y <= py <= y + h


# ---------------------------------------------------------------------------
# C1 / C2 — point-label collision avoidance + label-aware figure bboxes
# ---------------------------------------------------------------------------

# Per-character width estimate for italic-serif label text at the point's
# font size.  Calibrated against the actual canvas font; off by ~10% for
# very narrow ("i", "l") or very wide ("M", "W") characters but accurate
# enough for collision detection.
_LABEL_W_PER_CHAR = 0.55

# Pixel padding around each label box used for collision detection.  A
# little slack so labels never quite touch.
_LABEL_PAD = 2.0


def _label_dims(vs: VisualShape) -> tuple[float, float]:
    """Estimate (width, height) of *vs*'s rendered point label, in canvas px."""
    label = (vs.label or "").strip()
    fs = float(vs.font_size or 14.0)
    w = max(8.0, len(label) * _LABEL_W_PER_CHAR * fs)
    h = fs + 2.0
    return w, h


def _label_rect_for_side(
    vs: VisualShape, cx: float, cy: float, side: str,
) -> tuple[float, float, float, float]:
    """Bounding rect (x, y, w, h) of *vs*'s label when placed on *side* of
    the dot at (cx, cy).  Mirrors the offsets in ``_render_point``."""
    lw, lh = _label_dims(vs)
    r = _POINT_PX * 0.25
    fs = float(vs.font_size or 14.0)
    if side == "above":
        return (cx - lw / 2, cy - r - 4 - lh, lw, lh)
    if side == "left":
        return (cx - r - 4 - lw, cy - lh / 2 + fs * 0.35, lw, lh)
    if side == "right":
        return (cx + r + 4, cy - lh / 2 + fs * 0.35, lw, lh)
    # default: below
    return (cx - lw / 2, cy + r, lw, lh)


def _rects_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + _LABEL_PAD <= bx
        or bx + bw + _LABEL_PAD <= ax
        or ay + ah + _LABEL_PAD <= by
        or by + bh + _LABEL_PAD <= ay
    )


def _assign_label_sides(
    point_records: list[tuple[VisualShape, float, float]],
) -> None:
    """Greedy collision-resolver: pick a label side for each point so its
    label rectangle does not overlap any previously-placed label.

    Side preference order (per point): below, above, right, left.  For
    points whose labels would still collide on every side, we accept
    the least-bad choice (sticks with "below") rather than throwing.
    """
    if len(point_records) < 2:
        if point_records:
            point_records[0][0].meta.setdefault("label_side", "below")
        return

    placed: list[tuple[float, float, float, float]] = []
    for vs, cx, cy in point_records:
        chosen_side = "below"
        chosen_rect: tuple[float, float, float, float] | None = None
        for side in ("below", "above", "right", "left"):
            rect = _label_rect_for_side(vs, cx, cy, side)
            if not any(_rects_overlap(rect, p) for p in placed):
                chosen_side = side
                chosen_rect = rect
                break
        if chosen_rect is None:
            chosen_rect = _label_rect_for_side(vs, cx, cy, "below")
        vs.meta["label_side"] = chosen_side
        placed.append(chosen_rect)


def _label_bbox(
    vs: VisualShape, cx: float, cy: float,
) -> tuple[float, float, float, float]:
    """Return the canvas-px bounding rect of *vs*'s label, using the side
    chosen by ``_assign_label_sides``.  Used to expand the figure bbox
    so caption placement avoids the label area."""
    side = (vs.meta.get("label_side") or "below").lower()
    return _label_rect_for_side(vs, cx, cy, side)
