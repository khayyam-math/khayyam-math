"""Deterministic layout planner for express SVG figures.

Replaces the existing greedy ``reflow_overlapping_text`` pass with a
globally-optimal placement computed via OR-tools CP-SAT.

The problem is **Point-Feature Label Placement** (Christensen-Marks-
Shieber 1995): given anchors (the LLM-chosen x/y of each text) and
candidate positions around each anchor, choose one candidate per
label that (a) keeps every label inside the viewBox, (b) minimises
pairwise overlap, and (c) minimises displacement from the anchor.

Why CP-SAT and not DP / force-directed / greedy?

* DP requires optimal substructure with a natural sweep order; 2D
  unrestricted layout has a dense pairwise constraint graph and no
  such order — DP states explode to exponential.
* Force-directed (Fruchterman-Reingold) reaches a local minimum, is
  non-deterministic without seeded init, and can't represent
  "anchored to point P within radius r" cleanly.
* Greedy (the existing pass) cannot recover from early bad choices.
* For our scale (m ≤ ~30 labels, k = 16 candidates per label), CP-SAT
  with a random_seed solves to provable optimality in <1s — the right
  trade-off of rigour, speed, and determinism.

The planner is a single entry point: ``plan_layout(svg) -> svg``.
Idempotent on a clean figure; no-op when ortools is unavailable so
the caller can fail open.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

try:
    from ortools.sat.python import cp_model  # type: ignore
    _ORTOOLS_AVAILABLE = True
except ImportError:
    _ORTOOLS_AVAILABLE = False


# ── Parsing helpers ────────────────────────────────────────────────

_ATTR_RE = re.compile(r'\b([A-Za-z_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')


def _attrs(tag: str) -> dict[str, str]:
    """Parse an SVG tag's attributes, tolerating single or double quotes."""
    return {
        m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
        for m in _ATTR_RE.finditer(tag)
    }


def _viewbox(svg: str) -> Optional[Tuple[float, float, float, float]]:
    """Extract the SVG's viewBox as (x, y, w, h).  Returns None if absent."""
    m = re.search(
        r'<svg\b[^>]*?\bviewBox\s*=\s*(?:"([^"]*)"|\'([^\']*)\')',
        svg, re.S,
    )
    if not m:
        return None
    raw = m.group(1) if m.group(1) is not None else m.group(2)
    try:
        parts = raw.replace(",", " ").split()
        return (float(parts[0]), float(parts[1]),
                float(parts[2]), float(parts[3]))
    except (ValueError, IndexError):
        return None


def _g_ranges(svg: str) -> List[Tuple[int, int]]:
    """Character ranges occupied by top-level <g>...</g> blocks.

    Used to skip text inside groups — those carry their own layout
    (matrix cells, etc.) and are handled by autofit_group_rects.
    """
    ranges: List[Tuple[int, int]] = []
    depth = 0
    start = -1
    for m in re.finditer(r'<g\b[^>]*>|</g>', svg, re.S):
        if m.group(0).startswith("</"):
            depth -= 1
            if depth == 0 and start >= 0:
                ranges.append((start, m.end()))
                start = -1
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return ranges


# ── Stage 1: extract items + estimate bboxes ───────────────────────


@dataclass
class TextItem:
    """A top-level <text> element the planner is free to relocate."""

    # Character offsets in the source SVG of the opening <text ...> tag.
    tag_start: int
    tag_end: int

    # Text content (between <text> and </text>).
    content: str

    # Original LLM-chosen anchor position.
    anchor_x: float
    anchor_y: float

    # Estimated rendered bbox at the original anchor (w, h in viewBox
    # units).  Width uses 0.6 × font-size × char-count, height uses
    # 1.2 × font-size.  Conservative — real text often renders
    # narrower but tighter bboxes risk under-spacing.
    width: float
    height: float

    # text-anchor: "start" | "middle" | "end" — controls how the bbox
    # is positioned relative to (anchor_x, anchor_y).
    text_anchor: str

    # font-size for re-emission (we don't change it).
    font_size: float

    # Original raw tag string so we can rewrite x/y attributes
    # without losing other attributes (font-family, fill, id, ...).
    raw_tag: str

    # id attribute, if present.  Used by plan_layout to skip texts
    # that narration highlights — moving them away from their
    # geometric anchor breaks the visual link between the highlight
    # rect (which snaps to the id'd element's getBBox) and the
    # primitive the narration is describing.
    elem_id: str = ""


