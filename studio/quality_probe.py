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
ALERT_EMAIL = os.environ.get("SEVIM_PROBE_ALERT_EMAIL", "arash_kermani@yahoo.com")

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
    s = os.environ.get("SEVIM_SES_FROM_ADDRESS") or "noreply@khayyammath.com"
    return s if "<" in s else f"Khayyam Math probe <{s}>"


def send_alert(subject: str, body: str) -> None:
    try:
        import boto3
        boto3.client("ses").send_email(
            Source=_sender(),
            Destination={"ToAddresses": [ALERT_EMAIL]},
            Message={"Subject": {"Data": subject[:200]},
                     "Body": {"Text": {"Data": body[:60000]}}},
        )
        print(f"[probe] alert e-mailed to {ALERT_EMAIL}", flush=True)
    except Exception as exc:  # noqa: BLE001
        # Last resort: at least surface it in the task logs.
        print(f"[probe] FAILED to send alert: {exc}\n{subject}\n{body}",
              flush=True, file=sys.stderr)


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
        pad = 6.0
        outside = 0
        for tm in re.finditer(r'<text\b[^>]*\bx="([\-0-9.]+)"[^>]*\by="([\-0-9.]+)"', svg):
            x, y = float(tm.group(1)), float(tm.group(2))
            if x < vx - pad or x > vx + vw + pad or y < vy - pad or y > vy + vh + pad:
                outside += 1
        if outside:
            issues.append(f"{outside} text element(s) outside the viewBox")
        # oversized element: any rect/circle covering most of the canvas
        for rm in re.finditer(r'<rect\b[^>]*\bwidth="([0-9.]+)"[^>]*\bheight="([0-9.]+)"', svg):
            if float(rm.group(1)) > 0.92 * vw and float(rm.group(2)) > 0.92 * vh:
                issues.append("an element nearly fills the whole canvas")
                break
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


async def _run() -> tuple[str, dict]:
    prompt = _which_problem()
    sys.path.insert(0, os.getcwd())
    from studio.express import express_figure
    base = os.environ.get("OPENAI_BASE_URL") or os.environ.get(
        "SEVIM_VLLM_URL") or "https://api.openai.com/v1"
    model = (os.environ.get("SEVIM_FORCE_ACTIVE_MODEL")
             or os.environ.get("SEVIM_VLLM_MODEL") or "gpt-4o")
    key = os.environ.get("OPENAI_API_KEY", "")
    result = await express_figure(prompt, base_url=base, model=model, api_key=key)
    return prompt, result


def main() -> int:
    if _today() > END_DATE:
        print(f"[probe] past end date {END_DATE}; no-op", flush=True)
        return 0
    try:
        prompt, result = asyncio.run(_run())
    except Exception:  # noqa: BLE001
        send_alert(
            "[Khayyam probe] the quality probe itself crashed",
            "The 6-hourly figure-quality probe failed to run:\n\n"
            + traceback.format_exc())
        return 1
    problems = inspect_quality(prompt, result)
    if problems:
        body = (
            f"The 6-hourly quality probe flagged a figure on khayyammath.com.\n\n"
            f"Prompt:\n  {prompt}\n\nProblems detected:\n"
            + "\n".join(f"  - {p}" for p in problems)
            + f"\n\nroute={result.get('template')}  "
            f"retries_used={result.get('retries_used')}  "
            f"svg_len={len(result.get('svg') or '')}\n\n"
            "This is an automated probe; reply is not monitored. Inspect and "
            "fix the system as needed.")
        send_alert(
            f"[Khayyam probe] quality issue: {prompt[:60]}", body)
        print(f"[probe] PROBLEMS on {prompt!r}: {problems}", flush=True)
    else:
        print(f"[probe] clean: {prompt!r} (route={result.get('template')})",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
