"""Sevim MCP tool surface.

A *hybrid* surface: the host LLM can either describe a clause in natural
language and let Sevim's S2 extractor build the graph, or construct the
graph directly with structured ``add_node`` / ``add_edge`` calls.  Both
modes mutate the same long-lived canvas and can be freely intermixed.

Tool list
---------
  sevim_open           — create/get a canvas, return a live view URL
  sevim_describe       — NL pass-through (one clause → S1→S5 → merged)
  sevim_add_node       — structured: append a node directly
  sevim_add_edge       — structured: append a directed edge directly
  sevim_remove         — drop nodes/edges by id
  sevim_apply          — batched: many mutations atomically (low-latency)
  sevim_render         — return the current SVG explicitly
  sevim_vocabulary     — list supported relations, primitives, kinds, shapes
  sevim_list_canvases  — enumerate open canvases
  sevim_close          — discard a canvas

The host LLM is expected to embed the ``view_url`` returned by
``sevim_open`` in its reply so the user can keep the live diagram visible
while the conversation continues.
"""
from __future__ import annotations

import os
import threading
from typing import Any

import sys
import webbrowser

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from sevim.ir import MATH_PRIMITIVES, MATH_RELATIONS

from service.canvas import (
    REGISTRY,
    _KIND_ALIASES,
    _VALID_KINDS,
)
from service.canvas import Canvas


