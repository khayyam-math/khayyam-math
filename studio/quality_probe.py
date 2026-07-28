#!/usr/bin/env python3
"""6-hourly figure-quality probe (runs as a scheduled ECS task).

Invoked as ``python -m studio.quality_probe`` so it ships inside the
``studio`` package that the production image already carries (the image
copies whole packages, not the repo's ``scripts/`` dir).

Feeds one challenging problem through the SAME production code path
(``express_figure``), inspects the result, and e-mails the operator only
when something is wrong.  Designed to run unattended from EventBridge
Scheduler every 6 hours through the end of August 2026.

Two independent stops bound it to that window:
  1. the EventBridge schedule carries an ``EndDate`` of 2026-08-31, and
  2. this script no-ops (and sends nothing) once the date is past, so even
     a stray invocation after the window does nothing.

It alerts on: an empty / malformed figure, text outside the viewBox, an
oversized element, leaked internals, a generation error, or retries
exhausted with the figure still failing review --- and on the probe
itself crashing.  A clean run sends no e-mail (so the inbox only ever
carries real problems).
"""
from __future__ import annotations

import asyncio
import datetime
import os
import re
import sys
import traceback

# Hard window: do nothing once this date is past.
END_DATE = datetime.date(2026, 8, 31)
# Alert recipient comes ONLY from the environment — never hard-coded, so
# the public repo carries no personal address.  In production it is set on
# the probe's ECS task (from the operator's .env at deploy time).  Empty =>
# no e-mail is sent (the run still logs to stdout / CloudWatch).
ALERT_EMAIL = os.environ.get("SEVIM_PROBE_ALERT_EMAIL", "").strip()

# Auto-remediation.  OFF by default (so a clone never tries to "fix" things
# unattended).  When ON (set in the operator's .env), a detected problem is
# first re-attempted IN PLACE — re-run the safe layout passes, then
# regenerate the figure — and the operator is e-mailed ONLY if that fails,
# i.e. only for PERSISTENT issues.  This NEVER edits code or redeploys; the
# probe runs in a task with no git-write or deploy credentials, and the
# live system is never autonomously self-modified.  It repairs the figure
# OUTPUT, not the system.
AUTOFIX = os.environ.get("SEVIM_PROBE_AUTOFIX", "").strip().lower() in (
    "1", "true", "on", "yes")

# A rotating pool of deliberately demanding prompts spanning the routes
# and the open-ended long tail.
PROBLEMS = [
    "prove that vertex cover is NP-complete",
    "prove that the clique problem is NP-complete",
    "explain the spectral theorem with an example",
    "show the singular value decomposition of a 2x2 matrix",
    "explain eigenvalues and eigenvectors geometrically",
    "illustrate Dijkstra's algorithm on a small weighted graph",
    "explain Bayes theorem with a tree diagram",
    "show how gradient descent minimizes a function",
    "prove that there are infinitely many primes",
    "explain the central limit theorem with a sketch",
    "draw a DFA for binary strings ending in 01",
    "show the proof of the fundamental theorem of calculus",
    "explain conditional probability with a Venn diagram",
    "multiply a 2x3 matrix by a 3x2 matrix with a worked example",
    "show the unit circle with sin and cos at 30, 45, 60 degrees",
]


def _today() -> datetime.date:
    return datetime.datetime.utcnow().date()


