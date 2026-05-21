"""Sevim Studio backend — direct-to-Anthropic chat with sevim tools.

Mounted into the main FastAPI app at ``/studio/*``.  Endpoints:

  GET  /studio                serve the SPA
  POST /studio/chat           one-shot chat turn (streaming SSE response)
  POST /studio/canvas/new     spawn a fresh studio canvas
  GET  /studio/health         API key configured? piper available?

The chat endpoint loops Claude's tool-use turns server-side: the
client sends one user message, the server pumps the multi-step
tool-use conversation with Anthropic until Claude returns a stop
reason, streaming text deltas back to the client as SSE events.
Tool calls execute against the same CanvasRegistry singleton the
MCP path uses, so the canvas in Studio is the *same* canvas the
MCP server's viewer would show.

Required env var: ``ANTHROPIC_API_KEY``.
Optional env var: ``SEVIM_STUDIO_MODEL`` (default ``claude-opus-4-7``).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from service.canvas import REGISTRY
from studio.auth import (
    clear_cookie,
    current_user,
    is_required as auth_is_required,
    request_magic_link,
    require_user,
    verify_link_and_set_cookie,
)


_STATIC = Path(__file__).resolve().parent / "static"

# OpenAI-compatible Chat Completions endpoint Sevim talks to.  Defaults
# to the user's local vLLM but the env vars below let the same code
# point at api.openai.com (or any other compatible service).
_DEFAULT_VLLM_URL = "http://127.0.0.1:8000/v1"
_DEFAULT_VLLM_MODEL = "Qwen/Qwen2.5-14B-Instruct-AWQ"


def _backend() -> str:
    """Selected studio backend.  Always ``"vllm"`` in v0.4 (the OpenAI-
    compatible Chat Completions API path).  The Anthropic-direct backend
    was removed when we collapsed to the single sevim_express pipeline."""
    return "vllm"


def _vllm_url() -> str:
    return (os.environ.get("SEVIM_VLLM_URL") or _DEFAULT_VLLM_URL).rstrip("/")


def _vllm_model() -> str:
    return os.environ.get("SEVIM_VLLM_MODEL") or _DEFAULT_VLLM_MODEL


# --------------------------------------------------------------------
# Multi-backend selector.  Each entry is presented as a choice in the
# UI dropdown (see studio/static/studio.html) and resolved to a
# (base_url, model, api_key, model_id) tuple at request time.
#
# The "id" field is what gets persisted in the telemetry model_id
# column so future fine-tuning corpora can be filtered per backend.
# Display names are user-facing; ids stay stable across renames.
#
# A backend whose env vars resolve to an empty URL is considered
# unconfigured: it stays in the catalog (so the UI can show "not
# configured") but cannot be chosen.
# --------------------------------------------------------------------

def _qwen_lora_vllm_url() -> str:
    """vLLM endpoint that hosts Qwen2.5-7B + Khayyam-Math LoRA adapters.

    Lives at SEVIM_QWEN_VLLM_URL --- populated by the production CDK
    stack to point at the dedicated GPU instance (g6.xlarge spot)
    serving qwen_lora_v4.  Empty in dev unless the user sets it.
    """
    return (os.environ.get("SEVIM_QWEN_VLLM_URL") or "").rstrip("/")


# Reachability cache for the Qwen vLLM endpoint.  Without this the
# admin can flip the active model to qwen_lora_v4, but during a vLLM
# bootstrap (first deploy, post-reboot model reload, OOM crash loop)
# the endpoint is set in the env yet not yet serving --- chat
# requests then time out instead of falling back to OpenAI.  The probe
# keeps a (status, last_check_unix) pair and re-tests at most once
# every PROBE_TTL_S seconds; one cheap GET /models per minute beats
# a 180-second request timeout on every chat turn.
import time as _ttime  # noqa: E402

_PROBE_TTL_S = 30.0
_qwen_probe_cache: dict[str, tuple[bool, float]] = {}


def _qwen_lora_vllm_reachable() -> bool:
    """Return True iff the Qwen vLLM endpoint answers a quick probe.

    Returns False when SEVIM_QWEN_VLLM_URL is unset, the endpoint
    refuses the connection, or the probe times out.  Cached for
    PROBE_TTL_S seconds so the cost is one HTTP HEAD per minute per
    Fargate task.
    """
    url = _qwen_lora_vllm_url()
    if not url:
        return False
    now = _ttime.time()
    cached = _qwen_probe_cache.get(url)
    if cached and (now - cached[1]) < _PROBE_TTL_S:
        return cached[0]
    try:
        import httpx
        with httpx.Client(timeout=1.5) as c:
            r = c.get(f"{url}/models")
        ok = (r.status_code == 200)
    except Exception:  # noqa: BLE001
        ok = False
    _qwen_probe_cache[url] = (ok, now)
    return ok


def model_catalog() -> list[dict[str, Any]]:
    """Return the list of selectable backends in the order the UI
    should present them.  Each entry is a dict with stable fields:

      id            persisted in telemetry.model_id; never user-facing
      label         shown in the admin page
      default       true for the entry pre-selected when nothing is set
      available     false if the backend cannot serve traffic right now
      reason        human-readable reason when available=false
      experimental  display a small "experimental" badge in the UI
      cost_tier     informational hint ("quality" / "cheap" / "in-house")
    """
    openai_ready = bool(os.environ.get("OPENAI_API_KEY"))
    qwen_url = _qwen_lora_vllm_url()
    # Two-stage Qwen availability: the URL is configured AND a quick
    # probe says it answers.  This way, if vLLM is bootstrapping
    # (first deploy), reclaimed (spot), or OOM-crashing, we mark Qwen
    # as unavailable so the fallback chain routes to OpenAI rather
    # than timing out per-request.
    qwen_reachable = bool(qwen_url) and _qwen_lora_vllm_reachable()
    qwen_reason = (
        "" if qwen_reachable else
        ("SEVIM_QWEN_VLLM_URL is not configured" if not qwen_url
         else "vLLM endpoint not responding (bootstrap / outage)")
    )
    return [
        # Order matters: ``get_active_model()`` picks the first
        # available entry as a hard fallback when the admin setting
        # and the marked default are both unreachable.  The in-house
        # Qwen LoRA is the preferred backend once it's wired up; if
        # the GPU isn't running we cleanly fall back to OpenAI.
        {
            "id": "qwen_lora_v4",
            "label": "Qwen 2.5-7B + Khayyam Math v4 (in-house LoRA)",
            "default": True,
            "available": qwen_reachable,
            "reason": qwen_reason,
            "experimental": True,
            "cost_tier": "in-house",
        },
        {
            "id": "gpt-4o",
            "label": "GPT-4o (OpenAI)",
            "default": False,
            "available": openai_ready,
            "reason": "" if openai_ready else "OPENAI_API_KEY is not configured",
            "experimental": False,
            "cost_tier": "quality",
        },
        {
            "id": "gpt-4o-mini",
            "label": "GPT-4o mini (OpenAI · cheap)",
            "default": False,
            "available": openai_ready,
            "reason": "" if openai_ready else "OPENAI_API_KEY is not configured",
            "experimental": False,
            "cost_tier": "cheap",
        },
        {
            "id": "qwen_base",
            "label": "Qwen 2.5-7B (base, no LoRA)",
            "default": False,
            "available": qwen_reachable,
            "reason": qwen_reason,
            "experimental": False,
            "cost_tier": "in-house",
        },
    ]


def get_active_model() -> str:
    """Return the model id that should serve the next chat request.

    Resolution order:
      0. ``SEVIM_FORCE_ACTIVE_MODEL`` env var (deployment-time override).
         Beats everything else.  Used by the fine-tune workflow to
         atomically promote a freshly-trained model id on cdk deploy
         without needing an admin click — set the env var, deploy,
         and every Fargate task immediately picks up the new model.
         Unset the env var (next cdk deploy) to release the override
         and let the normal resolution chain take over again.
      1. The admin's ``active_model`` setting from telemetry, if it
         points to an available backend.
      2. The catalog entry marked ``default=True``, if it is available.
      3. The first ``available=True`` entry in catalog order.
      4. The marked default (even if unavailable) — so the caller still
         gets a deterministic string to log.

    This way the operator can set Qwen as the preferred backend, but
    if its vLLM endpoint is offline (spot reclaim, GPU instance not
    yet deployed) traffic seamlessly falls through to OpenAI.
    """
    catalog_list = model_catalog()
    catalog = {m["id"]: m for m in catalog_list}
    force = os.environ.get("SEVIM_FORCE_ACTIVE_MODEL", "").strip()
    if force:
        # A force-override might point at a fine-tuned id not yet in
        # the catalog (the catalog is hand-curated, the fine-tune id
        # is auto-generated).  Return it verbatim — the chat path
        # ships whatever string we hand it directly to OpenAI's
        # /chat/completions, and OpenAI knows the fine-tune id.
        return force
    try:
        from sevim.telemetry import get_telemetry
        tel = get_telemetry()
        chosen = tel.get_setting("active_model") if tel is not None else None
    except Exception:  # noqa: BLE001
        chosen = None
    if chosen and chosen in catalog and catalog[chosen]["available"]:
        return chosen
    for m in catalog_list:
        if m["default"] and m["available"]:
            return m["id"]
    for m in catalog_list:
        if m["available"]:
            return m["id"]
    return next((m["id"] for m in catalog_list if m["default"]), "gpt-4o")


def resolve_backend() -> tuple[str, str, str | None, str]:
    """Resolve the admin-selected backend to
    ``(base_url, model_name, api_key, model_id)``.

    Falls back to the catalog default (``gpt-4o``) when the admin
    setting is unset or names an unavailable backend.  The chat
    pipeline calls this with no arguments — the user has no input
    into the choice.
    """
    chosen = get_active_model()
    # Fine-tuned model ids are of the form
    # `ft:<base>:<org>:<suffix>:<id>` and are dispatched to OpenAI's
    # standard chat-completions endpoint with the fine-tune id as
    # the model name.  This branch is hit when SEVIM_FORCE_ACTIVE_MODEL
    # carries a fine-tune id, or when an admin adds the id to
    # MODEL_CATALOG and selects it via /studio/admin.
    if chosen.startswith("ft:"):
        return (
            "https://api.openai.com/v1",
            chosen,
            os.environ.get("OPENAI_API_KEY"),
            chosen,
        )
    if chosen == "gpt-4o":
        # The base URL still comes from SEVIM_VLLM_URL so dev can
        # point this at a local OpenAI-compatible server; the model
        # name is hard-coded here because the admin choice should
        # not be overridden by SEVIM_VLLM_MODEL env-var drift.
        return (_vllm_url(), "gpt-4o",
                os.environ.get("OPENAI_API_KEY"), "gpt-4o")
    if chosen == "gpt-4o-mini":
        return (_vllm_url(), "gpt-4o-mini",
                os.environ.get("OPENAI_API_KEY"), "gpt-4o-mini")
    if chosen == "qwen_lora_v4":
        # vLLM with --enable-lora exposes the adapter as a separate
        # "model" name.  Convention: the adapter id is the served
        # model name; the base lives under "Qwen/Qwen2.5-7B-Instruct".
        return (_qwen_lora_vllm_url(), "qwen_lora_v4",
                os.environ.get("SEVIM_QWEN_VLLM_KEY"), "qwen_lora_v4")
    if chosen == "qwen_base":
        return (_qwen_lora_vllm_url(), "Qwen/Qwen2.5-7B-Instruct",
                os.environ.get("SEVIM_QWEN_VLLM_KEY"), "qwen_base")
    # Should not reach.
    return (_vllm_url(), _vllm_model(), os.environ.get("OPENAI_API_KEY"), "gpt-4o")


# --------------------------------------------------------------------
# Admin auth.  Comma-separated whitelist in SEVIM_ADMIN_EMAILS; the
# user's e-mail from the signed magic-link cookie must match.  When
# unset there is NO admin — the page returns 404 to everyone.
# --------------------------------------------------------------------

def _admin_emails() -> set[str]:
    raw = os.environ.get("SEVIM_ADMIN_EMAILS") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(request: Request) -> bool:
    from studio.auth import current_user
    user = current_user(request)
    if not user:
        return False
    return user.lower() in _admin_emails()


def require_admin(request: Request) -> str:
    """FastAPI dependency: 404 (NOT 401) for non-admin callers, so the
    admin URL is undiscoverable.  Returns the operator e-mail on
    success — used as the ``updated_by`` audit trail in settings.
    """
    from studio.auth import current_user
    user = current_user(request)
    if not user or user.lower() not in _admin_emails():
        raise HTTPException(404, "Not Found")
    return user

# --------------------------------------------------------------------
# Follow-up vs new-topic detection
# --------------------------------------------------------------------

# Phrases that strongly signal the user is iterating on the CURRENT
# canvas rather than asking for a fresh topic. When ANY of these
# appears in the prompt, the studio auto-attaches the current canvas
# to sevim_express as REFINEMENT MODE context. Otherwise the express
# call gets no implicit prior — a clean fresh figure is generated.
#
# Default is "no implicit prior" because the contamination cost
# (old + new SVGs blended) is worse than the missed-refinement cost
# (user can pin the canvas explicitly if they want continuity).
_FOLLOWUP_KEYWORDS: tuple[str, ...] = (
    # references to the existing figure
    "this figure", "this diagram", "this canvas", "this image",
    "the figure", "the diagram", "the canvas",
    "in the same figure", "on the same figure", "same figure",
    "in the picture", "on the picture",
    # action verbs that mutate an existing figure
    "add ", "remove ", "delete ", "change ", "modify ",
    "highlight ", "annotate ", "extend ", "rotate ",
    "color ", "colour ", "recolor", "recolour",
    "scale ", "shrink ", "enlarge ", "move ",
    "fix the", "fix this", "update the", "update this",
    "refine", "improve",
    # continuation
    "continue", "next step", "explain step", "explain the ",
    "elaborate", "more detail", "in more detail",
    # explicit time-anchors
    "now show", "now add", "now change", "now make",
    "also show", "also add", "also include",
    "instead", "instead of",
    # bare pronouns referring to the current figure
    "make it", "change it", "color it", "colour it",
    "label it",
)


def _looks_like_followup(prompt: str) -> bool:
    """Cheap heuristic: does this prompt look like a follow-up on
    the current canvas, or a fresh new-topic request?

    Returns True for follow-ups. Defaults to False (new topic) when
    no follow-up signal is present, so a brand-new prompt like
    "draw a Venn diagram" doesn't accidentally pull in the prior
    Pythagorean SVG as REFINEMENT-MODE context.
    """
    if not prompt:
        return False
    p = prompt.lower().strip()
    return any(kw in p for kw in _FOLLOWUP_KEYWORDS)


router = APIRouter(prefix="/studio")


# ---------------------------------------------------------------------------
# Tool schemas — wire sevim ops into Anthropic tool-use definitions.
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "sevim_express",
        "description": (
            "Build a complete teaching figure for the user's request.  "
            "ONE call, no follow-ups: Sevim asks gpt-4o to emit a "
            "self-contained SVG + phrase-timed narration in a single "
            "structured response, runs a vision-review loop (≤3 "
            "retries) against a strict math-teacher rubric, synthesises "
            "piper TTS audio, and ships the canvas to the user.  Works "
            "for any concept that can be drawn statically: matrix "
            "multiplication, 3SAT reductions, set diagrams, function "
            "plots, geometry, complexity-class diagrams, derivative "
            "visualisations, etc.  This is the ONLY canvas-building "
            "tool you have — there is no separate sevim_open / "
            "sevim_plan / sevim_apply / sevim_narrate.  Call this with "
            "the user's request, then end your turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The user's request, verbatim or lightly clarified.",
                },
                "context_canvas_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional canvas IDs (express_xxxxx) of prior "
                        "figures the user is refining or combining.  "
                        "When set, Sevim attaches each prior figure's "
                        "PNG snapshot + the prompt that made it to the "
                        "LLM call so gpt-4o sees the real pixels.  "
                        "Studio auto-supplies the most-recent canvas "
                        "and any pinned canvases when the user asks "
                        "for a refinement; you typically don't need to "
                        "pass this manually."
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
]


async def _execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    session_id: str | None = None,
    on_svg_chunk: Callable[[str], Awaitable[None]] | None = None,
    on_primer_chunk: Callable[[str], Awaitable[None]] | None = None,
    owner: str | None = None,
    flagged: bool = False,
) -> dict[str, Any]:
    """Run the single Studio tool: ``sevim_express``.

    Loads any prior canvases the user is refining out of REGISTRY,
    delegates to ``express_figure`` for SVG generation + vision audit,
    then injects the resulting SVG and synthesises narration.

    When ``session_id`` is provided AND telemetry is enabled, records
    the turn (prompt, canvas, retries, cost) for later mining.
    """
    if name != "sevim_express":
        raise ValueError(f"unknown tool {name!r} — only sevim_express is exposed")
    from studio.express import express_figure
    from sevim.telemetry import get_telemetry
    from studio.sessions import estimate_express_cost
    import time as _time
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("sevim_express requires a non-empty 'prompt'")
    # Conversational context: pull prior canvases out of REGISTRY so
    # gpt-4o sees the actual figure(s) the user is refining (SVG XML +
    # PNG snapshot + original prompt + narration script).
    #
    # A multi-turn audit found that when the user switches TOPIC (e.g.,
    # T1 'trapezoid area' → T2 'derivative of f(x)=x²'), passing the
    # prior canvas as context causes the model to OVERLAY the new
    # figure on the old one ('refinement creep').  We pre-classify the
    # new prompt: if it has no refinement cue (add / change / continue
    # / 'this figure' / etc.) we drop the prior context so each new
    # topic gets a fresh canvas.
    from studio.express import looks_like_refinement
    context_ids = list(args.get("context_canvas_ids") or [])
    context_canvases: list[dict[str, Any]] = []
    if context_ids and looks_like_refinement(prompt):
        for prior_id in context_ids[:3]:  # cap at 3 prior figures (cost)
            try:
                pc = REGISTRY.get(prior_id)
            except KeyError:
                continue
            if not getattr(pc, "svg", None):
                continue
            context_canvases.append({
                "id": pc.canvas_id,
                "svg": pc.svg,
                "prompt": pc.genesis_prompt or "",
                "narration": (pc.narration_manifest or {}).get("phrases") or [],
            })
    t0 = _time.monotonic()
    # Backend selection is admin-controlled, not request-controlled:
    # ``resolve_backend()`` consults the server-side ``active_model``
    # setting (set from the admin page) rather than anything the user
    # might supply.  This keeps individual chat sessions from picking
    # the experimental Qwen path on their own.
    base_url, model_name, api_key, resolved_model_id = resolve_backend()

    # Theory primer runs CONCURRENTLY with the figure generation so the
    # learner reads / hears the conceptual setup while the SVG is still
    # rendering.  Primer is always synthesised by gpt-4o-mini (text-only,
    # fast, ~$0.0001 per turn) regardless of which model is generating
    # the figure — the in-house Qwen LoRA is figure-only.  When OpenAI
    # creds aren't available (dev / CI) the primer task short-circuits
    # to an empty string and the chat falls through to figure-only mode.
    from studio.express import generate_theory_primer
    _openai_key = os.environ.get("OPENAI_API_KEY")
    primer_url = "https://api.openai.com/v1"
    primer_model = os.environ.get("SEVIM_PRIMER_MODEL", "gpt-4o-mini")

    async def _run_primer() -> str:
        if on_primer_chunk is None or not _openai_key:
            return ""
        return await generate_theory_primer(
            user_prompt=prompt,
            base_url=primer_url,
            model=primer_model,
            api_key=_openai_key,
            on_text_chunk=on_primer_chunk,
        )

    primer_task = asyncio.create_task(_run_primer())
    figure_task = asyncio.create_task(express_figure(
        user_prompt=prompt,
        base_url=base_url,
        model=model_name,
        api_key=api_key,
        context_canvases=context_canvases,
        on_svg_chunk=on_svg_chunk,
    ))
    # Await figure first because the rest of _execute_tool unpacks its
    # result.  The primer task runs to completion alongside; we collect
    # the assembled string for telemetry / canvas prelude after.
    result = await figure_task
    primer_text = await primer_task
    duration_s = _time.monotonic() - t0
    cid = "express_" + secrets.token_hex(16)
    c = REGISTRY.open(canvas_id=cid, math_mode=True, animate=False,
                      width=900, height=620, owner=owner)
    c.set_raw_svg(result["svg"])
    c.genesis_prompt = prompt
    narration = result.get("narration") or []
    # Diagnostic: how many phrases ship with a non-empty highlight
    # array?  When the answer is "0", the figure renders but the
    # learner sees nothing flash while the audio plays — the
    # "no highlight is happening" complaint surfaced exactly this
    # way.  Also count phrases that reference an id that DOES
    # exist in the served SVG so we can tell a missing-id failure
    # apart from a missing-id-emission failure.
    if narration:
        import re as _re
        svg_ids = set(_re.findall(r"id\s*=\s*['\"]([^'\"]+)['\"]",
                                  result.get("svg", "")))
        with_h = 0
        unmatched = 0
        for ph in narration:
            h = ph.get("highlight") or []
            if isinstance(h, str):
                h = [h]
            if any(x for x in h if isinstance(x, str) and x.strip()):
                with_h += 1
                for x in h:
                    if isinstance(x, str) and x.strip() and x not in svg_ids:
                        unmatched += 1
        print(
            f"[express] narration highlight density: "
            f"phrases={len(narration)} with_highlight={with_h} "
            f"unmatched_ids={unmatched} svg_ids={len(svg_ids)}",
            flush=True, file=__import__("sys").stderr,
        )
    if narration:
        _narrate_t0 = _time.monotonic()
        narrate_out = c.narrate(narration)
        _narrate_dt = _time.monotonic() - _narrate_t0
        print(
            f"[express] narrate done: phrases={len(narration)} "
            f"dt={_narrate_dt:.2f}s wav_phrases={narrate_out.get('phrase_count')}",
            flush=True, file=__import__("sys").stderr,
        )
    else:
        narrate_out = {}
    retries_used = result.get("retries_used", 0)
    cost_estimate = estimate_express_cost(retries_used=retries_used)
    review_history = result.get("review_history", [])
    # Telemetry: fire-and-forget so the RDS write latency (1-2s per
    # row; record_turn + record_canvas + N repair pairs) does NOT
    # delay tool_result reaching the browser.  The express turn was
    # already 25-30s by the time we got here; adding telemetry on the
    # critical path was pushing total wall-clock past the user's
    # patience threshold.  Errors get printed but never raise.
    tel = get_telemetry()
    if tel is not None and session_id:
        def _record_async(
            tel=tel,
            session_id=session_id,
            prompt=prompt,
            canvas_id=c.canvas_id,
            context_ids=[pc["id"] for pc in context_canvases],
            n_phrases=len(narration),
            retries_used=retries_used,
            review_history=review_history,
            duration_s=duration_s,
            cost_estimate=cost_estimate,
            resolved_model_id=resolved_model_id,
            title=result.get("title", ""),
            svg=result.get("svg", ""),
            narration_phrases=narration,
            repairs=list(result.get("repairs") or []),
            flagged=flagged,
            math_claims=list(result.get("math_claims") or []),
        ) -> None:
            try:
                turn_id = tel.record_turn(
                    session_id=session_id, user_prompt=prompt,
                    canvas_id=canvas_id, prior_canvas_ids=context_ids,
                    n_phrases=n_phrases, retries_used=retries_used,
                    review_history=review_history, duration_s=duration_s,
                    cost_usd_estimate=cost_estimate,
                    intent=("flagged" if flagged else "express"),
                    model_id=resolved_model_id,
                )
                tel.record_canvas(
                    canvas_id=canvas_id, session_id=session_id,
                    turn_id=turn_id, title=title, svg=svg,
                    narration=narration_phrases,
                    model_id=resolved_model_id,
                    math_claims=math_claims,
                )
                for pair in repairs:
                    tel.record_repair_pair(
                        session_id=session_id, turn_id=turn_id,
                        attempt_index=pair.get("attempt_index", 0),
                        user_prompt=prompt,
                        bad_svg=pair.get("bad_svg"),
                        bad_narration=pair.get("bad_narration"),
                        critique=pair.get("critique"),
                        good_svg=pair.get("good_svg"),
                        good_narration=pair.get("good_narration"),
                        model_id=resolved_model_id,
                    )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[telemetry] background record FAILED: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True, file=__import__("sys").stderr,
                )

        # Run in default loop's thread executor — Telemetry uses the
        # synchronous psycopg client so we MUST go off-thread to avoid
        # blocking the asyncio event loop.
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _record_async)
    return {
        "canvas_id": c.canvas_id,
        "view_url": f"/canvas/{c.canvas_id}/view",
        "title": result.get("title", ""),
        "n_phrases": len(narration),
        "retries_used": retries_used,
        "review_history": review_history,
        "context_used": [pc["id"] for pc in context_canvases],
        "narration": narrate_out,
        "duration_s": duration_s,
        "cost_usd_estimate": cost_estimate,
        # Empty when no OPENAI_API_KEY or when the primer call failed.
        # The frontend already received the streamed chunks; this field
        # is for clients that consume the JSON result directly (e.g.
        # MCP, integration tests).
        "primer": primer_text,
    }


# ---------------------------------------------------------------------------
# Tool-format translation (Anthropic-style internal schema → OpenAI
# function-calling format the OpenAI/vLLM Chat Completions API expects).
# ---------------------------------------------------------------------------

def _tools_openai_format() -> list[dict[str, Any]]:
    """Translate the internal TOOLS list to OpenAI function-calling format."""
    out: list[dict[str, Any]] = []
    for t in TOOLS:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        })
    return out


# ---------------------------------------------------------------------------
# Chat endpoint — streams Claude's response back to the browser.
# ---------------------------------------------------------------------------

class ChatTurn(BaseModel):
    role: str          # "user" | "assistant"
    content: str       # plain text


class ChatReq(BaseModel):
    history: list[ChatTurn] = []
    user: str
    # Most-recent canvas the iframe is showing.  Studio attaches this
    # to the express call so the LLM sees the figure the user is
    # currently looking at and can refine it instead of starting fresh.
    canvas_id: str | None = None
    # Additional canvases the user has explicitly pinned ("📌") in the
    # chat panel.  Used for combining figures across turns.  Capped at
    # ~3 by the frontend to bound LLM cost.
    prior_canvas_ids: list[str] = []
    # Stable per-tab session id the frontend generates on first load
    # and persists in localStorage.  Used by telemetry, rate limiter,
    # and cost guard.  When missing (older frontends), Studio assigns
    # a synthetic one for this single request.
    session_id: str | None = None
    # True when this turn came from the "Fix this figure" button —
    # the user is reporting a problem with the current figure.  The
    # turn is tagged in telemetry so the problem-patterns admin view
    # can surface what users are struggling with.
    flagged: bool = False


SYSTEM_PROMPT = (
    "You are a real-time visual TUTOR — like ChatGPT but with a live "
    "diagram canvas next to the chat.  You have exactly ONE tool: "
    "``sevim_express(prompt)``.  It draws/updates the canvas (SVG + "
    "phrase-timed audio narration).  Calling it generates fresh audio "
    "every time, so DO NOT call it for follow-ups that don't actually "
    "change what's on the canvas.\n"
    "\n"
    "DECISION RULE — call sevim_express, or just reply in chat?\n"
    "\n"
    "  Call sevim_express WHEN:\n"
    "    • the user asks for a new figure / visualization\n"
    "    • the user asks to MODIFY the figure on screen ('add the "
    "      conclusion', 'use different numbers', 'highlight the chord', "
    "      'combine this with the matrix-mult one')\n"
    "    • the user asks for a different example of the same concept\n"
    "\n"
    "  Reply in CHAT (no tool call) WHEN:\n"
    "    • the user asks a complementary / clarifying question about "
    "      the figure already on screen ('why is that true?', 'what's "
    "      the area underneath?', 'what does the highlighted arc mean?', "
    "      'explain step 3 again')\n"
    "    • the user wants conceptual elaboration on something just "
    "      discussed, where the existing figure already supports the "
    "      explanation\n"
    "    • greetings, acknowledgements, off-topic chat\n"
    "    • the request is too vague to draw — ask a clarifying "
    "      question instead\n"
    "\n"
    "WHEN YOU REPLY IN CHAT:\n"
    "  • Be concise (1-3 sentences).  This is a chat reply, not a "
    "    re-lesson — the canvas already has the diagram.\n"
    "  • Lead with the SPECIFIC INSIGHT for this case.  Do NOT open "
    "    by re-defining a term the user already used in the question.\n"
    "  • NEVER describe what is visually obvious in the figure.  "
    "    Sentences like 'we see…', 'here is…', 'on the left/right…', "
    "    'the figure shows…', 'A is connected to B' add zero math "
    "    content — the user can see those things.  If you'd say 'see "
    "    the orange arc', say what's TRUE ABOUT the orange arc "
    "    ('the orange arc is the supplementary angle, so α + β = π').\n"
    "  • Do NOT re-narrate what the canvas's audio already covered.  "
    "    Add NEW information; don't repeat the previous turn.\n"
    "  • Do NOT call sevim_express just to redraw the same figure.\n"
    "\n"
    "WHEN YOU CALL sevim_express:\n"
    "  • Studio AUTO-attaches the most-recent canvas + any pinned "
    "    canvases as context — you can refer to them naturally in your "
    "    sevim_express prompt without quoting canvas IDs.\n"
    "  • For a refinement, write a prompt that bundles the original "
    "    request plus the new change so the model has full context.\n"
    "  • After the tool returns, end your turn with at most ONE short "
    "    acknowledgement sentence in chat (e.g. 'Updated.' or 'Here it "
    "    is.').  The canvas's narration covers the explanation.\n"
    "\n"
    "WHAT THE CANVAS CAN DRAW WELL — know your own range:\n"
    "  • function graphs and 3-D surfaces — any y = f(x) or z = f(x,y)\n"
    "  • exact symbolic calculus — derivatives, gradients, Hessians, "
    "integrals, limits, and finding AND classifying critical points "
    "(computed exactly with a CAS, never guessed)\n"
    "  • matrices and matrix operations; vectors\n"
    "  • automata, state machines, graphs, trees, flowcharts\n"
    "  • geometry — triangles, circles, the unit circle, Pythagoras\n"
    "  • fractions, number lines, place value, Venn diagrams, "
    "probability and normal-distribution figures, algorithm traces\n"
    "  Together these span primary-school through university math.\n"
    "\n"
    "BE HONEST ABOUT LIMITS: if a request is not math, or needs a kind "
    "of figure the canvas does not do well, say so plainly and offer "
    "the closest thing you CAN draw — never pretend or produce a "
    "figure you know is wrong.\n"
    "\n"
    "USE THE CONVERSATION: you have the full history of this session. "
    "When the user says 'it', 'that', 'the figure', 'fix it', 'change "
    "the colour', they mean what is already on the canvas — refer back "
    "and act on it; never ask them to repeat themselves.\n"
    "\n"
    "How sevim_express works internally (FYI): gpt-4o emits SVG + "
    "phrase-timed narration in one structured response; Sevim audits "
    "the figure against a math-teacher rubric (up to 3 retries) and "
    "synthesises piper TTS audio with phrase highlights.  The "
    "math-correctness inspector specifically catches false claims like "
    "'angles of a triangle sum to 2π' (truth: π) — trust it."
)


@router.post("/chat")
async def chat(
    req: ChatReq,
    request: Request,
    user: str = Depends(require_user),
):
    """Studio chat entrypoint.

    Pipeline order:
      1. Assign / record session_id (telemetry).
      2. Content filter (reject obvious off-topic / injection prompts).
      3. Rate limit + cost guard (reject sessions over their caps).
      4. Preference pre-router (handles backend-only requests without
         a gpt-4o call).
      5. Otherwise stream the gpt-4o → sevim_express pipeline.
    """
    import sys
    import secrets as _secrets

    session_id = req.session_id or ("anon_" + _secrets.token_hex(4))
    req.session_id = session_id  # ensure downstream code sees a value

    print(f"[studio-chat] POST received: session={session_id[:12]!r} "
          f"user={req.user[:60]!r} canvas_id={req.canvas_id}",
          flush=True, file=sys.stderr)

    # 1. Session bookkeeping (no-op if telemetry disabled).
    from sevim.telemetry import get_telemetry
    from studio.sessions import (
        get_rate_limiter, check_cost_guard, hash_ip,
    )
    from studio.safety import check_prompt
    tel = get_telemetry()
    # Resolve the real client IP.  Behind an ALB / CloudFront the
    # immediate request.client.host is the load balancer; the actual
    # client's IP is the LEFTMOST entry in X-Forwarded-For (the chain
    # is "client, proxy1, proxy2, ...").  Trust the header only when
    # SEVIM_TRUST_PROXY=1 (default off; turn ON in the AWS deploy).
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and os.environ.get("SEVIM_TRUST_PROXY", "0") == "1":
        client_ip = forwarded.split(",")[0].strip() or None
    else:
        client_ip = (request.client.host if request.client else None)
    ip_hash_value = hash_ip(client_ip)
    if tel is not None:
        tel.upsert_session(
            session_id=session_id,
            user_agent=request.headers.get("user-agent"),
            ip_hash=ip_hash_value,
        )

    # 2. Content filter.
    deny = check_prompt(req.user)
    if deny is not None:
        async def deny_stream():
            yield {"event": "text", "data": json.dumps({"text": deny})}
            yield {"event": "done",
                   "data": json.dumps({"stop_reason": "content_filter"})}
        return EventSourceResponse(deny_stream())

    # 3. Rate limit + cost guard.  IP-bucket guards against the
    # "clear localStorage to refresh session_id" bypass.
    rl_msg = get_rate_limiter().check(session_id, ip_hash=ip_hash_value)
    if rl_msg is None:
        rl_msg = check_cost_guard(session_id)
    if rl_msg is not None:
        async def limit_stream():
            yield {"event": "text", "data": json.dumps({"text": rl_msg})}
            yield {"event": "done",
                   "data": json.dumps({"stop_reason": "rate_limited"})}
        return EventSourceResponse(limit_stream())

    # 4. Preference pre-router (no LLM round-trip needed).
    from studio.preferences import parse_preference
    pref = parse_preference(req.user)
    if pref is not None:
        print(f"[studio-chat] preference matched: {pref}", flush=True, file=sys.stderr)
        async def pref_stream():
            yield {"event": "tool_call", "data": json.dumps({
                "name": "set_preference",
                "args_keys": [f"{pref.get('key','?')}={pref.get('value','?')}"],
            })}
            yield {"event": "tool_result", "data": json.dumps(pref)}
            yield {"event": "text", "data": json.dumps(
                {"text": pref.get("status", "Preference applied.")}
            )}
            yield {"event": "done",
                   "data": json.dumps({"stop_reason": "preference_applied"})}
            # Telemetry: lightweight record (no canvas, no cost).
            if tel is not None:
                tel.record_turn(
                    session_id=session_id,
                    user_prompt=req.user,
                    intent="preference",
                )
        return EventSourceResponse(pref_stream())

    # 5. Full express pipeline.
    # ping=15 emits an SSE keepalive comment every 15 s.  Without it,
    # the long gap between `tool_call` and `tool_result` (during which
    # we're synthesising 10-15 OpenAI TTS phrases sequentially) looked
    # like an idle connection to the ALB / browser, which dropped it,
    # which raised asyncio.CancelledError in _execute_tool, which
    # surfaced as `{"error": ""}` in the tool_result.  Keeping the
    # socket warm prevents the cancellation entirely.
    return EventSourceResponse(_stream_vllm_chat(req, user), ping=15)


async def _stream_vllm_chat(req: ChatReq, user: str):
    """Stream the tool-calling loop against a local vLLM server.

    Mirrors the SSE event vocabulary the Anthropic streamer emits
    (``text`` / ``tool_call`` / ``tool_result`` / ``done`` / ``error``)
    so the studio frontend works unchanged.

    vLLM serves the OpenAI Chat Completions API, so all wire formats
    (tools, assistant tool_calls, tool messages, finish_reason, SSE
    framing) follow OpenAI's spec.  Requires vLLM to be started with
    ``--enable-auto-tool-choice --tool-call-parser hermes`` for
    Qwen2.5-Instruct.
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Compact prior turns: keep just role+text, cap each turn's content
    # at ~600 chars, and only retain the most recent 6 turns.  Without
    # this, refinement turns N+ accumulate the full assistant chat-side
    # text from every prior round and inflate the LLM's input until
    # response latency degrades.
    history_window = 6
    trimmed = list(req.history)[-history_window:]
    for turn in trimmed:
        text = (turn.content or "")
        if len(text) > 600:
            text = text[:580] + " …[truncated]"
        messages.append({"role": turn.role, "content": text})
    messages.append({"role": "user", "content": req.user})

    base = _vllm_url()
    model = _vllm_model()
    tools = _tools_openai_format()

    # Track the most-recent canvas the LLM has touched so the post-stop
    # audit knows which canvas to inspect.
    latest_canvas_id: str | None = None
    import sys as _sys
    def _slog(msg: str) -> None:
        print(f"[chat-loop] {msg}", flush=True, file=_sys.stderr)

    _slog(f"start canvas_id={req.canvas_id} prior={req.prior_canvas_ids} "
          f"history_len={len(req.history)} user={req.user[:50]!r}")

    for _step in range(20):  # bumped from 8 to fit up to 3 audit retries
        _slog(f"step={_step} sending outer LLM call (messages={len(messages)})")
        # Force the model to call sevim_express on every turn.  Studio's
        # job is to build figures; if we let tool_choice='auto' the
        # model can decide to respond in chat-side text instead, leaving
        # the iframe empty and the chat panel hung waiting for a tool
        # result that never comes.  Preference requests are pre-routed
        # before this loop, so by the time we get here the user wants a
        # figure.
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": {
                "type": "function",
                "function": {"name": "sevim_express"},
            },
            "parallel_tool_calls": False,
            "stream": True,
            "max_tokens": 4096,
            "temperature": 0.3,
        }

        # Per-step accumulators.  vLLM streams text in `delta.content`
        # and tool calls in `delta.tool_calls[*]`; both arrive in many
        # tiny pieces and we have to reassemble before executing.
        text_buf = ""
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        # Bearer auth when targeting OpenAI (or any OpenAI-compatible
        # service that requires it).  Local vLLM doesn't need auth, so
        # the header is omitted unless OPENAI_API_KEY is set.
        headers = {"content-type": "application/json"}
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # HARD TOTAL CAP on this round-trip.  Even if OpenAI keeps the
        # connection alive with pings (which makes per-event read
        # timeouts useless), an inline elapsed-time check on each event
        # ensures we abort within the deadline.  The forced
        # tool_choice="sevim_express" already keeps responses small, so
        # 120s is generous.
        import time as _time
        _OUTER_TOTAL_TIMEOUT_S = 120.0
        _stream_start = _time.monotonic()
        try:
            _timeout = httpx.Timeout(connect=15.0, read=90.0,
                                     write=15.0, pool=15.0)
            async with httpx.AsyncClient(timeout=_timeout) as client:
                async with client.stream(
                    "POST",
                    f"{base}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    _slog(f"step={_step} outer LLM responded status={resp.status_code}")
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="replace")
                        _slog(f"step={_step} HTTP {resp.status_code} body: {body[:200]}")
                        yield {"event": "error",
                               "data": json.dumps({"detail": body[:600]})}
                        return
                    _line_count = 0
                    async for raw in resp.aiter_lines():
                        _line_count += 1
                        if _time.monotonic() - _stream_start > _OUTER_TOTAL_TIMEOUT_S:
                            _slog(f"step={_step} HARD TIMEOUT after {_OUTER_TOTAL_TIMEOUT_S}s lines={_line_count} — aborting")
                            raise TimeoutError(f"outer LLM stream exceeded {_OUTER_TOTAL_TIMEOUT_S}s")
                        # Log every 20th line to confirm stream is alive.
                        if _line_count % 20 == 1:
                            _slog(f"step={_step} streaming line {_line_count}: {raw[:80]!r}")
                        if not raw or not raw.startswith("data:"):
                            continue
                        data_str = raw[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            ev = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        choices = ev.get("choices") or []
                        if not choices:
                            continue
                        ch = choices[0]
                        delta = ch.get("delta") or {}
                        if delta.get("content"):
                            chunk = delta["content"]
                            text_buf += chunk
                            yield {"event": "text",
                                   "data": json.dumps({"text": chunk})}
                        for tc in (delta.get("tool_calls") or []):
                            idx = tc.get("index", 0)
                            slot = tool_calls.setdefault(idx, {
                                "id": None, "name": None, "arguments": "",
                            })
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                                yield {"event": "tool_call",
                                       "data": json.dumps({
                                           "name": fn["name"], "args_keys": [],
                                       })}
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
                        if ch.get("finish_reason"):
                            finish_reason = ch["finish_reason"]
                            _slog(f"step={_step} finish_reason={finish_reason!r} "
                                  f"text_buf_len={len(text_buf)} "
                                  f"tool_calls={list(tool_calls.keys())}")
            _slog(f"step={_step} stream consumed: lines={_line_count} "
                  f"finish_reason={finish_reason!r} tool_calls={[tc.get('name') for tc in tool_calls.values()]}")
        except Exception as exc:  # noqa: BLE001
            _slog(f"step={_step} OUTER LLM ERROR: {type(exc).__name__}: {exc}")
            yield {"event": "error",
                   "data": json.dumps({"detail": f"outer LLM stream failed: {type(exc).__name__}: {exc}"})}
            return

        # Assemble the assistant message exactly the way OpenAI expects
        # it on the next turn.  ``content`` MUST be present (can be
        # empty) when ``tool_calls`` is set; setting None upsets some
        # OpenAI-compatible servers.
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": text_buf}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.get("id") or f"call_{idx}",
                    "type": "function",
                    "function": {
                        "name": tc.get("name") or "",
                        "arguments": tc.get("arguments") or "{}",
                    },
                }
                for idx, tc in sorted(tool_calls.items())
            ]
        messages.append(assistant_msg)

        # If the model produced any tool calls, execute them — REGARDLESS
        # of finish_reason.  When tool_choice forces a specific function,
        # OpenAI returns finish_reason='stop' even though the tool_call
        # IS present in the deltas.  Only the absence of tool_calls (or
        # an explicit 'length' truncation) means there's nothing to run.
        if not tool_calls:
            if finish_reason == "length":
                yield {"event": "text", "data": json.dumps({"text": (
                    "\n\n[Studio: gpt-4o hit max_tokens before finishing.]"
                )})}
            yield {"event": "done",
                   "data": json.dumps({"stop_reason": finish_reason})}
            return

        # Execute each tool and append the OpenAI ``tool`` messages.
        for idx, tc in sorted(tool_calls.items()):
            try:
                args = json.loads(tc.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            # Auto-inject conversational context IDs into sevim_express:
            # the LLM rarely knows the canvas_id of the figure currently
            # in the iframe, so we plumb it through from the frontend.
            # User-pinned canvases (req.prior_canvas_ids) get prepended
            # before the latest canvas so the order matches "explicitly
            # pinned" → "implicitly current".
            if (tc.get("name") or "") == "sevim_express":
                supplied = list(args.get("context_canvas_ids") or [])
                ctx_ids: list[str] = []
                # User-PINNED canvases: always attached (explicit
                # user signal that they want continuity).
                for pid in (req.prior_canvas_ids or []):
                    if pid and pid not in ctx_ids:
                        ctx_ids.append(pid)
                # Current canvas: attach ONLY when the prompt looks
                # like a follow-up on the current figure. A clear
                # new-topic prompt ("show me X", "draw Y") generates
                # a fresh figure without prior-SVG copy-paste — the
                # LLM otherwise blends old and new SVG elements
                # because REFINEMENT MODE tells it to "preserve
                # every unchanged element byte-for-byte".
                if (req.canvas_id
                        and req.canvas_id not in ctx_ids
                        and _looks_like_followup(req.user)):
                    ctx_ids.append(req.canvas_id)
                for sid in supplied:
                    if sid and sid not in ctx_ids:
                        ctx_ids.append(sid)
                if ctx_ids:
                    args["context_canvas_ids"] = ctx_ids
            tool_failed = False
            try:
                # Progressive-SVG plumbing: only the express tool emits
                # partial SVG.  Every time the streaming JSON parser
                # makes progress on the 'svg' field, the callback pushes
                # a snapshot onto this queue, which we drain into SSE
                # frames while the tool task is running.  Last-write-
                # wins inside the queue (collapse to keep the wire
                # cheap), but we still yield as often as the network
                # lets us.
                tool_name = tc.get("name") or ""
                svg_queue: asyncio.Queue[str] | None = None
                primer_queue: asyncio.Queue[str] | None = None
                on_svg_chunk: (
                    Callable[[str], Awaitable[None]] | None
                ) = None
                on_primer_chunk: (
                    Callable[[str], Awaitable[None]] | None
                ) = None
                if tool_name == "sevim_express":
                    svg_queue = asyncio.Queue()
                    primer_queue = asyncio.Queue()

                    async def _push_svg_chunk(
                        partial: str,
                        _q: asyncio.Queue[str] = svg_queue,
                    ) -> None:
                        await _q.put(partial)

                    async def _push_primer_chunk(
                        delta: str,
                        _q: asyncio.Queue[str] = primer_queue,
                    ) -> None:
                        await _q.put(delta)

                    on_svg_chunk = _push_svg_chunk
                    on_primer_chunk = _push_primer_chunk

                tool_task = asyncio.create_task(_execute_tool(
                    tool_name, args,
                    session_id=req.session_id,
                    on_svg_chunk=on_svg_chunk,
                    on_primer_chunk=on_primer_chunk,
                    owner=user,
                    flagged=req.flagged,
                ))

                if svg_queue is not None and primer_queue is not None:
                    # Two parallel streams to drain into SSE frames:
                    #   * primer_chunk — raw deltas from the theory
                    #     primer (gpt-4o-mini), forwarded in their
                    #     natural order so SpeechSynthesis in the
                    #     browser can speak them as they arrive.
                    #   * svg_chunk — cumulative snapshots of the
                    #     in-progress SVG; collapsed last-write-wins
                    #     to keep the wire cheap.
                    while not tool_task.done():
                        flushed = False
                        # Primer drain first (small text, low latency).
                        while not primer_queue.empty():
                            delta = primer_queue.get_nowait()
                            yield {
                                "event": "primer_chunk",
                                "data": json.dumps({"text": delta}),
                            }
                            flushed = True
                        # SVG drain second; collapse to latest.
                        if not svg_queue.empty():
                            partial = svg_queue.get_nowait()
                            while not svg_queue.empty():
                                partial = svg_queue.get_nowait()
                            yield {
                                "event": "svg_chunk",
                                "data": json.dumps({"svg": partial}),
                            }
                            flushed = True
                        if not flushed:
                            # Nothing pending — sleep briefly so the
                            # tool task can make progress.
                            try:
                                await asyncio.wait_for(
                                    asyncio.shield(asyncio.sleep(0.025)),
                                    timeout=0.025,
                                )
                            except asyncio.TimeoutError:
                                pass
                    # Drain whatever landed after .done() flipped.
                    while not primer_queue.empty():
                        delta = primer_queue.get_nowait()
                        yield {
                            "event": "primer_chunk",
                            "data": json.dumps({"text": delta}),
                        }
                    if not svg_queue.empty():
                        partial = svg_queue.get_nowait()
                        while not svg_queue.empty():
                            partial = svg_queue.get_nowait()
                        yield {
                            "event": "svg_chunk",
                            "data": json.dumps({"svg": partial}),
                        }
                    # Signal end-of-primer so the browser can stop
                    # speaking and release the speech synthesiser.
                    yield {
                        "event": "primer_done",
                        "data": json.dumps({}),
                    }

                out = await tool_task
                # Track the most-recent canvas_id touched by any tool —
                # this is what the post-stop audit will inspect.
                if isinstance(out, dict) and out.get("canvas_id"):
                    latest_canvas_id = out["canvas_id"]
                body = json.dumps(out)
            except Exception as exc:  # noqa: BLE001
                # Surface enough context to debug.  CancelledError /
                # bare TimeoutError have empty str(); use repr() and
                # a traceback so CloudWatch shows the actual problem.
                import traceback
                err_repr = repr(exc) or type(exc).__name__
                traceback.print_exc(file=_sys.stderr)
                print(f"[chat-loop] tool {tc.get('name')!r} FAILED: {err_repr}",
                      flush=True, file=_sys.stderr)
                body = json.dumps({"error": err_repr})
                tool_failed = True
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or f"call_{idx}",
                "content": body,
            })
            yield {"event": "tool_result", "data": body}
            # Server-side exclusivity: sevim_express does
            # open + apply + narrate atomically.  If we let the loop
            # continue, the model habitually goes on to call the
            # structured tools, and the iframe ends up swapping to
            # whatever canvas the LAST tool created — overwriting the
            # good express figure with a junk structured one.  Force a
            # clean stop here.
            if (tc.get("name") or "") == "sevim_express":
                if tool_failed:
                    yield {"event": "text", "data": json.dumps({
                        "text": (
                            "Sorry — couldn't generate that figure. "
                            "Please try again, or rephrase the request."
                        )
                    })}
                    yield {"event": "done", "data": json.dumps({
                        "stop_reason": "express_failed"
                    })}
                else:
                    # No "Done — see the canvas above." status line:
                    # by this point the primer panel has already
                    # delivered the explanation in the chat AND the
                    # canvas is rendered, so a third "done" bubble is
                    # just visual noise (worse on mobile where the
                    # chat is already tight).  Front-end's 'done'
                    # handler still attaches the 📌 pin button to
                    # the primer / assistant node so the canvas can
                    # be carried forward to the next prompt.
                    yield {"event": "done", "data": json.dumps({
                        "stop_reason": "express_complete"
                    })}
                return

    yield {"event": "done", "data": json.dumps({"stop_reason": "max_steps"})}