def _maybe_open_browser(view_url: str) -> bool:
    """Spawn the user's default browser onto ``view_url``.

    Python's stdlib ``webbrowser`` redirects the spawned process's
    stdout/stderr to DEVNULL on Linux, so this won't corrupt the MCP
    JSON-RPC stream we're carrying on stdout.  Set ``SEVIM_NO_BROWSER=1``
    to disable (useful for headless servers, CI, or tests).

    Returns True if a browser launch was attempted successfully.
    """
    if os.environ.get("SEVIM_NO_BROWSER"):
        return False
    try:
        return bool(webbrowser.open(view_url, new=2, autoraise=True))
    except Exception as e:  # noqa: BLE001
        # webbrowser.open can raise on exotic Linux setups (no DISPLAY,
        # no xdg-open, etc.).  Log but don't fail the tool call — the
        # user can still click the view_url manually.
        print(f"[sevim-mcp] webbrowser.open failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Module config — populated by mcp_server.__main__ before run().
# ---------------------------------------------------------------------------

class _Config:
    host: str = "127.0.0.1"
    port: int = 7777

    @classmethod
    def view_url(cls, canvas_id: str) -> str:
        return f"http://{cls.host}:{cls.port}/canvas/{canvas_id}/view"


# A "default" canvas is opened lazily on first use so the LLM can call
# sevim_describe without an explicit sevim_open round-trip.
_DEFAULT_CANVAS_ID = "default"


def _get_or_default(canvas_id: str | None) -> Canvas:
    """Resolve the target canvas for a tool call.

    Order of resolution when ``canvas_id`` is None:
      1. Most-recently-active *user-opened* canvas (excludes "default").
         This catches the "LLM forgot to thread canvas_id" case.  Without
         this, a tool call after ``sevim_open(math_mode=True)`` that
         omits canvas_id would silently land on a fresh "default"
         canvas with math_mode=False, and the LLM would only notice
         when it called ``sevim_review`` and got the wrong shape.
      2. Existing "default" canvas if one was previously created.
      3. Lazily create "default" so ``sevim_describe`` still works
         without an explicit ``sevim_open`` round-trip.

    When ``canvas_id`` is explicitly provided, behaviour is unchanged
    (get-or-create with that id).
    """
    if canvas_id is None:
        latest = REGISTRY.latest_active(exclude=_DEFAULT_CANVAS_ID)
        if latest is not None:
            return latest
        try:
            return REGISTRY.get(_DEFAULT_CANVAS_ID)
        except KeyError:
            return REGISTRY.open(canvas_id=_DEFAULT_CANVAS_ID)
    try:
        return REGISTRY.get(canvas_id)
    except KeyError:
        return REGISTRY.open(canvas_id=canvas_id)


# ---------------------------------------------------------------------------
# All 30 supported RelationType values — kept here statically so the
# vocabulary tool doesn't have to introspect the typing.Literal at runtime.
# ---------------------------------------------------------------------------

_BASE_RELATIONS = [
    "contains", "part_of", "causes", "sequence",
    "attribute_of", "similar_to", "opposes", "instance_of",
    "used_for", "requires", "reduces_to", "measures",
]
_ALL_RELATIONS = _BASE_RELATIONS + sorted(MATH_RELATIONS)


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "sevim",
    instructions=(
        "Sevim is a live diagram canvas.  When the user asks anything that "
        "benefits from a visual — math, geometry, set theory, system "
        "diagrams, causal chains, equations — open a canvas with sevim_open "
        "and either describe sentences (sevim_describe) or build the graph "
        "directly (sevim_add_node + sevim_add_edge).  sevim_open spawns "
        "the user's browser onto the canvas automatically (auto_open=True "
        "by default), so DO NOT tell the user to click the view_url — it "
        "is already opening for them.  You may still mention the URL once "
        "for reference, but a single short line like 'I've opened the "
        "canvas in your browser' is enough."
        "\n\n"
        "**The standard 3-call tutoring workflow.**  Audio plays in "
        "three phases — prelude, transition, walkthrough — and Sevim "
        "wires them together automatically:\n"
        "\n"
        "  1. ``sevim_open(math_mode=True, animate=True, prelude=\"…\")``\n"
        "     The PRELUDE is the full verbal problem-and-solution "
        "     definition (50–150 words).  State the problem, name the "
        "     technique, give the high-level idea — everything you'd say "
        "     before pointing at a diagram.  The viewer speaks this via "
        "     the browser's TTS the moment the canvas opens, *while you "
        "     are still writing* the next tool call.  The user hears a "
        "     complete verbal explanation before the figure finishes "
        "     rendering.\n"
        "\n"
        "  2. ``sevim_apply(canvas_id=…, ops=[…])`` — ALL nodes, edges, "
        "     AND captions in ONE batched call.  Captions go in the same "
        "     list:  {\"op\":\"add_caption\", \"text\":…, \"x\":…, \"y\":…, "
        "     \"anchor\":…}.  NEVER call sevim_add_caption in isolation "
        "     when you're adding several — fold them in.\n"
        "\n"
        "  3. ``sevim_narrate(canvas_id=…, script=[…])`` — phrase-timed "
        "     walk-through with highlights.  Each phrase highlights one "
        "     canvas element while it's spoken.  The viewer automatically "
        "     plays a transition phrase ('And now please look at the "
        "     diagram.') between the prelude and your script — you don't "
        "     have to write the transition yourself.  Start the script "
        "     directly with the first observation about the figure "
        "     (e.g. {\"speak\":\"This top arc is variable x_1 set to "
        "     TRUE.\", \"highlight\":\"n_t1\"}).\n"
        "\n"
        "If you find yourself writing add_node, add_edge, or add_caption "
        "in isolation more than twice in a row, stop and rewrite as one "
        "sevim_apply.  Each isolated call costs 3–10 s of YOUR thinking "
        "time, not Sevim's, and the user feels every second."
        "\n\n"
        "**Carry canvas_id.**  When you call any mutation tool after "
        "sevim_open returns a canvas_id, pass that exact id back on "
        "every subsequent call.  Without it, Sevim now routes to your "
        "most recent canvas, but it is much safer to be explicit."
        "\n\n"
        "Closed-loop visual feedback: every mutation tool returns a "
        "`warnings` list when the algorithmic critic spots problems "
        "(point off its declared circle, duplicate coordinates, math_mode "
        "without coords, etc.) — read it and self-correct on the next "
        "call.  For complex figures, also call sevim_review BEFORE "
        "concluding your reply: it returns the canvas as a PNG so you "
        "(if vision-capable) can see what the user is about to see and "
        "fix layout problems the algorithmic critic can't catch."
        "\n\n"
        "In-canvas narration: when the explanation benefits from a "
        "step-by-step reveal (math proofs, geometric constructions, "
        "any 'first… then… finally…' walk-through), open the canvas "
        "with `animate=True` and interleave `sevim_add_caption` calls "
        "between figure-building calls.  Each shape/edge/caption fades "
        "in 0.4s after the previous one via SMIL, so the canvas plays "
        "itself like a slow whiteboard reveal — the user opens the "
        "view_url once and watches the explanation unfold."
        "\n\n"
        "Voice narration: for the richest explainer experience, build "
        "the figure FIRST, then call `sevim_narrate` with a script of "
        "short phrases that each reference one canvas element.  The "
        "viewer plays the audio and highlights the matching element "
        "exactly while its phrase is being spoken — phrase-accurate "
        "by virtue of per-phrase TTS synthesis, no estimation."
    ),
)