def _est_char_width(font_size: float) -> float:
    """Conservative per-character width estimate (0.6 em)."""
    return 0.6 * font_size


def _bbox_at(anchor_x: float, anchor_y: float, width: float, height: float,
             text_anchor: str) -> Tuple[float, float, float, float]:
    """Return (x_min, y_min, x_max, y_max) for a text bbox at an anchor.

    SVG <text> y is the BASELINE — the glyph extends roughly
    0.8 × font-size above and 0.2 × font-size below the baseline.
    We approximate as a height-tall box centred on a baseline 0.8h
    above its bottom edge.
    """
    if text_anchor == "middle":
        x_min = anchor_x - width / 2
    elif text_anchor == "end":
        x_min = anchor_x - width
    else:  # "start" or default
        x_min = anchor_x
    x_max = x_min + width
    # baseline at anchor_y; ascender = 0.8h above, descender = 0.2h below
    y_min = anchor_y - 0.8 * height
    y_max = anchor_y + 0.2 * height
    return (x_min, y_min, x_max, y_max)


def extract_text_items(svg: str) -> List[TextItem]:
    """Parse top-level <text> elements with estimated bboxes.

    Skips <text> elements inside <g>...</g> groups (handled by other
    passes) and <tspan> children (which inherit their parent's
    coordinates and don't need separate placement).
    """
    items: List[TextItem] = []
    skip = _g_ranges(svg)

    def _in_group(pos: int) -> bool:
        return any(s <= pos < e for s, e in skip)

    # Match `<text ...>content</text>` non-greedily.  Tolerates nested
    # <tspan> by capturing everything up to the FIRST </text>.
    text_re = re.compile(r'<text\b([^>]*)>(.*?)</text>', re.S)

    for m in text_re.finditer(svg):
        if _in_group(m.start()):
            continue
        attrs = _attrs(m.group(0))
        try:
            ax = float(attrs.get("x", "0"))
            ay = float(attrs.get("y", "0"))
        except ValueError:
            continue
        font_size_raw = attrs.get("font-size", "16").rstrip("pxptem")
        try:
            fs = float(font_size_raw)
        except ValueError:
            fs = 16.0
        text_anchor = attrs.get("text-anchor", "start").lower()
        # Strip <tspan>...</tspan> and any other inner tags to count
        # visible characters only.
        visible = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not visible:
            continue
        char_w = _est_char_width(fs)
        width = max(char_w * len(visible), 1.0)
        height = fs * 1.2
        items.append(TextItem(
            tag_start=m.start(),
            tag_end=m.end(),
            content=visible,
            anchor_x=ax,
            anchor_y=ay,
            width=width,
            height=height,
            text_anchor=text_anchor,
            font_size=fs,
            raw_tag=m.group(0),
            elem_id=attrs.get("id", ""),
        ))
    return items


# ── Stage 2: candidate-position generation ─────────────────────────


@dataclass
class Candidate:
    """A possible (x, y) for an item plus its computed bbox + cost."""

    item_idx: int
    x: float
    y: float
    bbox: Tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax)
    # Cost relative to the anchor: 0 for the anchor itself, growing
    # with displacement.  Used as the objective's preference term.
    displacement_cost: int


# Candidate grid: 8 cardinal directions × 3 radial offsets, plus the
# anchor itself.  Offsets are in viewBox units; tuned for typical
# 900×650 figures with 16-32px text.
#
# Narration-anchored labels (those whose id appears in a highlight
# phrase) are *pinned* by plan_layout's protected_ids parameter so
# the canvas viewer's highlight rect stays over the right primitive.
# Everything else can move up to 56 px from its anchor — enough to
# clear typical wide-label overlaps but small enough that the moved
# label remains visually associated with its anchor.
_DIRS: List[Tuple[float, float]] = [
    (0.0, 0.0),   # anchor itself
    (1.0, 0.0),   # E
    (-1.0, 0.0),  # W
    (0.0, -1.0),  # N
    (0.0, 1.0),   # S
    (0.7, -0.7),  # NE
    (-0.7, -0.7), # NW
    (0.7, 0.7),   # SE
    (-0.7, 0.7),  # SW
]
_OFFSETS = (12.0, 28.0, 56.0)  # px in viewBox space