def _which_problem() -> str:
    """Rotate deterministically by day-of-year + 6-hour slot so each run
    of the day picks a different prompt and the pool cycles over time."""
    now = datetime.datetime.utcnow()
    slot = now.timetuple().tm_yday * 4 + (now.hour // 6)
    return PROBLEMS[slot % len(PROBLEMS)]


def _sender() -> str:
    from service.mailer import sender_address
    return (sender_address(display_name="Khayyam Math probe")
            or "Khayyam Math probe <noreply@khayyammath.com>")


def send_alert(subject: str, body: str) -> None:
    if not ALERT_EMAIL:
        # No recipient configured (e.g. a clone without the env var):
        # surface the alert in the logs instead of crashing on an empty
        # destination.
        print(f"[probe] no SEVIM_PROBE_ALERT_EMAIL set; alert not e-mailed.\n"
              f"{subject}\n{body}", flush=True)
        return
    from service.mailer import send_email
    ok = send_email(
        to=ALERT_EMAIL,
        subject=subject[:200],
        text=body[:60000],
        sender=_sender(),
    )
    if ok:
        print(f"[probe] alert e-mailed to {ALERT_EMAIL}", flush=True)
    else:
        # Last resort: at least surface it in the task logs.
        print(f"[probe] FAILED to send alert\n{subject}\n{body}",
              flush=True, file=sys.stderr)


_IDENTITY = (1.0, 1.0, 0.0, 0.0)  # (sx, sy, tx, ty)


def _compose(outer: tuple, inner: tuple) -> tuple:
    """Compose two translate+scale transforms so the result maps a child
    point through ``inner`` then ``outer``:  p -> outer(inner(p))."""
    so_x, so_y, to_x, to_y = outer
    si_x, si_y, ti_x, ti_y = inner
    return (so_x * si_x, so_y * si_y, so_x * ti_x + to_x, so_y * ti_y + to_y)


def _parse_transform(s: str):
    """Reduce an SVG ``transform`` attribute to a (sx, sy, tx, ty) tuple.

    Returns ``None`` if it carries a real rotation / skew / matrix we
    can't fold into pure translate+scale --- the caller then treats the
    bounds check as inconclusive rather than risk a false positive.
    Graphviz (and our deterministic routes) only ever emit
    ``scale(...) rotate(0) translate(...)``, which folds cleanly."""
    acc = _IDENTITY
    for name, body in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        nums = [float(n) for n in re.findall(r"[-+0-9.eE]+", body)]
        if name == "translate":
            op = (1.0, 1.0, nums[0] if nums else 0.0,
                  nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale":
            sx = nums[0] if nums else 1.0
            op = (sx, nums[1] if len(nums) > 1 else sx, 0.0, 0.0)
        elif name == "rotate":
            if nums and abs(nums[0]) > 1e-6:
                return None  # genuine rotation — can't reduce
            op = _IDENTITY
        else:  # matrix, skewX, skewY, …
            return None
        acc = _compose(acc, op)
    return acc


def _texts_outside_viewbox(svg: str, vx, vy, vw, vh, pad=6.0):
    """Count <text> anchors falling outside the viewBox AFTER applying the
    cumulative ancestor transform.  Returns ``None`` when the result is
    inconclusive (unparseable, or an unresolved rotation/matrix), so the
    caller can decline to alert rather than fire a false positive on the
    transform-wrapped output that graphviz and friends emit."""
    from xml.dom import minidom
    try:
        doc = minidom.parseString(svg)
    except Exception:  # noqa: BLE001
        return None
    outside = 0
    aborted = False

    def walk(node, ctm):
        nonlocal outside, aborted
        if aborted or node.nodeType != node.ELEMENT_NODE:
            return
        tf = node.getAttribute("transform")
        if tf:
            local = _parse_transform(tf)
            if local is None:
                aborted = True
                return
            ctm = _compose(ctm, local)
        if node.tagName.split(":")[-1] == "text":
            sx = node.getAttribute("x").split()[:1]
            sy = node.getAttribute("y").split()[:1]
            if sx and sy:
                try:
                    x, y = float(sx[0]), float(sy[0])
                except ValueError:
                    x = y = None
                if x is not None:
                    X, Y = ctm[0] * x + ctm[2], ctm[1] * y + ctm[3]
                    if (X < vx - pad or X > vx + vw + pad
                            or Y < vy - pad or Y > vy + vh + pad):
                        outside += 1
        for c in node.childNodes:
            walk(c, ctm)

    walk(doc.documentElement, _IDENTITY)
    return None if aborted else outside


def _node_text(node) -> str:
    """Concatenate the text of a <text> node and any <tspan> children."""
    out = []
    for c in node.childNodes:
        if c.nodeType == c.TEXT_NODE:
            out.append(c.data)
        elif c.nodeType == c.ELEMENT_NODE:
            out.append(_node_text(c))
    return "".join(out)


def _overlapping_text_pairs(svg: str):
    """Count pairs of <text> whose estimated bounding boxes overlap
    heavily (>50% of the smaller box), AFTER resolving ancestor
    transforms.  This catches the colliding / duplicated labels that the
    LLM-SVG path occasionally emits and that the bounds / oversize checks
    don't see.  Returns ``None`` when inconclusive (unparseable, or an
    irreducible rotation/matrix) so the caller declines to alert rather
    than false-positive on transformed output.

    Conservative: only considers labels of >= 5 visible chars (never axis
    ticks or single symbols), matching the de-collision pass so a figure
    that survives that pass and still overlaps is a genuine defect."""
    from xml.dom import minidom
    try:
        doc = minidom.parseString(svg)
    except Exception:  # noqa: BLE001
        return None
    boxes: list[tuple[float, float, float, float]] = []
    aborted = False

    def walk(node, ctm):
        nonlocal aborted
        if aborted or node.nodeType != node.ELEMENT_NODE:
            return
        tf = node.getAttribute("transform")
        if tf:
            local = _parse_transform(tf)
            if local is None:
                aborted = True
                return
            ctm = _compose(ctm, local)
        if node.tagName.split(":")[-1] == "text":
            content = re.sub(r"\s+", " ", _node_text(node)).strip()
            sx = node.getAttribute("x").split()[:1]
            sy = node.getAttribute("y").split()[:1]
            if len(content) >= 5 and sx and sy:
                try:
                    x, y = float(sx[0]), float(sy[0])
                    fs = float(re.sub(r"[^0-9.]", "",
                                      node.getAttribute("font-size") or "14")
                               or 14)
                except ValueError:
                    fs = None
                if fs:
                    w = max(1, len(content)) * 0.6 * fs * abs(ctm[0])
                    anchor = node.getAttribute("text-anchor") or "start"
                    X = ctm[0] * x + ctm[2]
                    Y = ctm[1] * y + ctm[3]
                    if anchor == "middle":
                        left = X - w / 2
                    elif anchor == "end":
                        left = X - w
                    else:
                        left = X
                    h = fs * 1.2 * abs(ctm[1])
                    boxes.append((left, Y - fs * abs(ctm[1]), left + w, Y + 0.25 * h))
        for c in node.childNodes:
            walk(c, ctm)

    walk(doc.documentElement, _IDENTITY)
    if aborted:
        return None
    pairs = 0
    for i in range(len(boxes)):
        ax0, ay0, ax1, ay1 = boxes[i]
        area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
        for j in range(i + 1, len(boxes)):
            bx0, by0, bx1, by1 = boxes[j]
            ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
            iy = max(0.0, min(ay1, by1) - max(ay0, by0))
            inter = ix * iy
            if inter <= 0:
                continue
            area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
            smaller = min(area_a, area_b)
            if smaller > 0 and inter / smaller > 0.5:
                pairs += 1
    return pairs


def inspect_quality(prompt: str, result: dict) -> list[str]:
    """Return a list of detected problems; empty means the figure is fine.
    Pure string/structural checks (no browser) on top of the in-pipeline
    vision/structural/math review the figure already passed."""
    issues: list[str] = []
    svg = result.get("svg") or ""
    if result.get("error"):
        issues.append(f"generation error: {result['error']}")
    if not svg or "<svg" not in svg:
        issues.append("empty or missing SVG")
        return issues  # nothing more to check
    # Valid XML
    try:
        from xml.dom import minidom
        minidom.parseString(svg)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"invalid SVG XML: {exc}")
    # viewBox bounds: every <text> anchor should sit inside it
    m = re.search(r'viewBox="([\-0-9.]+)\s+([\-0-9.]+)\s+([\-0-9.]+)\s+([\-0-9.]+)"', svg)
    if m:
        vx, vy, vw, vh = (float(g) for g in m.groups())
        # Resolve each <text>'s ancestor transforms before bounds-checking:
        # graphviz wraps the whole figure in a <g transform="translate(...)">
        # so raw x/y look out-of-bounds while the rendered glyph sits inside.
        # _texts_outside_viewbox returns None when it can't reduce a
        # transform (rotation/matrix) — we don't alert on inconclusive.
        outside = _texts_outside_viewbox(svg, vx, vy, vw, vh)
        if outside:
            issues.append(f"{outside} text element(s) outside the viewBox")
        # Oversized element: a single rect that nearly fills the canvas.
        # This heuristic only makes sense for the free-form LLM-SVG
        # fallback, where a giant coloured box is a real defect.
        # Deterministic routes (matplotlib / plotly / graphviz / …) draw a
        # full-canvas background + plot area BY DESIGN, so they are trusted
        # and skipped.  Even on the LLM path, ignore background-like rects
        # (white / none / transparent) — a white backdrop is not a defect.
        if not (result.get("template") or "").strip():
            for rm in re.finditer(r'<rect\b[^>]*?>', svg):
                tag = rm.group(0)
                wm = re.search(r'\bwidth="([0-9.]+)"', tag)
                hm = re.search(r'\bheight="([0-9.]+)"', tag)
                if not (wm and hm):
                    continue
                if (float(wm.group(1)) > 0.92 * vw
                        and float(hm.group(1)) > 0.92 * vh):
                    # A rect with NO fill attribute renders BLACK (a real
                    # box) — only an EXPLICIT light/none fill is a backdrop.
                    fm = re.search(r'fill\s*[:=]\s*["\']?\s*([#a-z0-9()]+)',
                                   tag, re.I)
                    fill = fm.group(1).lower() if fm else ""
                    if fill in ("none", "#fff", "#ffffff", "white",
                                "transparent"):
                        continue
                    issues.append("an element nearly fills the whole canvas")
                    break
    # Colliding / duplicated labels: heavily-overlapping text pairs.  The
    # de-collision pass should remove these, so any that survive to a
    # served figure are a real defect (and the class the bounds/oversize
    # checks miss).  None = inconclusive (don't alert).
    overlaps = _overlapping_text_pairs(svg)
    if overlaps:
        issues.append(f"{overlaps} pair(s) of heavily-overlapping text labels")
    # Leaked internals (model/provider names should never reach the SVG)
    low = svg.lower()
    for bad in ("gpt-4o", "openai", "claude", "system prompt", "as an ai"):
        if bad in low:
            issues.append(f"leaked internal token in SVG: {bad!r}")
    # Generation health: retries exhausted with the figure still failing
    rh = result.get("review_history") or []
    if int(result.get("retries_used") or 0) >= 2 and rh:
        issues.append(
            f"retries exhausted ({result.get('retries_used')}) with the "
            f"figure still failing review: {str(rh[-1])[:300]}")
    return issues


async def _generate(prompt: str) -> dict:
    """Run one prompt through the SAME production path the website uses."""
    sys.path.insert(0, os.getcwd())
    from studio.express import express_figure
    base = os.environ.get("OPENAI_BASE_URL") or os.environ.get(
        "SEVIM_VLLM_URL") or "https://api.openai.com/v1"
    model = (os.environ.get("SEVIM_FORCE_ACTIVE_MODEL")
             or os.environ.get("SEVIM_VLLM_MODEL") or "gpt-4o")
    key = os.environ.get("OPENAI_API_KEY", "")
    return await express_figure(prompt, base_url=base, model=model, api_key=key)


async def _run() -> tuple[str, dict]:
    prompt = _which_problem()
    return prompt, await _generate(prompt)


async def attempt_autofix(prompt: str, result: dict) -> tuple[bool, str, dict]:
    """Try to remediate a flagged figure WITHOUT touching code or the live
    system.  Two bounded steps, cheapest first:

      1. Re-run the safe, deterministic layout passes (`polish_svg`) on the
         existing SVG and re-inspect.  This alone fixes the common cases
         (duplicate labels, recoverable overlap) with no LLM call.
      2. Regenerate the figure from scratch and re-inspect.  This clears a
         TRANSIENT LLM-variance defect; a deterministic-route bug
         reproduces the same output and correctly stays unresolved, so it
         escalates to a human.

    Returns (resolved, method, fixed_result)."""
    # Step 1 — re-polish in place.
    try:
        from studio.express import polish_svg
        svg = result.get("svg") or ""
        repolished = polish_svg(svg)
        if repolished and repolished != svg:
            cand = {**result, "svg": repolished}
            if not inspect_quality(prompt, cand):
                return True, "layout re-polish", cand
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] autofix re-polish errored: {exc}", flush=True)
    # Step 2 — regenerate.
    try:
        regen = await _generate(prompt)
        if not inspect_quality(prompt, regen):
            return True, "regeneration", regen
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] autofix regeneration errored: {exc}", flush=True)
    return False, "", result