@mcp.tool()
def sevim_open(
    canvas_id: str | None = None,
    math_mode: bool = False,
    animate: bool = False,
    auto_open: bool = True,
    width: int = 700,
    height: int = 440,
    prelude: str | None = None,
    transition: str | None = None,
    intro: str | None = None,
) -> dict[str, Any]:
    """Open a new (or reuse an existing) Sevim diagram canvas.

    Returns a ``view_url`` the user can open in a browser to watch the
    diagram build in real time.  Always show this URL to the user in your
    first reply on a new canvas.

    **Choose math_mode carefully — it controls the layout algorithm:**

    - ``math_mode=False`` (default, concept-diagram mode): nodes are placed
      by Sugiyama layered layout based on edge topology.  Best for causal
      chains, system diagrams, hierarchies, "X causes Y", "A requires B".

    - ``math_mode=True`` (geometric figure mode): nodes are placed at the
      Cartesian coordinates given in their ``meta`` dict (``x``/``y`` for
      points, ``cx``/``cy``/``r`` for circles).  Edges become straight
      lines between actual coordinates.  Use this whenever you'd draw on
      a coordinate plane: geometry proofs, unit-circle illustrations,
      function plots, vector diagrams, set-theory Venn diagrams with
      positioned regions.

    **animate=True** turns the canvas into a self-playing whiteboard:
    every shape and edge fades in 0.4s after the previous one, so the
    figure builds itself incrementally as the user watches.  Combine
    with ``sevim_add_caption`` to interleave explanatory text — the
    captions fade in on their own beat too, mimicking a Khan-Academy /
    3blue1brown style explainer.  No JavaScript required; the SVG plays
    itself via SMIL animations.

    Args:
        canvas_id: Optional stable id.  If omitted, a fresh id is generated.
            Re-using an id returns the existing canvas unchanged.
        math_mode: See above.  Open with ``math_mode=True`` for any figure
            where the *positions* of objects matter; pass real coordinates
            via ``sevim_add_node(meta_extras=...)``.
        animate: Stagger fade-in of new shapes/edges/captions for a
            "whiteboard reveal" effect.  Recommended when explaining
            something step-by-step on the canvas itself.
        auto_open: Spawn the user's default browser onto the view_url
            (default True; once per canvas).  The user does not need to
            click the URL — the page opens automatically.  You should
            still mention the URL in your reply for reference, but you
            can skip "click here to open it".  Set False if you suspect
            the user is on a headless setup.
        width: Canvas width in SVG user units.
        height: Canvas height in SVG user units.
        prelude: The FULL problem + solution definition, spoken via the
            browser's built-in TTS the moment the canvas opens.  Should
            be 50–150 words: state the problem, name the technique, give
            the high-level idea.  The viewer speaks this *while you write
            the figure-build calls*, so the user hears a complete verbal
            explanation before the figure even finishes rendering.  After
            the prelude, the viewer plays a transition phrase ("And now
            please look at the diagram.") and hands off to ``sevim_narrate``
            for the phrase-aligned walkthrough.  Web Speech autoplays
            without a click on most browsers — far more reliable than
            the gated <audio> element.
        transition: Optional override for the transition phrase spoken
            between the prelude and the narration.  Default is "And now
            please look at the diagram."  Keep short (one sentence).
        intro: DEPRECATED alias for ``prelude``.  If both are set,
            ``prelude`` wins.
    """
    c = REGISTRY.open(
        canvas_id=canvas_id,
        math_mode=math_mode,
        width=width,
        height=height,
        animate=animate,
    )
    view_url = _Config.view_url(c.canvas_id)

    spoken = (prelude or intro or "").strip()
    transition_text = (transition or "").strip()

    # Stash the prelude + transition on the canvas so the viewer's
    # Web Speech path can speak them instantly on load.  Piper still
    # synthesises a backup WAV in the background — viewer falls back
    # to it if Web Speech is unavailable.
    if spoken or transition_text:
        with c.lock:
            if spoken:
                c.intro_text = spoken
            if transition_text:
                c.transition_text = transition_text
            c.revision += 1
            c.updated_at = _time_now()
        if spoken:
            threading.Thread(
                target=_synth_intro_in_background,
                args=(c, spoken),
                name=f"sevim-intro-{c.canvas_id}",
                daemon=True,
            ).start()

    # Auto-launch the browser the first time we see this canvas, so the
    # user doesn't have to click the URL.  Subsequent ``sevim_open``
    # calls against the same canvas_id are no-ops (the tab is already
    # open or the user closed it on purpose).
    browser_opened = False
    with c.lock:
        if auto_open and not c.browser_opened:
            browser_opened = _maybe_open_browser(view_url)
            c.browser_opened = True

    return {
        "canvas_id": c.canvas_id,
        "view_url": view_url,
        "math_mode": c.math_mode,
        "animate": c.animate,
        "browser_opened": browser_opened,
        "width": c.width,
        "height": c.height,
        "revision": c.revision,
        "prelude_speaking": bool(spoken),
    }