@router.post("/canvas/new")
def new_canvas(user: str = Depends(require_user)) -> dict[str, str]:
    """Spawn a fresh canvas owned by the signed-in user."""
    # 16 hex bytes (128 bits) — unguessable.  Combined with the
    # per-canvas owner check on /canvas/<id>/*, a canvas cannot be
    # reached by guessing ids or by another signed-in user.
    cid = "studio_" + secrets.token_hex(16)
    c = REGISTRY.open(canvas_id=cid, math_mode=True, animate=True,
                      width=820, height=520, owner=user)
    return {"canvas_id": c.canvas_id, "view_url": f"/canvas/{c.canvas_id}/view"}


# ---------------------------------------------------------------------------
# Magic-link auth routes.  Active when SEVIM_AUTH_REQUIRED=1; harmless
# (just unused endpoints) when it isn't.  Login UI is intentionally
# minimal — a single email field + submit button, no JS, no theming.
# ---------------------------------------------------------------------------

_LOGIN_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#fafafa" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1a1a1a" media="(prefers-color-scheme: dark)">
<title>Sign in to Khayyam Math</title>
<style>
 :root {
   color-scheme: light dark;
   --bg: #fafafa; --fg: #222; --muted: #666; --accent: #1f6fe0;
   --border: #ddd; --field-bg: #fff;
 }
 @media (prefers-color-scheme: dark) {
   :root { --bg: #1a1a1a; --fg: #eee; --muted: #aaa;
           --border: #3a3a3a; --field-bg: #232323; }
 }
 *, *::before, *::after { box-sizing: border-box; }
 html, body {
   margin: 0; padding: 0; min-height: 100%;
   background: var(--bg); color: var(--fg);
   font: 16px/1.5 -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
 }
 body {
   /* Center the card vertically — empty space at the bottom of a
      mostly-empty page felt unfinished. */
   min-height: 100dvh;
   display: flex; align-items: center; justify-content: center;
   padding: max(1.5em, env(safe-area-inset-top))
            max(1em, env(safe-area-inset-right))
            max(1.5em, env(safe-area-inset-bottom))
            max(1em, env(safe-area-inset-left));
 }
 .card { width: 100%; max-width: 26em; }
 .brand { font-size: 1.8em; font-weight: 700; letter-spacing: -0.02em;
          margin: 0 0 0.15em; }
 .tagline { color: var(--muted); font-size: 0.95em; margin: 0 0 1.5em;
            line-height: 1.45; }
 form { display: flex; flex-direction: column; gap: 0.8em; }
 input[type=email] {
   padding: 0.75em 0.9em; width: 100%;
   border: 1px solid var(--border); border-radius: 8px;
   background: var(--field-bg); color: var(--fg);
   font-size: 16px;  /* prevent iOS auto-zoom on focus */
   min-height: 48px;
 }
 input[type=email]:focus {
   outline: none;
   border-color: var(--accent);
   box-shadow: 0 0 0 3px rgba(31,111,224,0.18);
 }
 button {
   padding: 0 1.4em; min-height: 48px;
   font-size: 1em; font-weight: 500;
   border: 0; border-radius: 8px; background: var(--accent); color: #fff;
   cursor: pointer; transition: filter 120ms ease;
 }
 button:hover { filter: brightness(1.05); }
 .ok { color: #2a7a3a; margin-top: 1em; font-size: 0.92em; }
 @media (prefers-color-scheme: dark) { .ok { color: #4ade80; } }
 .privacy { color: var(--muted); font-size: 0.78em;
            margin-top: 1.6em; line-height: 1.5; }
</style></head><body>
<div class="card">
  <h1 class="brand">Khayyam Math</h1>
  <p class="tagline">A live diagram tutor — sign in with email, no password.</p>
  <form method="POST" action="/studio/auth/request-link">
    <input type="email" name="email" required autofocus
           placeholder="you@example.com" autocomplete="email"
           autocapitalize="off" spellcheck="false" inputmode="email">
    <button type="submit">Email me a sign-in link</button>
  </form>
  __NOTICE__
  <p class="privacy">Your email is used only as a stable identifier so we
  can track usage against the daily quota. We don't share it.</p>
  <p class="privacy" style="margin-top:0.6em">
    <a href="/terms">Terms</a> · <a href="/contact">Contact</a>
  </p>
</div>
<!-- Cookie banner removed: we set only ONE strictly-necessary cookie (sevim_auth) — no consent required under UAE PDPL or EU GDPR.  See /terms for the disclosure. -->
</body></html>
"""


@router.get("/auth/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return HTMLResponse(_LOGIN_HTML.replace("__NOTICE__", ""))


@router.post("/auth/request-link", response_class=HTMLResponse)
def auth_request_link(request: Request, email: str = Form(...)) -> HTMLResponse:
    request_magic_link(email, request)
    notice = (
        '<p class="ok">If that address is valid, a sign-in link is on '
        "its way. Check your inbox (and spam folder).</p>"
    )
    return HTMLResponse(_LOGIN_HTML.replace("__NOTICE__", notice))


@router.get("/auth/verify")
def auth_verify(t: str, request: Request) -> Response:
    redirect = RedirectResponse(url="/studio", status_code=302)
    email = verify_link_and_set_cookie(t, redirect)
    if email is None:
        return RedirectResponse(url="/studio/auth/login", status_code=302)
    return redirect


@router.post("/auth/logout")
def auth_logout() -> Response:
    response = RedirectResponse(url="/studio/auth/login", status_code=302)
    clear_cookie(response)
    return response


@router.get("/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    """Front-end uses this to decide whether to show the login link."""
    return {
        "auth_required": auth_is_required(),
        "user": current_user(request),
    }


@router.get("/preferences")
def preferences() -> dict[str, Any]:
    """Current backend-side UI preferences (highlight color, audio speed,
    volume, autoplay).  The canvas viewer polls this and applies the
    values to its DOM/audio elements."""
    from studio.preferences import get_settings
    return get_settings()


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, _user: str = Depends(require_admin)):
    """Operator-only dashboard.  Returns 404 to anyone whose signed
    cookie e-mail isn't in ``SEVIM_ADMIN_EMAILS``, so the URL is
    effectively undiscoverable.

    The page itself is a small SPA that polls ``/studio/admin/stats``
    and ``/studio/admin/models`` and posts to
    ``/studio/admin/active-model``.  All authentication is the same
    magic-link cookie the regular chat surface uses.
    """
    html_path = _STATIC / "admin.html"
    if not html_path.exists():
        raise HTTPException(500, "admin.html missing")
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/stats")
def admin_stats(_user: str = Depends(require_admin)) -> dict[str, Any]:
    """Per-model usage roll-ups for the operator dashboard."""
    from sevim.telemetry import get_telemetry
    tel = get_telemetry()
    if tel is None:
        return {"available": False, "reason": "telemetry disabled"}
    return {
        "available": True,
        "active_model": get_active_model(),
        "windows": {
            "24h":  tel.usage_by_model(since_s=86_400),
            "7d":   tel.usage_by_model(since_s=7 * 86_400),
            "30d":  tel.usage_by_model(since_s=30 * 86_400),
            "all":  tel.usage_by_model(since_s=10**12),
        },
    }


@router.get("/admin/models")
def admin_models(_user: str = Depends(require_admin)) -> dict[str, Any]:
    """List the selectable LLM backends — same catalog the routing
    layer consumes, but only exposed to admins."""
    return {"models": model_catalog(), "active": get_active_model()}


class SetActiveModelReq(BaseModel):
    model_id: str


@router.post("/admin/active-model")
def admin_set_active_model(
    req: SetActiveModelReq,
    user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Set the production-traffic model.  Persists to the telemetry
    ``settings`` table; takes effect on the *next* chat request.
    """
    catalog = {m["id"]: m for m in model_catalog()}
    if req.model_id not in catalog:
        raise HTTPException(400, f"unknown model {req.model_id!r}")
    if not catalog[req.model_id]["available"]:
        raise HTTPException(409, f"backend {req.model_id!r} is not configured: "
                                 f"{catalog[req.model_id]['reason']}")
    from sevim.telemetry import get_telemetry
    tel = get_telemetry()
    if tel is None:
        raise HTTPException(503, "telemetry unavailable; cannot persist setting")
    tel.set_setting("active_model", req.model_id, updated_by=user)
    return {"ok": True, "active_model": req.model_id, "updated_by": user}


@router.get("/admin/problems", include_in_schema=False)
def admin_problems(_user: str = Depends(require_admin)) -> dict[str, Any]:
    """Recurring user-facing problems, mined from telemetry — so the
    operator sees collective issues without users emailing them.

    Three buckets over the last 30 days: turns the user explicitly
    flagged ("Something's wrong"), turns that errored, and turns that
    needed multiple repair retries.
    """
    from sevim.telemetry import get_telemetry
    tel = get_telemetry()
    if tel is None:
        return {"available": False, "reason": "telemetry disabled"}
    import time as _t
    since = _t.time() - 30 * 86400.0

    def _q(sql: str) -> list:
        try:
            return tel.query(sql)
        except Exception:  # noqa: BLE001
            return []

    def _rows(rows, keys):
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            if "prompt" in d and d["prompt"]:
                d["prompt"] = str(d["prompt"])[:240]
            if "error" in d and d["error"]:
                d["error"] = str(d["error"])[:200]
            out.append(d)
        return out

    flagged = _q(
        "SELECT user_prompt, timestamp, canvas_id FROM turns "
        f"WHERE intent = 'flagged' AND timestamp > {since} "
        "ORDER BY timestamp DESC LIMIT 40")
    errored = _q(
        "SELECT user_prompt, error, timestamp FROM turns "
        f"WHERE error IS NOT NULL AND error <> '' AND timestamp > {since} "
        "ORDER BY timestamp DESC LIMIT 40")
    high_retry = _q(
        "SELECT user_prompt, retries_used, timestamp FROM turns "
        f"WHERE retries_used >= 2 AND timestamp > {since} "
        "ORDER BY timestamp DESC LIMIT 40")
    return {
        "available": True,
        "window_days": 30,
        "flagged": _rows(flagged, ("prompt", "ts", "canvas_id")),
        "errored": _rows(errored, ("prompt", "error", "ts")),
        "high_retry": _rows(high_retry, ("prompt", "retries", "ts")),
    }


@router.get("/admin/lean", include_in_schema=False)
def admin_lean(_user: str = Depends(require_admin)) -> dict[str, Any]:
    """Lean+Mathlib offline catalog verifier dashboard.

    Reports counts by status across the last 30 days, plus the most
    recent FAILED rows for triage.  Failures do NOT pull figures from
    production — they're surfaced here for the operator to review.
    """
    from sevim.telemetry import get_telemetry
    tel = get_telemetry()
    if tel is None:
        return {"available": False, "reason": "telemetry disabled"}

    def _q(sql: str, args: tuple = ()) -> list:
        try:
            return tel.query(sql, args)
        except Exception:  # noqa: BLE001
            return []

    counts: dict[str, int] = {}
    for row in _q(
            "SELECT status, COUNT(*) FROM lean_verifications "
            "GROUP BY status"):
        counts[row[0]] = int(row[1])

    failed_rows = _q(
        "SELECT v.canvas_id, v.claim_idx, v.claim_a, v.claim_b, "
        "       v.claim_kind, v.lean_output, v.verified_at, c.title "
        "FROM lean_verifications v "
        "LEFT JOIN canvases c ON c.canvas_id = v.canvas_id "
        "WHERE v.status IN ('failed','timeout') "
        "ORDER BY v.verified_at DESC LIMIT 40")
    recent_verified = _q(
        "SELECT canvas_id, claim_idx, claim_a, claim_b, verified_at "
        "FROM lean_verifications "
        "WHERE status = 'verified' "
        "ORDER BY verified_at DESC LIMIT 20")

    failed = [
        {"canvas_id": r[0], "claim_idx": r[1],
         "a": (r[2] or "")[:120], "b": (r[3] or "")[:120],
         "kind": r[4], "lean_output": (r[5] or "")[:400],
         "verified_at": r[6], "title": (r[7] or "")[:120]}
        for r in failed_rows
    ]
    verified = [
        {"canvas_id": r[0], "claim_idx": r[1],
         "a": (r[2] or "")[:120], "b": (r[3] or "")[:120],
         "verified_at": r[4]}
        for r in recent_verified
    ]
    return {
        "available": True,
        "counts": counts,
        "failed_recent": failed,
        "verified_recent": verified,
    }


@router.post("/admin/diagnose", include_in_schema=False)
async def admin_diagnose(
    _user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Have the LLM read the recent problem reports and draft a
    diagnosis + suggested fixes for the operator to review.

    It proposes TEXT only — a written analysis.  It never changes
    code or configuration; the operator decides what to act on.
    """
    payload = admin_problems(_user=_user)
    if not payload.get("available"):
        return {"available": False, "reason": "telemetry disabled"}
    lines: list[str] = []
    for p in payload.get("flagged", [])[:25]:
        lines.append(f"[user-flagged] {p.get('prompt', '')}")
    for p in payload.get("errored", [])[:25]:
        lines.append(f"[errored: {p.get('error', '')}] {p.get('prompt', '')}")
    for p in payload.get("high_retry", [])[:15]:
        lines.append(f"[needed {p.get('retries', '?')} retries] "
                     f"{p.get('prompt', '')}")
    if not lines:
        return {"available": True,
                "diagnosis": "No problem reports in the last 30 days."}
    sys_prompt = (
        "You are the engineering lead of Khayyam Math, a math-figure "
        "tutoring app (it turns prompts into SVG/3-D figures with voice "
        "narration). Below are recent problem reports — figures users "
        "flagged as wrong, turns that errored, and turns that needed "
        "many repair retries. Group them into a few RECURRING patterns. "
        "For each pattern give: (1) what is going wrong, (2) the likely "
        "cause, (3) a concrete, specific suggested fix. Be brief and "
        "practical. Plain text, use short headings. Do not invent "
        "problems not supported by the reports.")
    body = "\n".join(lines)[:6000]
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {"content-type": "application/json"}
            api_key = os.environ.get("OPENAI_API_KEY")
            url = _vllm_url()
            if api_key and "openai.com" in url:
                headers["Authorization"] = f"Bearer {api_key}"
            r = await client.post(
                f"{url.rstrip('/')}/chat/completions",
                json={"model": _vllm_model(), "max_tokens": 1100,
                      "temperature": 0.3,
                      "messages": [
                          {"role": "system", "content": sys_prompt},
                          {"role": "user", "content": body}]},
                headers=headers)
        if r.status_code != 200:
            return {"available": True,
                    "diagnosis": "Could not generate a diagnosis right "
                                 "now — the model service did not respond."}
        text = r.json()["choices"][0]["message"]["content"] or ""
    except Exception:  # noqa: BLE001
        return {"available": True,
                "diagnosis": "Could not generate a diagnosis right now."}
    return {"available": True, "diagnosis": text,
            "report_count": len(lines)}


@router.get("/health", include_in_schema=False)
def health() -> dict[str, Any]:
    """Coarse liveness for the Studio status pill.

    Returns ONLY ``{"status": "ok"|"unavailable"}`` — never the
    backend, the model name, or the upstream URL.  Those used to be in
    the payload and were visible to anyone reading the page's network
    traffic; they are internal implementation details.
    """
    backend = _backend()
    ok = True
    if backend == "vllm":
        url = _vllm_url()
        try:
            headers = {}
            api_key = os.environ.get("OPENAI_API_KEY")
            if api_key and "openai.com" in url:
                headers["Authorization"] = f"Bearer {api_key}"
            # 6s budget: a 2s probe to a remote provider from Fargate
            # times out intermittently even when the service is fine.
            # Any HTTP response — even 401/429 — proves the network
            # path works; only a connection/timeout error means down.
            with httpx.Client(timeout=6.0) as c:
                c.get(f"{url}/models", headers=headers)
        except Exception:  # noqa: BLE001
            ok = False
    else:
        ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {"status": "ok" if ok else "unavailable"}




_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$", re.MULTILINE)


def _strip_client_comments(html: str) -> str:
    """Drop HTML/CSS/JS comments before serving a page.

    The source keeps its comments (they document non-obvious UI
    decisions) but the bytes the browser receives carry no
    architectural narration for someone reading "view source".
    Conservative: only ``<!-- -->`` blocks, ``/* */`` blocks, and
    full-line ``//`` comments — an inline trailing ``//`` is left
    untouched so a URL or regex literal is never corrupted.
    """
    html = _HTML_COMMENT_RE.sub("", html)
    html = _BLOCK_COMMENT_RE.sub("", html)
    html = _LINE_COMMENT_RE.sub("", html)
    return html


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def studio_index(request: Request):
    # Logged-out user landing here would otherwise see the SPA load,
    # type a prompt, and get a confusing 401 from /studio/chat.  Bounce
    # them straight to the login page instead.
    if auth_is_required() and current_user(request) is None:
        return RedirectResponse(url="/studio/auth/login", status_code=302)
    html_path = _STATIC / "studio.html"
    if not html_path.exists():
        raise HTTPException(500, "studio.html missing")
    # The studio.html file changes frequently as we iterate.  Firefox's
    # default heuristic caches HTML aggressively even on the same TCP
    # session, which left the user staring at stale UI long after the
    # server had new code.  Force a fresh fetch on every page load.
    return HTMLResponse(
        _strip_client_comments(html_path.read_text(encoding="utf-8")),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