def gen_candidates(items: List[TextItem],
                   viewbox: Tuple[float, float, float, float]) -> List[Candidate]:
    """Generate one Candidate list per item — anchor + up to 24 nearby.

    Filters out candidates whose bbox would clip the viewBox so the
    MIP doesn't waste variables on infeasible positions.
    """
    vbx, vby, vbw, vbh = viewbox
    margin = 4.0  # keep labels off the very edge for legibility
    out: List[Candidate] = []
    for i, item in enumerate(items):
        # The anchor (no displacement) is always candidate 0 if it fits.
        seen: set[Tuple[int, int]] = set()
        for dx_unit, dy_unit in _DIRS:
            for off in (_OFFSETS if (dx_unit, dy_unit) != (0.0, 0.0) else (0.0,)):
                cx = item.anchor_x + dx_unit * off
                cy = item.anchor_y + dy_unit * off
                # Dedup near-coincident candidates (e.g. anchor + zero offset).
                key = (int(cx), int(cy))
                if key in seen:
                    continue
                seen.add(key)
                bbox = _bbox_at(cx, cy, item.width, item.height, item.text_anchor)
                # Reject if the bbox clips the viewBox.
                if (bbox[0] < vbx + margin or bbox[2] > vbx + vbw - margin
                        or bbox[1] < vby + margin or bbox[3] > vby + vbh - margin):
                    continue
                # Cost is the Euclidean displacement in units of 4px.
                dispx = cx - item.anchor_x
                dispy = cy - item.anchor_y
                cost = int(((dispx ** 2 + dispy ** 2) ** 0.5) / 4.0)
                out.append(Candidate(
                    item_idx=i, x=cx, y=cy, bbox=bbox,
                    displacement_cost=cost,
                ))
    return out


# ── Stage 3-4: build + solve CP-SAT MIP ────────────────────────────