def _time_now() -> float:
    import time
    return time.time()


def _synth_intro_in_background(canvas, text: str) -> None:
    """Run piper synthesis on a daemon thread; failures are non-fatal."""
    try:
        canvas.intro(text)
    except Exception as exc:  # noqa: BLE001 — viewer Web Speech keeps working
        print(f"[sevim-intro-bg] piper synthesis failed: {exc}")


@mcp.tool()
def sevim_describe(text: str, canvas_id: str | None = None) -> dict[str, Any]:
    """Hand a natural-language clause to Sevim's S1→S5 pipeline and merge
    the extracted nodes/edges into the canvas.

    Best for "translate this sentence into a diagram fragment".  For
    fine-grained control over node kinds and exact relations, prefer
    sevim_add_node + sevim_add_edge instead.

    Args:
        text: A single clause (e.g. "Decision trees partition the feature
            space recursively.").  Multiple clauses work but extraction
            quality is best one clause at a time.
        canvas_id: Target canvas.  Omit to use the default canvas.
    """
    c = _get_or_default(canvas_id)
    result = c.describe(text)
    result["view_url"] = _Config.view_url(c.canvas_id)
    return result


@mcp.tool()
def sevim_add_node(
    label: str,
    kind: str | None = None,
    canvas_id: str | None = None,
    meta_extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append ONE node.  Prefer ``sevim_apply`` with batched ops when adding
    more than one node in this turn — every individual call costs you
    ~3-10 s of JSON-args generation time, and the user feels every second.

    USE THIS TOOL ONLY WHEN: you are adding a single afterthought node
    after the bulk figure is already built.  Otherwise put the node into
    the next ``sevim_apply`` call as ``{"op":"add_node", "label":..., ...}``.

    Args:
        label: Display label.  IDs are derived from this (slugified) so
            re-using a label updates the existing node instead of creating
            a duplicate.
        kind: Visual primitive.  Aliases: "concept" (rect, default for
            general nouns), "attribute" (ellipse), "parameter" (diamond),
            "architecture" (hexagon), "layer" (parallelogram).  Math
            primitives accepted directly: "point", "segment", "circle",
            "polygon", "axes", "set_blob", "matrix_bracket",
            "equation_block", "decoration_mark", "bracket_brace",
            "tensor_box", "string_wire", "pullback_corner", "axes_3d",
            "pdf_curve", "plate", "error_bar", "proof_node".  Omit to let
            Sevim guess from the label.
        canvas_id: Target canvas.  Omit to use the default canvas.
        meta_extras: Free-form metadata that drives sizing/rendering.
            **In a math_mode canvas, the x/y/cx/cy/r values are the
            object's ACTUAL coordinates on the figure** — Sevim places
            shapes at those positions and draws edges as straight lines
            between them.  Pick a coordinate system (e.g. unit circle ⇒
            cx=0, cy=0, r=1; points on the circle at (cosθ, sinθ)) and
            be consistent across all nodes you add.

            Examples by kind:
              point:          {"x": 1.0, "y": 0.0}      (math_mode: actual position)
              circle:         {"cx": 0.0, "cy": 0.0, "r": 1.0}  (math_mode: real geometric circle)
              axes:           {"xmin": -1.5, "xmax": 1.5, "ymin": -1.5, "ymax": 1.5}
              equation_block: {"latex": r"\\cos(90^\\circ) = 0", "unicode": "cos(90°)=0"}
              matrix_bracket: {"nrows": 2, "ncols": 3,
                               "cells": [["a","b","c"],["d","e","f"]],
                               "delim": "bracket"}
              set_blob:       {"members": ["a","b"], "kind": "venn"}

            For straight lines between two points (segments / chords /
            radii / coordinate drops), DO NOT add a `segment`-kind node;
            instead add an edge with relation "connects" between the two
            point nodes.  Sevim will draw the actual line in math_mode.
    """
    c = _get_or_default(canvas_id)
    result = c.add_node(label=label, kind=kind, meta_extras=meta_extras)
    result["canvas_id"] = c.canvas_id
    result["view_url"] = _Config.view_url(c.canvas_id)
    return result


@mcp.tool()
def sevim_add_edge(
    src_id: str,
    dst_id: str,
    relation: str,
    canvas_id: str | None = None,
) -> dict[str, Any]:
    """Append ONE edge.  Prefer ``sevim_apply`` with batched ops when adding
    multiple edges — each isolated call costs ~3-10 s of model time.

    USE THIS TOOL ONLY WHEN: you are adding a single afterthought edge
    after the bulk figure is already built.  Otherwise put it in the
    next ``sevim_apply`` call as ``{"op":"add_edge", "src_id":..., "dst_id":..., "relation":...}``.

    Args:
        src_id: Source node id (from sevim_add_node or sevim_describe).
        dst_id: Destination node id.
        relation: One of the 30 closed relations.  Concept-diagram set:
            "causes", "part_of", "contains", "sequence", "attribute_of",
            "similar_to", "opposes", "instance_of", "used_for", "requires",
            "reduces_to", "measures".  Math set: "lies_on", "between",
            "perpendicular", "parallel", "tangent_to", "equals",
            "congruent", "approximately_equal", "isomorphic_to",
            "element_of", "subset_of", "disjoint", "maps_to", "labels",
            "points_to", "connects", "grouped_with", "aligned_with",
            "implies", "adjoint_to", "natural_transformation", "commutes",
            "transforms".  Call sevim_vocabulary for the full list.
        canvas_id: Target canvas.  Omit to use the default canvas.
    """
    c = _get_or_default(canvas_id)
    result = c.add_edge(src_id=src_id, dst_id=dst_id, relation=relation)
    result["canvas_id"] = c.canvas_id
    result["view_url"] = _Config.view_url(c.canvas_id)
    return result


@mcp.tool()
def sevim_add_caption(
    text: str,
    x: float | None = None,
    y: float | None = None,
    anchor: str = "auto",
    canvas_id: str | None = None,
) -> dict[str, Any]:
    """Add ONE caption.  STRONGLY prefer ``sevim_apply`` with batched ops
    when you have several captions to add — each isolated call costs ~3-10 s
    of model time you spend writing the JSON, and captions almost always
    come in groups.

    USE THIS TOOL ONLY WHEN: you are adding a single afterthought caption
    after the figure and other captions are built.  In every other case,
    put it in the next ``sevim_apply`` as
    ``{"op":"add_caption", "text":..., "x":..., "y":..., "anchor":...}``.

    An in-canvas caption is explanatory text that sits in the canvas
    margin with a leader line back to the figure piece it explains.

    **Captions never overlay the figure.**  In math_mode the caption is
    pushed to the appropriate canvas margin (top / bottom / left / right
    of the figure's bounding box) and a thin dashed leader line connects
    the caption box to the math anchor (x, y).  Multiple captions in the
    same margin stack without overlapping.

    Args:
        text: The caption text.  Keep it short — one sentence, ~6-18
            words is ideal.  Captions wrap to multiple lines but a wall
            of text on the canvas reads poorly; put longer commentary
            in your chat reply.
        x, y: Math-coordinate anchor for the leader line.  The caption
            box itself goes into a margin, but the leader points at
            (x, y) so the user knows what the caption is about.
        anchor: Which margin the caption goes into:
              "auto"   (default) — pick the margin with the most room
              "above"  — top margin, leader from caption's bottom edge
              "below"  — bottom margin, leader from top edge
              "left"   — left margin, leader from right edge
              "right"  — right margin, leader from left edge
              "overlay" — legacy in-figure placement; will overlap shapes,
                          use only when you really need the caption
                          inside the figure (rare).
        canvas_id: Target canvas.  Omit to use the default canvas.
    """
    c = _get_or_default(canvas_id)
    meta_extras: dict[str, Any] = {"anchor": anchor}
    if x is not None and y is not None:
        meta_extras["x"] = float(x)
        meta_extras["y"] = float(y)
    result = c.add_node(label=text, kind="caption", meta_extras=meta_extras)
    result["canvas_id"] = c.canvas_id
    result["view_url"] = _Config.view_url(c.canvas_id)
    return result


@mcp.tool()
def sevim_remove(ids: list[str], canvas_id: str | None = None) -> dict[str, Any]:
    """Drop nodes and/or edges by id.  Removing a node also drops all of
    its incident edges.

    Args:
        ids: List of node-ids ("n_…") or edge-ids ("e_…") to remove.
        canvas_id: Target canvas.  Omit to use the default canvas.
    """
    c = _get_or_default(canvas_id)
    result = c.remove(ids)
    result["canvas_id"] = c.canvas_id
    result["view_url"] = _Config.view_url(c.canvas_id)
    return result


@mcp.tool()
def sevim_apply(ops: list[dict[str, Any]], canvas_id: str | None = None) -> dict[str, Any]:
    """Apply many mutations in one tool call (lower latency than many
    separate calls).  Useful when describing a multi-step proof or a
    diagram with several related pieces.

    Each op is one of:
      {"op": "add_node",    "label": "...", "kind": "...", "meta_extras": {...}}
      {"op": "add_edge",    "src_id": "...", "dst_id": "...", "relation": "..."}
      {"op": "add_caption", "text": "...", "x": ..., "y": ..., "anchor": "..."}
      {"op": "describe",    "text": "..."}
      {"op": "remove",      "ids": ["..."]}
      {"op": "clear"}

    Args:
        ops: Ordered list of operations.  All applied under the canvas's
            lock; a single re-render runs at the end.
        canvas_id: Target canvas.  Omit to use the default canvas.
    """
    c = _get_or_default(canvas_id)
    results: list[dict[str, Any]] = []
    for i, op in enumerate(ops):
        kind = op.get("op")
        try:
            if kind == "add_node":
                results.append(c.add_node(
                    label=op["label"],
                    kind=op.get("kind"),
                    meta_extras=op.get("meta_extras"),
                ))
            elif kind == "add_edge":
                results.append(c.add_edge(
                    src_id=op["src_id"],
                    dst_id=op["dst_id"],
                    relation=op["relation"],
                ))
            elif kind == "add_caption":
                # Captions are nodes with kind="caption"; the layout
                # pass routes them to a margin via the anchor meta.
                meta = {
                    k: op[k] for k in ("x", "y", "anchor")
                    if k in op
                }
                if "meta_extras" in op:
                    meta.update(op["meta_extras"])
                results.append(c.add_node(
                    label=op["text"],
                    kind="caption",
                    meta_extras=meta,
                ))
            elif kind == "describe":
                results.append(c.describe(op["text"]))
            elif kind == "remove":
                results.append(c.remove(op["ids"]))
            elif kind == "clear":
                results.append(c.clear())
            else:
                results.append({"error": f"unknown op {kind!r}", "index": i})
        except Exception as e:
            results.append({"error": str(e), "index": i, "op": kind})
    snap = c.snapshot()
    return {
        "canvas_id": c.canvas_id,
        "view_url": _Config.view_url(c.canvas_id),
        "results": results,
        "revision": snap["revision"],
        "node_count": snap["node_count"],
        "edge_count": snap["edge_count"],
    }


@mcp.tool()
def sevim_intro(
    text: str,
    canvas_id: str | None = None,
) -> dict[str, Any]:
    """Synthesise a short intro audio that plays immediately on canvas open.

    Use right after sevim_open to fill the dead air while you are
    still writing the figure-build calls.  The browser plays the
    intro the moment it arrives; when ``sevim_narrate`` is later
    called with the full phrase-timed script, the viewer transitions
    from intro to main narration without a click.

    Recommended length: one or two sentences (8-25 seconds when
    spoken).  Long enough to cover your build latency, short enough
    that the user is not stuck listening to filler.

    Args:
        text: One or two sentences introducing the figure you are about
            to draw.  Plain prose; no highlight targets.  Example:
            "Let's reduce 3SAT to Hamiltonian path.  I'll build the
            variable diamond gadget first, then connect the clause
            nodes."
        canvas_id: Target canvas.  Omit to use the most-recently-opened
            canvas.

    Returns:
        Summary with the synthesised intro's duration in seconds.
    """
    c = _get_or_default(canvas_id)
    try:
        result = c.intro(text)
    except FileNotFoundError as e:
        return {
            "canvas_id": c.canvas_id,
            "view_url": _Config.view_url(c.canvas_id),
            "error": str(e),
            "hint": (
                "Download a piper voice model (default expected path: "
                "~/.local/share/sevim/voices/en_US-lessac-medium.onnx + "
                ".onnx.json) or set SEVIM_VOICE_MODEL to point at one."
            ),
        }
    result["view_url"] = _Config.view_url(c.canvas_id)
    return result


@mcp.tool()
def sevim_narrate(
    script: list[dict[str, Any]],
    canvas_id: str | None = None,
) -> dict[str, Any]:
    """Generate voice narration with a phrase-timed highlight schedule.

    The viewer plays the audio and highlights the matching canvas
    element for the duration of each phrase.  Highlight timing comes
    from each phrase's actual TTS-output WAV duration, so it is
    naturally word-accurate at phrase granularity — never sped up or
    slowed down to fit the visual.

    Browsers block autoplay until the user has interacted with the
    page; the viewer surfaces a one-click "▶ Play narration" overlay
    on first arrival.  After the user clicks once, subsequent
    narrations on the same canvas play automatically.

    Args:
        script: Ordered list of phrase dicts.  Each phrase:

            {"speak": "Let's start with the unit circle",
             "highlight": "n_unit_circle"}

          - ``speak``: the text to say (one short phrase, ideally
            one referent).  Keep phrases short — 6 to 12 words is
            ideal — so each highlight has a tight time window.
          - ``highlight`` (optional): canvas element id (`n_…` for
            nodes, `e_…` for edges) to light up while this phrase
            is being spoken.  Omit for "narration only, no
            highlight" phrases (intros, transitions).

          The highlight stays active from this phrase's start to its
          end; transitions to the next highlight happen at the
          phrase boundary.

        canvas_id: Target canvas.  Omit to use the default canvas.

    Returns:
        Summary including total duration and any highlight ids that
        don't exist on the canvas (so you can correct them).

    Tips for good narrations:
      - Build the figure FIRST with sevim_add_node / sevim_add_edge,
        THEN call sevim_narrate once with the full script — the
        highlight ids must already exist when narration runs.
      - One phrase = one referent.  "Pick a point P on the circle"
        is one phrase highlighting `n_p`; don't bundle two referents
        in a single phrase.
      - Pause-style transitions: include a phrase like
        ``{"speak": "Now,"}`` (no highlight) to give the listener a
        beat between sections.
    """
    c = _get_or_default(canvas_id)
    try:
        result = c.narrate(script)
    except FileNotFoundError as e:
        return {
            "canvas_id": c.canvas_id,
            "view_url": _Config.view_url(c.canvas_id),
            "error": str(e),
            "hint": (
                "Download a piper voice model (default expected path: "
                "~/.local/share/sevim/voices/en_US-lessac-medium.onnx + "
                ".onnx.json) or set SEVIM_VOICE_MODEL to point at one."
            ),
        }
    result["view_url"] = _Config.view_url(c.canvas_id)
    return result


@mcp.tool()
def sevim_review(canvas_id: str | None = None, png_width: int = 1200) -> list:
    """Render the canvas as a PNG and return it for visual review.

    **This is the closed-loop feedback step.**  After building a complex
    figure, call sevim_review *before* concluding your reply to the user.
    The result includes:

      1. A short text summary (counts, revision, all algorithmic warnings).
      2. A PNG image of the current canvas — if you have vision capability,
         look at it and check whether the figure matches what you intended.
         If it doesn't, emit corrections via further sevim_* calls before
         the user sees the result.

    Algorithmic warnings (already attached to every mutation tool's
    result) catch coordinate-level mistakes (a point off its declared
    circle, duplicate coords, missing radius, math_mode without coords).
    Vision review catches the rest: lines pointing the wrong way, labels
    crashing into each other, the figure being unreadably small, etc.

    Args:
        canvas_id: Target canvas.  Omit to use the default canvas.
        png_width: PNG output width in pixels (height auto-scales).
            Larger = clearer for the LLM but more tokens.  Default 1200
            is a good balance.
    """
    c = _get_or_default(canvas_id)
    with c.lock:
        snap = {
            "canvas_id": c.canvas_id,
            "view_url": _Config.view_url(c.canvas_id),
            "revision": c.revision,
            "node_count": len(c.graph.nodes),
            "edge_count": len(c.graph.edges),
            "math_mode": c.math_mode,
            "warnings": list(c.warnings),
            "render_error": c.last_error,
        }
        svg = c.svg

    summary = _format_review_summary(snap)

    try:
        png_bytes = _svg_to_png(svg, width=png_width)
        return [summary, Image(data=png_bytes, format="png")]
    except Exception as e:
        # Fall back to SVG-as-text so the LLM still has *something* to look
        # at even when the PNG conversion fails (e.g. cairo not installed).
        return [
            f"{summary}\n\n(PNG conversion failed: {type(e).__name__}: {e}; "
            f"raw SVG follows so you can still inspect the structure.)\n\n{svg}"
        ]


def _format_review_summary(snap: dict[str, Any]) -> str:
    lines: list[str] = [
        f"Canvas {snap['canvas_id']!r} — revision {snap['revision']}, "
        f"{snap['node_count']} nodes, {snap['edge_count']} edges, "
        f"math_mode={snap['math_mode']}.",
        f"Live viewer: {snap['view_url']}",
    ]
    if snap.get("render_error"):
        lines.append(f"\nRENDER ERROR: {snap['render_error']}")
    warnings = snap.get("warnings") or []
    if warnings:
        lines.append(f"\n{len(warnings)} algorithmic warning(s):")
        for w in warnings[:20]:
            lines.append(f"  - [{w.get('code','?')}] {w.get('message','')}")
        if len(warnings) > 20:
            lines.append(f"  ... and {len(warnings)-20} more.")
    else:
        lines.append("\nNo algorithmic warnings.")
    return "\n".join(lines)


def _svg_to_png(svg: str, width: int = 1200) -> bytes:
    """Rasterise SVG to PNG via cairosvg.  Bytes are inlined into the
    MCP tool result so the host LLM (with vision) can see the figure."""
    import cairosvg
    return cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=width,
    )


@mcp.tool()
def sevim_render(
    canvas_id: str | None = None,
    include_svg: bool = True,
) -> dict[str, Any]:
    """Return the current SVG and counts.

    Args:
        canvas_id: Target canvas.  Omit to use the default canvas.
        include_svg: If False, return only counts and revision (cheaper).
    """
    c = _get_or_default(canvas_id)
    with c.lock:
        out: dict[str, Any] = {
            "canvas_id": c.canvas_id,
            "view_url": _Config.view_url(c.canvas_id),
            "revision": c.revision,
            "node_count": len(c.graph.nodes),
            "edge_count": len(c.graph.edges),
        }
        if include_svg:
            out["svg"] = c.svg
        return out


@mcp.tool()
def sevim_vocabulary() -> dict[str, Any]:
    """List Sevim's closed vocabularies — relations, primitive shapes, and
    the kind aliases accepted by sevim_add_node.

    Call this once per session if you're unsure what relation to use; the
    returned lists are exhaustive (Sevim refuses anything outside them).
    """
    return {
        "relations": {
            "concept": _BASE_RELATIONS,
            "math":    sorted(MATH_RELATIONS),
            "all":     _ALL_RELATIONS,
        },
        "primitives": {
            "concept": ["rect", "ellipse", "diamond", "hexagon", "parallelogram"],
            "math":    sorted(MATH_PRIMITIVES),
        },
        "kind_aliases": dict(_KIND_ALIASES),
        "valid_kinds": sorted(_VALID_KINDS),
    }


@mcp.tool()
def sevim_list_canvases() -> dict[str, Any]:
    """Enumerate all currently open canvases on this server."""
    return {"canvases": REGISTRY.list()}


@mcp.tool()
def sevim_close(canvas_id: str) -> dict[str, Any]:
    """Discard a canvas and free its memory.  After this the view_url 404s."""
    closed = REGISTRY.close(canvas_id)
    return {"canvas_id": canvas_id, "closed": closed}


# ---------------------------------------------------------------------------
# Server bootstrap (called by mcp_server.__main__).
# ---------------------------------------------------------------------------

def configure(host: str, port: int) -> None:
    _Config.host = host
    _Config.port = port


def run_stdio() -> None:
    """Block on the MCP stdio transport."""
    mcp.run()


def get_default_port() -> int:
    return int(os.environ.get("SEVIM_HTTP_PORT", "7777"))


def get_default_host() -> str:
    return os.environ.get("SEVIM_HTTP_HOST", "127.0.0.1")
