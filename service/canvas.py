"""Canvas state for the MCP plugin layer.

A *canvas* is a long-lived SceneGraph that the host LLM (Claude / ChatGPT /
Gemini) builds up incrementally across many tool calls. It supports two
construction modes that can be freely intermixed:

  - **NL pass-through** (`describe`): the LLM hands a sentence to Sevim's
    S1→S5 pipeline; nodes/edges extracted from the text are merged into the
    canvas's existing graph.

  - **Structured construction** (`add_node`, `add_edge`, `remove`): the LLM
    builds the IR directly using Sevim's closed primitive/relation
    vocabulary, bypassing the S2 extractor.

After every mutation the graph is re-rendered (S3→S5) and `revision` is
bumped so SSE viewers can pick up the change.

Threading model
---------------
Mutations are guarded by a `threading.Lock`.  uvicorn (running the live
viewer) reads ``revision`` and ``svg`` under the same lock from its own
thread.  No asyncio cross-loop coordination is needed because all shared
state is plain Python objects.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sevim.ir import (
    PlacedGraph, SceneEdge, SceneGraph, SceneNode, SpanRef, TraceEvent,
)
from sevim.pipeline import run_pipeline
from sevim.s3_map import map_visual
from sevim.s4_layout import layout
from sevim.s4_geo_layout import geometric_layout
from sevim.s5_render import render as render_svg
from sevim.geo_check import check as geo_check


# ---------------------------------------------------------------------------
# ID helpers (mirroring sevim.s2_extract conventions so structured-mode IDs
# collide cleanly with NL-mode IDs when the labels match).
# ---------------------------------------------------------------------------

def slug_nid(label: str) -> str:
    """`"Perpendicular Bisector"` → `"n_perpendicular_bisector"`."""
    return "n_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def slug_eid(frm: str, to: str, rel: str) -> str:
    return f"e_{rel}_{frm}_{to}"


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------

# Default semantic node_type assigned to structurally-added nodes.  S3's
# primitive selector keys off `meta["kind"]`, so node_type rarely matters
# for visual output once a kind is set.
_DEFAULT_NODE_TYPE = "entity"

# Subset of `Primitive` (sevim.ir) accepted as `kind=` in add_node.  Mapping
# to S3's `_label_to_primitive` is direct: meta["kind"] short-circuits.
_KIND_ALIASES: dict[str, str] = {
    # high-level concept-diagram aliases
    "concept":      "rect",
    "attribute":    "ellipse",
    "parameter":    "diamond",
    "architecture": "hexagon",
    "layer":        "parallelogram",
    # math aliases pass through unchanged when valid (validated below)
}

_VALID_KINDS: frozenset[str] = frozenset({
    # base primitives
    "rect", "ellipse", "diamond", "hexagon", "parallelogram",
    # math primitives
    "point", "segment", "curve", "polygon", "circle", "arc",
    "axes", "grid", "set_blob", "matrix_bracket", "equation_block",
    "decoration_mark", "bracket_brace",
    # university math primitives
    "tensor_box", "string_wire", "pullback_corner", "axes_3d",
    "pdf_curve", "plate", "error_bar", "proof_node",
    # narration
    "caption",
})


@dataclass
class Canvas:
    """One long-lived diagram session shared between the MCP tools and the
    live HTTP viewer."""
    canvas_id: str
    math_mode: bool = False
    width: int = 700
    height: int = 440
    animate: bool = False
    step_duration: float = 0.4   # seconds between successive appearances
    fade_duration: float = 0.35  # fade-in length per element
    graph: SceneGraph = field(default_factory=SceneGraph)
    svg: str = ""
    placed: PlacedGraph | None = None
    trace: list[TraceEvent] = field(default_factory=list)
    revision: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    warnings: list[dict] = field(default_factory=list)
    # Per-element appearance metadata used by ``_apply_fade_in``.  Each
    # entry is ``(batch_started_at_wall_clock, step_within_batch)``.  The
    # batch timestamp lets us emit *negative* SMIL begin times for elements
    # added long ago, so they render fully-opaque on every re-render
    # instead of restarting their fade-in.  Without this, every mutation
    # would visibly re-cascade every previously-added element from
    # opacity=0, producing a "blink, blink, blink…" rebuild on each
    # tool call.  Step within batch preserves the staggered reveal among
    # elements added together via ``sevim_apply``.
    appear_meta: dict[str, tuple[float, int]] = field(default_factory=dict)
    # True once we've spawned the user's browser onto this canvas's
    # view_url.  Prevents repeat-launches when the LLM re-calls sevim_open
    # against an existing canvas mid-session.
    browser_opened: bool = False
    # Voice narration: WAV path on disk + JSON-friendly phrase timing
    # manifest.  Both are populated by ``narrate(...)`` and served by the
    # FastAPI ``/canvas/<id>/narration.wav`` and ``/narration.json``
    # endpoints.  ``None`` while no narration has been generated.
    narration_wav: str | None = None
    narration_manifest: dict | None = None
    # Intro narration: a single short audio that the viewer plays
    # *immediately* on canvas open, while the LLM is still busy
    # generating the figure-build calls.  Covers the silent dead-air
    # the user otherwise watches.  When the figure is ready, the main
    # ``narration_wav`` takes over (viewer chains intro -> narration).
    intro_wav: str | None = None
    intro_duration_s: float = 0.0
    intro_text: str | None = None
    # Phrase the viewer speaks via Web Speech BETWEEN the prelude
    # (intro_text) and the figure-aligned narration WAV — so the
    # listener gets a clear cue to look at the canvas.  Override per
    # canvas; viewer falls back to a default English phrase.
    transition_text: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ---------- internal: re-run S3→S5 and bump revision ----------
    last_error: str | None = None

    def _rerender_locked(self) -> None:
        if not self.graph.nodes:
            self.svg = _empty_svg(self.width, self.height)
            self.placed = None
            self.last_error = None
        else:
            try:
                visual = map_visual(self.graph)
                # math_mode triggers the coordinate-honouring layout pass
                # (s4_geo_layout) instead of Sugiyama (s4_layout).  Use it
                # whenever the LLM is building geometric figures with
                # explicit coordinates in meta — those coordinates are
                # ignored by Sugiyama but respected here.
                if self.math_mode:
                    placed = geometric_layout(visual, self.width, self.height)
                else:
                    placed = layout(visual)
                self.placed = placed
                svg = render_svg(placed)
                if self.animate and self.appear_meta:
                    svg = _apply_fade_in(
                        svg,
                        self.appear_meta,
                        step_duration=self.step_duration,
                        fade_duration=self.fade_duration,
                    )
                self.svg = svg
                self.last_error = None
            except Exception as e:
                # Layout/render can fail on edge-case graphs (e.g. cyclic
                # math relations).  Keep the canvas alive with the last
                # good SVG and surface the error so the LLM can react.
                self.last_error = f"{type(e).__name__}: {e}"
        # L1 algorithmic critic — closed-loop visual feedback without
        # requiring the host LLM to have vision.  Warnings are surfaced
        # in every mutation tool's result so the LLM can self-correct.
        try:
            self.warnings = geo_check(self.graph, self.math_mode)
        except Exception:
            self.warnings = []
        self.revision += 1
        self.updated_at = time.time()

    # ---------- mutation: NL pass-through ----------
    def describe(self, text: str, utterance_id: str | None = None) -> dict[str, Any]:
        """Run S1→S5 on ``text`` and merge into this canvas's graph."""
        if not text.strip():
            raise ValueError("text must be non-empty")
        with self.lock:
            n_before, e_before = len(self.graph.nodes), len(self.graph.edges)
            uid = utterance_id or f"u{self.revision}"
            try:
                result = run_pipeline(text, utterance_id=uid, graph=self.graph)
                self.graph = result.graph
                self.placed = result.placed
                self.svg = result.svg
                self.trace = result.trace
                self.last_error = None
            except Exception as e:
                # Keep the prior good state; report the failure.
                self.last_error = f"{type(e).__name__}: {e}"
            self.revision += 1
            self.updated_at = time.time()
            added_nodes = [n.id for n in self.graph.nodes[n_before:]]
            added_edges = [e.id for e in self.graph.edges[e_before:]]
            if self.animate:
                batch_t = time.time()
                step = 0
                for nid in added_nodes:
                    if nid not in self.appear_meta:
                        self.appear_meta[nid] = (batch_t, step)
                        step += 1
                for eid in added_edges:
                    if eid not in self.appear_meta:
                        self.appear_meta[eid] = (batch_t, step)
                        step += 1
            return self._with_error({
                "canvas_id": self.canvas_id,
                "revision": self.revision,
                "added_nodes": added_nodes,
                "added_edges": added_edges,
                "node_count": len(self.graph.nodes),
                "edge_count": len(self.graph.edges),
            })

    # ---------- mutation: structured ----------
    def add_node(
        self,
        label: str,
        kind: str | None = None,
        meta_extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append (or update) a node directly, bypassing S2 extraction."""
        if not label.strip():
            raise ValueError("label must be non-empty")
        primitive = _resolve_kind(kind) if kind else None
        nid = slug_nid(label)
        meta: dict[str, Any] = dict(meta_extras or {})
        if primitive:
            meta.setdefault("kind", primitive)
        # Captions need the label text in meta for the S3 sizing pass —
        # `_math_size_override` doesn't see the SceneNode.label directly.
        if primitive == "caption":
            meta.setdefault("text", label)

        with self.lock:
            for n in self.graph.nodes:
                if n.id == nid:
                    n.label = label
                    if meta:
                        n.meta.update(meta)
                    self._rerender_locked()
                    return self._with_error({"node_id": nid, "created": False, "revision": self.revision})
            self.graph.nodes.append(SceneNode(
                id=nid,
                label=label,
                node_type=_DEFAULT_NODE_TYPE,
                embedding=(),
                salience=0.5,
                src_spans=[SpanRef(start=0, end=len(label), utterance_id="mcp")],
                meta=meta,
            ))
            if self.animate and nid not in self.appear_meta:
                self.appear_meta[nid] = (time.time(), 0)
            self._rerender_locked()
            return self._with_error({"node_id": nid, "created": True, "revision": self.revision})

    def add_edge(
        self,
        src_id: str,
        dst_id: str,
        relation: str,
    ) -> dict[str, Any]:
        """Append a directed edge between two existing nodes."""
        with self.lock:
            ids = {n.id for n in self.graph.nodes}
            if src_id not in ids:
                raise ValueError(f"src_id {src_id!r} not in canvas")
            if dst_id not in ids:
                raise ValueError(f"dst_id {dst_id!r} not in canvas")
            eid = slug_eid(src_id, dst_id, relation)
            for e in self.graph.edges:
                if e.id == eid:
                    return self._with_error({"edge_id": eid, "created": False, "revision": self.revision})
            self.graph.edges.append(SceneEdge(
                id=eid,
                from_id=src_id,
                to_id=dst_id,
                relation=relation,  # validated at SVG render time by the literal type
                src_spans=[SpanRef(start=0, end=0, utterance_id="mcp")],
            ))
            if self.animate and eid not in self.appear_meta:
                self.appear_meta[eid] = (time.time(), 0)
            self._rerender_locked()
            return self._with_error({"edge_id": eid, "created": True, "revision": self.revision})

    def remove(self, ids: Iterable[str]) -> dict[str, Any]:
        """Drop nodes and/or edges by id.  Removing a node also drops its
        incident edges."""
        ids_set = set(ids)
        if not ids_set:
            return {"removed": [], "revision": self.revision}
        with self.lock:
            kept_nodes = [n for n in self.graph.nodes if n.id not in ids_set]
            removed_nids = [n.id for n in self.graph.nodes if n.id in ids_set]
            kept_edges = [
                e for e in self.graph.edges
                if e.id not in ids_set
                and e.from_id not in removed_nids
                and e.to_id not in removed_nids
            ]
            removed = (
                [n.id for n in self.graph.nodes if n.id in ids_set]
                + [e.id for e in self.graph.edges if e.id in ids_set
                   or e.from_id in removed_nids or e.to_id in removed_nids]
            )
            self.graph.nodes = kept_nodes
            self.graph.edges = kept_edges
            for rid in removed:
                self.appear_meta.pop(rid, None)
            self._rerender_locked()
            return self._with_error({"removed": removed, "revision": self.revision})

    def clear(self) -> dict[str, Any]:
        with self.lock:
            self.graph = SceneGraph()
            self.appear_meta.clear()
            self._rerender_locked()
            return {"revision": self.revision}

    # ---------- narration ----------
    def narrate(self, script: list[dict]) -> dict[str, Any]:
        """Generate voice narration with a phrase-timed highlight schedule.

        Each entry in ``script`` is ``{"speak": str, "highlight": Optional[str]}``.
        The audio + manifest are stored on this canvas; the FastAPI service
        serves them at ``/canvas/<id>/narration.wav`` and ``.json``.

        If ``self.transition_text`` is set, it is **prepended as the first
        phrase** of the synthesized WAV (no highlight target).  This way
        the same piper voice speaks the transition between the prelude
        and the figure walkthrough — no voice mismatch, no client-side
        chaining, no separate audio element to manage.

        Validates that every ``highlight`` target actually exists in the
        graph — surfaces a ``warnings`` entry rather than failing silently
        if the LLM points at a missing id.
        """
        from sevim.narrate import synthesize_script
        # Validate highlight targets up front so the LLM gets a clean error.
        existing_ids = {n.id for n in self.graph.nodes} | {
            e.id for e in self.graph.edges
        }
        unknown: list[str] = []
        for entry in script:
            tgt = entry.get("highlight")
            if tgt and tgt not in existing_ids:
                unknown.append(tgt)
        # Prepend the transition phrase (same voice = same speaker).
        full_script: list[dict] = []
        if self.transition_text:
            full_script.append({
                "speak": self.transition_text,
                "highlight": None,
            })
        full_script.extend(script)
        narr_dir = _narration_dir(self.canvas_id)
        narr_dir.mkdir(parents=True, exist_ok=True)
        wav_path = narr_dir / "narration.wav"
        manifest = synthesize_script(full_script, str(wav_path))
        with self.lock:
            self.narration_wav = str(wav_path)
            self.narration_manifest = manifest
            self.revision += 1
            self.updated_at = time.time()
        return {
            "canvas_id": self.canvas_id,
            "duration_s": manifest["duration_s"],
            "phrase_count": len(manifest["phrases"]),
            "wav_path": str(wav_path),
            "unknown_highlight_targets": unknown,
        }

    # ---------- intro narration ----------
    def intro(self, text: str) -> dict[str, Any]:
        """Synthesise a short intro audio that the viewer plays immediately.

        Designed to fill the dead time while the host LLM is still
        writing build calls.  The browser plays the intro on canvas
        open; when ``narrate(...)`` is later called, the viewer chains
        from intro to main narration.  Calling ``intro`` again replaces
        the previous intro audio.
        """
        from sevim.narrate import synthesize_script
        text = (text or "").strip()
        if not text:
            raise ValueError("intro text must be non-empty")
        narr_dir = _narration_dir(self.canvas_id)
        narr_dir.mkdir(parents=True, exist_ok=True)
        wav_path = narr_dir / "intro.wav"
        # synthesize_script wants a list of phrase dicts.  One phrase,
        # no highlight target — pure speech.
        manifest = synthesize_script(
            [{"speak": text, "highlight": None}],
            str(wav_path),
        )
        with self.lock:
            self.intro_wav = str(wav_path)
            self.intro_duration_s = float(manifest.get("duration_s", 0.0))
            self.intro_text = text
            self.revision += 1
            self.updated_at = time.time()
        return {
            "canvas_id": self.canvas_id,
            "duration_s": self.intro_duration_s,
            "wav_path": self.intro_wav,
        }

    # ---------- internal: result decoration ----------
    def _with_error(self, result: dict[str, Any]) -> dict[str, Any]:
        """Attach `render_error` and `warnings` to a tool result.

        Caller already holds ``self.lock``.  Warnings are the L1 critic's
        output: they let the host LLM self-correct without vision.
        """
        if self.last_error:
            result["render_error"] = self.last_error
        if self.warnings:
            result["warnings"] = self.warnings
        return result

    # ---------- read ----------
    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            snap = {
                "canvas_id": self.canvas_id,
                "revision": self.revision,
                "node_count": len(self.graph.nodes),
                "edge_count": len(self.graph.edges),
                "math_mode": self.math_mode,
                "width": self.width,
                "height": self.height,
                "updated_at": self.updated_at,
            }
            if self.last_error:
                snap["render_error"] = self.last_error
            if self.warnings:
                snap["warnings"] = self.warnings
            return snap


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class CanvasRegistry:
    """In-process store of all live canvases.  Shared between the MCP tools
    and the FastAPI viewer."""

    def __init__(self) -> None:
        self._canvases: dict[str, Canvas] = {}
        self._lock = threading.Lock()

    def open(
        self,
        canvas_id: str | None = None,
        math_mode: bool = False,
        width: int = 700,
        height: int = 440,
        animate: bool = False,
    ) -> Canvas:
        cid = canvas_id or _new_canvas_id()
        with self._lock:
            if cid in self._canvases:
                return self._canvases[cid]
            c = Canvas(
                canvas_id=cid,
                math_mode=math_mode,
                width=width,
                height=height,
                animate=animate,
            )
            # initial empty render so the viewer has something to show
            c._rerender_locked()
            self._canvases[cid] = c
            return c

    def get(self, canvas_id: str) -> Canvas:
        with self._lock:
            c = self._canvases.get(canvas_id)
        if c is None:
            raise KeyError(f"canvas {canvas_id!r} not found")
        return c

    def close(self, canvas_id: str) -> bool:
        with self._lock:
            return self._canvases.pop(canvas_id, None) is not None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.snapshot() for c in self._canvases.values()]

    def latest_active(self, exclude: str | None = None) -> Canvas | None:
        """Return the canvas with the highest ``updated_at``, or None.

        Used to route tool calls that omit ``canvas_id`` to the canvas
        the LLM was most recently working on, instead of silently
        creating a fresh "default" canvas with mismatched settings
        (math_mode/animate).  ``exclude`` skips a specific id, typically
        the literal ``"default"`` so we only consider user-opened
        canvases.
        """
        with self._lock:
            candidates = [
                c for cid, c in self._canvases.items()
                if cid != exclude
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.updated_at)


# Module-level singleton — shared by mcp_server.server and service.app.
REGISTRY = CanvasRegistry()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_canvas_id() -> str:
    return "c_" + secrets.token_hex(4)


def _narration_dir(canvas_id: str) -> Path:
    """Per-canvas directory for narration assets (WAV files, etc.).

    Honours the env var ``SEVIM_DATA_DIR`` for the base path; defaults
    to ``~/.local/share/sevim/canvases``.  Files are scoped by canvas id
    so cleanup is just removing the per-canvas folder.
    """
    base = os.environ.get("SEVIM_DATA_DIR")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".local" / "share" / "sevim" / "canvases"
    return root / canvas_id


def _resolve_kind(kind: str) -> str:
    k = kind.strip().lower()
    if k in _KIND_ALIASES:
        return _KIND_ALIASES[k]
    if k in _VALID_KINDS:
        return k
    raise ValueError(
        f"unknown kind {kind!r}; expected one of: "
        + ", ".join(sorted(_VALID_KINDS | _KIND_ALIASES.keys()))
    )


def _empty_svg(w: int, h: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}"><rect width="100%" height="100%" fill="#fafafa"/>'
        f'<text x="{w//2}" y="{h//2}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="14" fill="#aaa">'
        f'empty canvas — waiting for the LLM to add nodes</text></svg>'
    )


# ---------------------------------------------------------------------------
# Fade-in post-processor (Path 1 of the in-canvas narration plan).
#
# Wraps every element bearing a data-nid / data-eid attribute with an SMIL
# <animate> child that fades the element from opacity 0 → 1 starting at
# ``step_index * step_duration`` seconds.  The result is a single
# self-playing SVG that builds itself incrementally — no JavaScript, no
# user interaction required.
# ---------------------------------------------------------------------------

import re as _re

# Match the opening of any element with a data-nid="…" or data-eid="…"
# attribute.  Captures the tag name, the full attribute string up to the
# attribute, the id key (`nid` or `eid`), and the id value.
_OPEN_TAG_RE = _re.compile(
    r'<(?P<tag>[a-zA-Z][a-zA-Z0-9]*)\b'
    r'(?P<head>[^>]*?)'
    r'\bdata-(?P<idkey>nid|eid)="(?P<idval>[^"]+)"'
    r'(?P<tail>[^>]*?)'
    r'(?P<close>/?>)',
)


def _apply_fade_in(
    svg: str,
    appear_meta: dict[str, tuple[float, int]],
    step_duration: float = 0.4,
    fade_duration: float = 0.35,
) -> str:
    """Inject SMIL fade-in animations on every element with appearance metadata.

    The viewer (canvas.html) replaces ``stage.innerHTML`` on every
    revision, which means SMIL ``begin="Ns"`` is *relative to the SVG's
    new load time*.  Naive positive begin values would restart every
    previously-faded element on every mutation — the "blink, blink, blink"
    rebuild cascade.

    Fix: compute an *absolute* begin per element from its batch timestamp:

        begin = (batch_started_at + step * step_duration) - now

    For elements added long ago, ``begin + dur`` is far in the past — we
    skip the ``<animate>`` entirely and the element renders fully opaque
    from byte 0, no flash.  For elements added in the most recent batch,
    begin is ~0s and they fade in normally (staggered by step_duration
    within the batch).  For mid-fade elements during rapid re-renders,
    begin is slightly negative so the browser jumps directly to the
    correct opacity instead of restarting from 0.

    Two patch shapes per match:

    1.  ``<g data-nid="X">…</g>`` (or any container element) — append
        ``style="opacity:0"`` to the opening tag and inject an
        ``<animate>`` element immediately after the opening tag.

    2.  ``<path data-eid="X" … />`` (self-closing) — wrap in a parent
        ``<g style="opacity:0"><animate.../><path.../></g>`` so the
        animation can target the wrapper's opacity.

    The post-processor is purely textual to avoid the namespace headaches
    of round-tripping through ElementTree.  It only touches elements
    whose id appears in ``appear_meta``; everything else passes through
    byte-identically.
    """
    if not appear_meta:
        return svg

    now = time.time()
    # Buffer past fade end before we drop the <animate>.  Keeps the
    # animation in place during a brief window after completion in case
    # the browser is still in the middle of one frame, but doesn't keep
    # it indefinitely.
    settled_buffer = 0.05

    def _animate_tag(begin_s: float) -> str:
        return (
            f'<animate attributeName="opacity" from="0" to="1" '
            f'begin="{begin_s:g}s" dur="{fade_duration:g}s" fill="freeze"/>'
        )

    def _replace(m: _re.Match) -> str:
        idval = m.group("idval")
        meta = appear_meta.get(idval)
        if meta is None:
            return m.group(0)
        batch_t, step = meta
        # Absolute begin: when, on the wall-clock, this element should
        # *start* fading in.  Subtract `now` to make it relative to the
        # SVG's new load time so SMIL plays it correctly post-replace.
        begin_s = (batch_t + step * step_duration) - now
        # Already faded long ago — emit no animation; element renders
        # fully opaque on load.  This is the line that kills the blink.
        if begin_s + fade_duration <= -settled_buffer:
            return m.group(0)
        tag = m.group("tag")
        head = m.group("head")
        idkey = m.group("idkey")
        tail = m.group("tail")
        is_self_closing = m.group("close") == "/>"
        # Reassemble the opening (without the inserted style).
        original_open = (
            f'<{tag}{head} data-{idkey}="{idval}"{tail}{m.group("close")}'
        )
        if is_self_closing:
            # Wrap the entire self-closing element in a fading <g>.
            return (
                f'<g style="opacity:0">{_animate_tag(begin_s)}{original_open}</g>'
            )
        # Container element — patch style on the existing tag and inject
        # the <animate> as a first child.
        if 'style="' in head or 'style="' in tail:
            # Existing style attribute; prepend opacity:0;.
            patched_head = _re.sub(
                r'style="', 'style="opacity:0;', head, count=1
            )
            patched_tail = _re.sub(
                r'style="', 'style="opacity:0;', tail, count=1
            ) if 'style="' in tail else tail
            head_to_use, tail_to_use = patched_head, patched_tail
        else:
            head_to_use, tail_to_use = head, f'{tail} style="opacity:0"'
        return (
            f'<{tag}{head_to_use} data-{idkey}="{idval}"{tail_to_use}>'
            f'{_animate_tag(begin_s)}'
        )

    return _OPEN_TAG_RE.sub(_replace, svg)