def _bboxes_overlap(a: Tuple[float, float, float, float],
                    b: Tuple[float, float, float, float],
                    pad: float = 2.0) -> bool:
    """Standard 2D bbox overlap with a small mandatory gap."""
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0]
                or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def solve_layout(items: List[TextItem],
                 candidates: List[Candidate],
                 *,
                 time_limit_s: float = 2.0,
                 overlap_weight: int = 1000,
                 random_seed: int = 42) -> Optional[List[int]]:
    """Assign one candidate per item via CP-SAT.

    Returns a list ``picked`` of length ``len(items)`` where
    ``picked[i]`` is the candidate's index in ``candidates``.
    Returns None if infeasible or ortools unavailable.

    Determinism: random_seed is forwarded to CP-SAT and the
    iteration order over candidates is the order they were
    generated, so the same input always produces the same output.
    """
    if not _ORTOOLS_AVAILABLE:
        return None
    if not items:
        return []

    # Group candidates by item.
    by_item: List[List[int]] = [[] for _ in items]
    for ci, c in enumerate(candidates):
        by_item[c.item_idx].append(ci)
    # Every item must have at least one feasible candidate.  If a
    # label's anchor was so close to a viewBox edge that EVERY
    # candidate was clipped, fall back: re-add the anchor with no
    # displacement so the layout doesn't lose the label.
    for i, ids in enumerate(by_item):
        if not ids:
            it = items[i]
            bbox = _bbox_at(it.anchor_x, it.anchor_y, it.width, it.height,
                            it.text_anchor)
            candidates.append(Candidate(
                item_idx=i, x=it.anchor_x, y=it.anchor_y,
                bbox=bbox, displacement_cost=0,
            ))
            by_item[i] = [len(candidates) - 1]

    model = cp_model.CpModel()

    # One bool per candidate; exactly one true per item.
    x = [model.NewBoolVar(f"x_{ci}") for ci in range(len(candidates))]
    for ids in by_item:
        model.Add(sum(x[ci] for ci in ids) == 1)

    # No-overlap clauses: for every pair of candidates (across DIFFERENT
    # items) whose bboxes intersect, forbid both being chosen.
    # Single-item candidates are mutually exclusive via the AddExactlyOne
    # constraint above so no need to enumerate those pairs.
    n = len(candidates)
    for i in range(n):
        ci = candidates[i]
        for j in range(i + 1, n):
            cj = candidates[j]
            if ci.item_idx == cj.item_idx:
                continue
            if _bboxes_overlap(ci.bbox, cj.bbox):
                # At most one of these two candidates can be chosen —
                # they overlap geometrically, so picking both is invalid.
                model.Add(x[i] + x[j] <= 1)

    # Objective: minimise total displacement.  Overlap is hard-
    # forbidden by the constraint above; if NO feasible assignment
    # exists, the solver returns INFEASIBLE and we fall through to
    # the caller (which leaves the SVG unchanged).
    model.Minimize(
        sum(c.displacement_cost * x[ci] for ci, c in enumerate(candidates))
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.random_seed = random_seed
    # Determinism: single thread + fixed search → reproducible runs.
    solver.parameters.num_workers = 1

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    picked: List[int] = [-1] * len(items)
    for ci, var in enumerate(x):
        if solver.Value(var) == 1:
            picked[candidates[ci].item_idx] = ci
    if any(p < 0 for p in picked):
        return None
    return picked


# ── Stage 5: re-emit SVG with chosen positions ─────────────────────


def apply_positions(svg: str, items: List[TextItem],
                    candidates: List[Candidate],
                    picked: List[int]) -> str:
    """Rewrite the SVG with the planner's chosen x/y for each item.

    Edits happen in REVERSE document order so earlier tag_start /
    tag_end offsets remain valid as we mutate the string.
    """
    pairs = sorted(
        ((items[i], candidates[picked[i]]) for i in range(len(items))),
        key=lambda p: p[0].tag_start,
        reverse=True,
    )
    out = svg
    for item, cand in pairs:
        new_tag = re.sub(
            r"\bx\s*=\s*(?:\"[^\"]*\"|'[^']*')",
            f'x="{cand.x:.2f}"',
            item.raw_tag, count=1,
        )
        new_tag = re.sub(
            r"\by\s*=\s*(?:\"[^\"]*\"|'[^']*')",
            f'y="{cand.y:.2f}"',
            new_tag, count=1,
        )
        out = out[:item.tag_start] + new_tag + out[item.tag_end:]
    return out


# ── Stage 6: post-layout validator ─────────────────────────────────


def count_overlaps(items: List[TextItem], candidates: List[Candidate],
                   picked: List[int]) -> int:
    """Count pairwise text-bbox overlaps in the chosen layout.

    Should be 0 after a successful MIP solve; non-zero means the
    solver returned a feasible but non-optimal answer (e.g. timed
    out) or the input had impossible constraints.
    """
    chosen = [candidates[picked[i]].bbox for i in range(len(items))]
    overlaps = 0
    for i in range(len(chosen)):
        for j in range(i + 1, len(chosen)):
            if _bboxes_overlap(chosen[i], chosen[j]):
                overlaps += 1
    return overlaps


# ── Public entry point ─────────────────────────────────────────────


def plan_layout(svg: str, *, time_limit_s: float = 2.0,
                protected_ids: Optional[set] = None) -> str:
    """Optimise top-level <text> positions in the SVG.

    ``protected_ids`` (set of strings): elements whose ``id`` appears
    in this set are kept at their original (x, y).  Used to pin
    narration-highlighted labels: the canvas viewer's highlight rect
    snaps to an element's getBBox(), so moving a labelled element
    away from its associated primitive would render the highlight
    "far from the thing it describes."

    Fails open: any error or solver infeasibility returns the
    original SVG unchanged so a layout bug never blocks a figure.
    """
    if not _ORTOOLS_AVAILABLE:
        return svg
    vb = _viewbox(svg)
    if vb is None:
        return svg
    items = extract_text_items(svg)
    if len(items) < 2:
        # Nothing to optimise.
        return svg
    # Protected items keep their original position — generate ONLY
    # the anchor candidate for them.  Other items get the full set.
    protected_ids = protected_ids or set()
    movable: List[TextItem] = []
    pinned: List[TextItem] = []
    for it in items:
        if it.elem_id and it.elem_id in protected_ids:
            pinned.append(it)
        else:
            movable.append(it)
    # Run the candidate generator on the FULL list so candidate
    # indices align with item indices; but for pinned items only
    # the anchor candidate is generated.
    candidates: List[Candidate] = []
    for i, item in enumerate(items):
        if item in pinned:
            bbox = _bbox_at(item.anchor_x, item.anchor_y, item.width,
                            item.height, item.text_anchor)
            candidates.append(Candidate(
                item_idx=i, x=item.anchor_x, y=item.anchor_y,
                bbox=bbox, displacement_cost=0,
            ))
        else:
            for c in gen_candidates([item], vb):
                # gen_candidates assigned item_idx=0 (single-item call);
                # rewrite to the global index.
                c.item_idx = i
                candidates.append(c)
    if not candidates:
        return svg
    picked = solve_layout(items, candidates, time_limit_s=time_limit_s)
    if picked is None:
        return svg
    return apply_positions(svg, items, candidates, picked)
