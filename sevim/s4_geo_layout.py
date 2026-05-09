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
    # If we only count the dot positions, the area where the labels live
    # looks empty to the caption placer and a caption lands on top of
    # the labels.  Extending each point's bbox to include its label
    # rectangle pushes captions to truly empty margins.
    for vs, cx_canvas, cy_canvas in point_records:
        geom_bboxes.append(_label_bbox(vs, cx_canvas, cy_canvas))

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
    """Place captions in canvas margins so they never cover the figure.

    Strategy
    --------
    Each caption has an ``anchor`` meta (one of ``above`` / ``below`` /
    ``left`` / ``right`` / ``auto`` / ``overlay``).  The anchor selects
    which canvas margin the caption goes into.  ``auto`` picks the
    margin with the most free room.  ``overlay`` keeps the legacy
    in-figure placement for the rare case the LLM really wants it.

    Within a margin, captions stack without overlap (horizontally for
    top/bottom; vertically for left/right).  A leader line is recorded
    in ``meta["leader_from_canvas"]`` / ``meta["leader_to_canvas"]`` so
    ``_render_caption`` can draw a thin line back to the math anchor.
    """
    # Figure bounding box (canvas-px) — captions are pushed outside this.
    if geom_bboxes:
        fig_xmin = min(b[0] for b in geom_bboxes)
        fig_xmax = max(b[0] + b[2] for b in geom_bboxes)
        fig_ymin = min(b[1] for b in geom_bboxes)
        fig_ymax = max(b[1] + b[3] for b in geom_bboxes)
    else:
        fig_xmin, fig_xmax = pad, canvas_w - pad
        fig_ymin, fig_ymax = pad + label_strip_h, canvas_h - pad

    # Stacking cursors per margin; updated as each caption is placed.
    cursors = {
        "top":    pad,                       # x advancing rightward in top strip
        "bottom": pad,                       # x advancing rightward in bottom strip
        "left":   pad + label_strip_h,       # y advancing downward in left strip
        "right":  pad + label_strip_h,       # y advancing downward in right strip
    }
    gap = 8.0

    # Decide auto-margin once for the whole batch — captions on the same
    # figure should cluster on the same side rather than ping-ponging.
    auto_margin = _pick_auto_margin(
        canvas_w, canvas_h, fig_xmin, fig_xmax, fig_ymin, fig_ymax, label_strip_h, pad,
    )

    out: list[tuple[VisualShape, float, float]] = []
    for vs in captions:
        m = vs.meta
        anchor = (m.get("anchor") or "auto").lower()
        if anchor == "auto":
            anchor = auto_margin

        # Math anchor → canvas-px (used as leader endpoint).
        if "x" in m and "y" in m:
            ax, ay = to_canvas(float(m["x"]), float(m["y"]))
        else:
            ax, ay = (fig_xmin + fig_xmax) / 2, (fig_ymin + fig_ymax) / 2

        if anchor == "overlay":
            # Legacy in-figure placement — the LLM has explicitly asked
            # for this and accepts that overlap may occur.
            ps_x = ax - vs.width / 2
            ps_y = ay - vs.height / 2
            ps_x = max(pad, min(canvas_w - vs.width - pad, ps_x))
            ps_y = max(pad, min(canvas_h - vs.height - pad, ps_y))
            out.append((vs, ps_x, ps_y))
            continue

        if anchor == "above" or anchor == "top":
            ps_y = max(pad, fig_ymin - vs.height - gap)
            ideal_x = ax - vs.width / 2
            ps_x = max(cursors["top"], ideal_x)
            ps_x = max(pad, min(canvas_w - vs.width - pad, ps_x))
            cursors["top"] = ps_x + vs.width + gap
            leader_from = (ps_x + vs.width / 2, ps_y + vs.height)
        elif anchor == "below" or anchor == "bottom":
            ps_y = min(canvas_h - vs.height - pad, fig_ymax + gap)
            ideal_x = ax - vs.width / 2
            ps_x = max(cursors["bottom"], ideal_x)
            ps_x = max(pad, min(canvas_w - vs.width - pad, ps_x))
            cursors["bottom"] = ps_x + vs.width + gap
            leader_from = (ps_x + vs.width / 2, ps_y)
        elif anchor == "right":
            ps_x = min(canvas_w - vs.width - pad, fig_xmax + gap)
            ideal_y = ay - vs.height / 2
            ps_y = max(cursors["right"], ideal_y)
            ps_y = max(pad, min(canvas_h - vs.height - pad, ps_y))
            cursors["right"] = ps_y + vs.height + gap
            leader_from = (ps_x, ps_y + vs.height / 2)
        elif anchor == "left":
            ps_x = max(pad, fig_xmin - vs.width - gap)
            ideal_y = ay - vs.height / 2
            ps_y = max(cursors["left"], ideal_y)
            ps_y = max(pad, min(canvas_h - vs.height - pad, ps_y))
            cursors["left"] = ps_y + vs.height + gap
            leader_from = (ps_x + vs.width, ps_y + vs.height / 2)
        else:
            # Unknown anchor — fall back to the auto pick.
            ps_y = min(canvas_h - vs.height - pad, fig_ymax + gap)
            ps_x = max(pad, min(canvas_w - vs.width - pad, ax - vs.width / 2))
            leader_from = (ps_x + vs.width / 2, ps_y)

        # Stash leader endpoints so _render_caption can draw the tether.
        # Skip the leader when the math anchor is essentially inside the
        # caption box (e.g. the LLM didn't pass coords) — drawing a
        # zero-length line just looks like a stray dot.
        if not _point_inside((ax, ay), ps_x, ps_y, vs.width, vs.height):
            vs.meta["leader_from_canvas"] = leader_from
            vs.meta["leader_to_canvas"] = (ax, ay)

        out.append((vs, ps_x, ps_y))

    return out


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