def main() -> int:
    if _today() > END_DATE:
        print(f"[probe] past end date {END_DATE}; no-op", flush=True)
        return 0
    # Run as `python -m studio.quality_probe` we bypass studio/__main__.py,
    # so hydrate SEVIM_TELEMETRY_DB from the RDS secret here — otherwise the
    # answer-cache/taxonomy reads hit an empty ephemeral SQLite instead of
    # the shared production DB.
    sys.path.insert(0, os.getcwd())
    try:
        from service.secrets import bootstrap as _bootstrap_secrets
        _bootstrap_secrets()
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] secret bootstrap skipped: {exc}", flush=True)
    try:
        prompt, result = asyncio.run(_run())
    except Exception:  # noqa: BLE001
        send_alert(
            "[Khayyam probe] the quality probe itself crashed",
            "The 6-hourly figure-quality probe failed to run:\n\n"
            + traceback.format_exc())
        return 1
    problems = inspect_quality(prompt, result)
    if not problems:
        print(f"[probe] clean: {prompt!r} (route={result.get('template')})",
              flush=True)
        return 0

    # A problem was detected.  Optionally try to auto-remediate the figure
    # before bothering the operator — escalate only PERSISTENT issues.
    if AUTOFIX:
        try:
            resolved, method, _fixed = asyncio.run(attempt_autofix(prompt, result))
        except Exception as exc:  # noqa: BLE001
            print(f"[probe] autofix crashed: {exc}", flush=True)
            resolved, method = False, ""
        if resolved:
            print(f"[probe] AUTO-FIXED via {method}: {prompt!r} "
                  f"(was: {problems})", flush=True)
            return 0
        autofix_note = (
            "Auto-fix was ON and attempted (re-polish + regeneration) but the "
            "figure still fails inspection, so this looks PERSISTENT and needs "
            "a human (likely a deterministic-route or systemic bug, not a "
            "transient flake).")
    else:
        autofix_note = "Auto-fix is OFF (SEVIM_PROBE_AUTOFIX not set)."

    body = (
        f"The 6-hourly quality probe flagged a figure on khayyammath.com.\n\n"
        f"Prompt:\n  {prompt}\n\nProblems detected:\n"
        + "\n".join(f"  - {p}" for p in problems)
        + f"\n\nroute={result.get('template')}  "
        f"retries_used={result.get('retries_used')}  "
        f"svg_len={len(result.get('svg') or '')}\n\n"
        + autofix_note
        + "\n\nThis is an automated probe; reply is not monitored. Inspect "
        "and fix the system as needed.")
    send_alert(f"[Khayyam probe] quality issue: {prompt[:60]}", body)
    print(f"[probe] PROBLEMS on {prompt!r}: {problems}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
