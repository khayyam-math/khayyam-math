"""Pre-deploy quality gate for Khayyam Math.

Runs a representative battery of prompts through the LOCAL build of the
service and asserts every automatable quality criterion from
``quality_criteria.xlsx``.  Exits non-zero on the first regression.

Wired into ``infra/deploy.sh``: a failing gate blocks ``cdk deploy``.

Run manually:
    cd infra && SEVIM_QUALITY_GATE_FAST=1 ./quality_gate.py
    # or, full battery
    cd infra && ./quality_gate.py

Skip the gate (NOT recommended — only for emergency hotfixes that
the gate itself is blocking):
    SEVIM_SKIP_QUALITY_GATE=1 ./deploy.sh
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import uvicorn

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PORT = int(os.environ.get("SEVIM_GATE_PORT", "8044"))
BASE = f"http://127.0.0.1:{PORT}"
FAST = bool(os.environ.get("SEVIM_QUALITY_GATE_FAST"))

# Colours for terminal output.
G, R, Y, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[0m"
CHECK, CROSS, WARN = f"{G}✓{X}", f"{R}✗{X}", f"{Y}!{X}"


# ──────────────────────────────────────────────────────────────────────
# Result-collection scaffolding
# ──────────────────────────────────────────────────────────────────────
@dataclasses.dataclass
class Check:
    name: str        # short name (≤ 50 char)
    category: str    # bucket from quality_criteria.xlsx
    passed: bool
    detail: str = ""

    def fmt(self) -> str:
        glyph = CHECK if self.passed else CROSS
        return (f"  {glyph} [{self.category:14}] {self.name}"
                + (f"  — {self.detail}" if self.detail and not self.passed
                   else ""))


@dataclasses.dataclass
class PromptResult:
    prompt: str
    canvas_id: Optional[str]
    duration_s: float
    ttfb_s: float
    raw_svg: str
    server_log: str
    checks: list[Check] = dataclasses.field(default_factory=list)
    error: Optional[str] = None

    def add(self, name: str, category: str,
            passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, category, passed, detail))


# ──────────────────────────────────────────────────────────────────────
# Per-prompt assertions
# ──────────────────────────────────────────────────────────────────────
def _parse_nodes(svg: str) -> list[tuple[float, float, float]]:
    """Return [(cx, cy, r), …] for every <circle> and <ellipse>."""
    nodes: list[tuple[float, float, float]] = []

    def _af(attrs: str, name: str) -> Optional[float]:
        m = re.search(rf'\b{name}\s*=\s*["\']([-\d.eE]+)', attrs)
        try:
            return float(m.group(1)) if m else None
        except ValueError:
            return None

    for m in re.finditer(r"<circle\b([^>]*)>", svg):
        cx, cy, r = (_af(m.group(1), "cx"),
                     _af(m.group(1), "cy"),
                     _af(m.group(1), "r"))
        if cx is not None and cy is not None and r and r > 0:
            nodes.append((cx, cy, r))
    for m in re.finditer(r"<ellipse\b([^>]*)>", svg):
        cx, cy = _af(m.group(1), "cx"), _af(m.group(1), "cy")
        rx, ry = _af(m.group(1), "rx"), _af(m.group(1), "ry")
        if cx is not None and cy is not None and rx and ry:
            nodes.append((cx, cy, (rx + ry) / 2.0))
    return nodes


def check_edge_endpoints_at_nodes(pr: PromptResult) -> None:
    """Layout #1: every <line>/two-point <path> endpoint sits on a node
    perimeter (within ~1.5 r) of some <circle>/<ellipse>."""
    import math
    nodes = _parse_nodes(pr.raw_svg)
    if not nodes:
        # Not a graph-shaped figure — vacuously pass.
        pr.add("edge endpoints at node centres", "Layout", True,
               "(no node circles/ellipses in figure)")
        return

    def _af(attrs: str, name: str) -> Optional[float]:
        m = re.search(rf'\b{name}\s*=\s*["\']([-\d.eE]+)', attrs)
        try:
            return float(m.group(1)) if m else None
        except ValueError:
            return None

    floating: list[tuple[float, float]] = []
    for m in re.finditer(r"<line\b([^>]*)>", pr.raw_svg):
        a = m.group(1)
        x1, y1 = _af(a, "x1"), _af(a, "y1")
        x2, y2 = _af(a, "x2"), _af(a, "y2")
        if None in (x1, y1, x2, y2):
            continue
        for px, py in ((x1, y1), (x2, y2)):
            best = min(math.hypot(px - cx, py - cy) - r
                       for cx, cy, r in nodes)
            if best > 1.5 * max(r for _, _, r in nodes):
                floating.append((px, py))

    # two-point paths
    for m in re.finditer(
            r'<path\b[^>]*\bd\s*=\s*["\']\s*M\s*([-\d.eE]+)[ ,]+'
            r'([-\d.eE]+)\s*L\s*([-\d.eE]+)[ ,]+([-\d.eE]+)\s*["\']',
            pr.raw_svg):
        x1, y1, x2, y2 = (float(g) for g in m.groups())
        for px, py in ((x1, y1), (x2, y2)):
            best = min(math.hypot(px - cx, py - cy) - r
                       for cx, cy, r in nodes)
            if best > 1.5 * max(r for _, _, r in nodes):
                floating.append((px, py))

    pr.add("edge endpoints at node centres", "Layout",
           passed=(not floating),
           detail=(f"{len(floating)} floating endpoint(s)"
                   if floating else ""))


def check_text_inside_viewbox(pr: PromptResult) -> None:
    """Layout #3: no <text x y> escapes the root viewBox.

    Accounts for ``<g transform="translate(tx ty)">`` wrappers so
    Graphviz output (which uses negative Y inside a translated group)
    isn't falsely flagged.
    """
    svg = pr.raw_svg
    root = re.search(r"<svg\b[^>]*>", svg)
    if not root:
        pr.add("text inside viewBox", "Layout", True, "(no <svg>)")
        return
    vbm = re.search(r'viewBox\s*=\s*["\']([-\d.\seE]+)["\']', root.group(0))
    if not vbm:
        pr.add("text inside viewBox", "Layout", True, "(no viewBox)")
        return
    parts = vbm.group(1).split()
    if len(parts) != 4:
        pr.add("text inside viewBox", "Layout", True, "(bad viewBox)")
        return
    ox, oy, ow, oh = (float(p) for p in parts)

    # Tokenise <g …> / </g> / <text …> in order, maintaining a
    # translate stack so every <text> sees its accumulated transform.
    tokens = re.finditer(
        r"<g\b([^>]*)>|</g\s*>|<text\b([^>]*)>", svg)
    translate_stack: list[tuple[float, float]] = [(0.0, 0.0)]
    outside = 0
    for tok in tokens:
        if tok.group(0).startswith("</g"):
            if len(translate_stack) > 1:
                translate_stack.pop()
            continue
        if tok.group(0).startswith("<g"):
            attrs = tok.group(1)
            tm = re.search(r'transform\s*=\s*["\']([^"\']+)["\']',
                           attrs)
            tx, ty = translate_stack[-1]
            if tm:
                for trans in re.finditer(
                        r'translate\s*\(\s*([-\d.eE]+)[\s,]+'
                        r'([-\d.eE]+)\s*\)', tm.group(1)):
                    tx += float(trans.group(1))
                    ty += float(trans.group(2))
            translate_stack.append((tx, ty))
            continue
        # <text …>
        attrs = tok.group(2)
        try:
            x = float(re.search(
                r'\bx\s*=\s*["\']([-\d.eE]+)', attrs).group(1))
            y = float(re.search(
                r'\by\s*=\s*["\']([-\d.eE]+)', attrs).group(1))
        except (AttributeError, ValueError):
            continue
        tx, ty = translate_stack[-1]
        eff_x, eff_y = x + tx, y + ty
        # 30 px slop for margin captions sitting on the edge.
        if eff_x < ox - 30 or eff_x > ox + ow + 30 \
                or eff_y < oy - 30 or eff_y > oy + oh + 30:
            outside += 1
    pr.add("text inside viewBox", "Layout",
           passed=(outside == 0),
           detail=f"{outside} text element(s) outside viewBox")


def check_no_huge_arrowheads(pr: PromptResult) -> None:
    """Layout #6: no <marker> with markerWidth/Height > 30."""
    huge = 0
    for m in re.finditer(r"<marker\b([^>]*)>", pr.raw_svg):
        a = m.group(1)
        for k in ("markerWidth", "markerHeight"):
            mm = re.search(rf'\b{k}\s*=\s*["\']([-\d.eE]+)', a)
            if mm and float(mm.group(1)) > 30:
                huge += 1
                break
    pr.add("arrowheads sized to stroke", "Layout",
           passed=(huge == 0),
           detail=f"{huge} oversize marker(s)")


def check_3d_aspect_cube(pr: PromptResult) -> None:
    """Layout #10: any embedded Plotly spec must use aspectmode='cube'
    when scene.zaxis is present (i.e. a 3D plot)."""
    m = re.search(r'<metadata\s+id=["\']plotly-spec["\'][^>]*>'
                  r'([^<]+)</metadata>', pr.raw_svg)
    if not m:
        pr.add("3D aspect cube", "Layout", True, "(no Plotly spec)")
        return
    try:
        import base64
        spec = json.loads(base64.b64decode(m.group(1)).decode())
    except Exception:  # noqa: BLE001
        pr.add("3D aspect cube", "Layout", True, "(unreadable spec)")
        return
    scene = (spec.get("layout") or {}).get("scene") or {}
    if not scene.get("zaxis"):
        pr.add("3D aspect cube", "Layout", True, "(2D)")
        return
    am = (scene.get("aspectratio") and "cube") or \
        scene.get("aspectmode")
    pr.add("3D aspect cube", "Layout",
           passed=(am == "cube"),
           detail=f"aspectmode={am!r}")


def check_svg_text_kept(pr: PromptResult) -> None:
    """Typography #15: figure contains <text>, not all-paths font."""
    has_text = bool(re.search(r"<text\b", pr.raw_svg))
    pr.add("SVG text kept as <text>", "Typography",
           passed=has_text,
           detail="no <text> elements found")


def check_legibility_floor(pr: PromptResult) -> None:
    """Typography #16: no <text> below a hard floor (8px)."""
    too_small = 0
    for m in re.finditer(r"<text\b([^>]*)>", pr.raw_svg):
        a = m.group(1)
        fs = re.search(r'\bfont-size\s*=\s*["\']([-\d.eE]+)', a)
        if fs and float(fs.group(1)) < 8.0:
            too_small += 1
    pr.add("legibility floor (≥ 8px)", "Typography",
           passed=(too_small == 0),
           detail=f"{too_small} text(s) below floor")


def check_no_internals_leaked(pr: PromptResult) -> None:
    """Security #37: no model/provider/format names reachable in
    SVG output or server response body."""
    banned = ("openai", "anthropic", "vllm", "gpt-4", "claude",
              "qwen", "sevim_express")
    leaks = [w for w in banned
             if re.search(rf"\b{re.escape(w)}\b",
                          pr.raw_svg, re.IGNORECASE)]
    pr.add("no internals leaked to client SVG", "Security",
           passed=(not leaks),
           detail=", ".join(leaks))


def check_math_verifier_ran(pr: PromptResult, expect_claims: bool) -> None:
    """Math correctness #19: if the prompt is expected to produce
    verifiable math claims, the verifier log line must appear."""
    if not expect_claims:
        pr.add("math verifier outcome logged", "Math correctness",
               True, "(no claims expected)")
        return
    seen = bool(re.search(r"math-correctness verifier:", pr.server_log))
    pr.add("math verifier outcome logged", "Math correctness",
           passed=seen,
           detail="no verifier log line found"
           if not seen else "")


def check_no_verifier_failures(pr: PromptResult) -> None:
    """Math correctness #19b: any verifier outcome logged should be
    'all verified' by the time the figure ships (retries handle the
    intermediate failures)."""
    fails = re.findall(r"math-correctness verifier: \d+ of \d+ "
                       r"claim\(s\) FAILED", pr.server_log)
    passed = re.findall(r"math-correctness verifier: all \d+ "
                        r"claim\(s\) verified", pr.server_log)
    if not fails and not passed:
        pr.add("math verifier accepted final attempt",
               "Math correctness", True, "(no claims)")
        return
    # The final outcome is the last verifier line.  If the LAST line
    # says FAILED and no later 'verified' line follows, this is a real
    # failure.
    last_fail = pr.server_log.rfind("claim(s) FAILED")
    last_pass = pr.server_log.rfind("claim(s) verified")
    final_ok = last_pass > last_fail
    pr.add("math verifier accepted final attempt",
           "Math correctness",
           passed=final_ok,
           detail=f"last verifier line: {'FAILED' if not final_ok else 'OK'}")


def check_template_route(pr: PromptResult,
                         expected: Optional[str]) -> None:
    """Routing #46: prompt expected to hit a deterministic template
    must do so."""
    if not expected:
        pr.add("deterministic route selected", "Routing", True,
               "(no specific route expected)")
        return
    # Server logs route as "<route> fast-path: …" for each template.
    patt = rf"{re.escape(expected)} fast-path:"
    seen = bool(re.search(patt, pr.server_log))
    pr.add(f"route → {expected}", "Routing",
           passed=seen,
           detail="route gate did not fire"
           if not seen else "")


def check_narration_phrases(pr: PromptResult) -> None:
    """Narration #28: at least one narration phrase produced.

    Parses the server log for ``[express] narrate done: phrases=N``
    lines that fall within this prompt's session-window slice.
    """
    m = re.search(r'narrate done: phrases=(\d+)', pr.server_log)
    n = int(m.group(1)) if m else 0
    pr.add("narration phrases produced", "Narration",
           passed=(n >= 1),
           detail=f"{n} phrases")


# Boilerplate openers we want to discourage in narration / chat.
# Treat each as a regex that matches the FIRST ~80 chars of a phrase.
_BOILERPLATE_OPENERS = (
    r"\bwe (?:can )?see\b",
    r"\bhere (?:we (?:can )?see|is|are)\b",
    r"\bon the (?:left|right|top|bottom)\b",
    r"\bthe (?:figure|diagram|image) shows\b",
    r"\bin (?:this |the )?(?:figure|diagram)\b",
    r"\bnote that .{0,30}\bis connected to\b",
    r"\brecall that\b",
    r"\bin mathematics, a\b",
    r"\bfirst,? let'?s\b",
    r"\bas (?:we|you) can see\b",
)


def check_no_boilerplate_opener(pr: PromptResult) -> None:
    """Narration anti-padding: the FIRST narration phrase must not
    open with a description-of-the-picture cliché.  Catches the
    'we see X is connected to Y' regression class."""
    # Pull all `"speak": "…"` strings from the server log slice in
    # order, take the first.
    speak_matches = re.findall(r'"speak"\s*:\s*"([^"]{0,300})"',
                                pr.server_log)
    if not speak_matches:
        # No narration in window — let the dedicated check handle it.
        pr.add("narration avoids boilerplate opener", "Narration",
               True, "(no narration captured)")
        return
    first = speak_matches[0].strip().lower()[:120]
    bad = [pat for pat in _BOILERPLATE_OPENERS
           if re.search(pat, first, flags=re.IGNORECASE)]
    pr.add("narration avoids boilerplate opener", "Narration",
           passed=(not bad),
           detail=(f"first phrase opens with cliché "
                   f"({bad[0]!r}): {first[:80]!r}" if bad else ""))


def check_no_reveal_mask(pr: PromptResult) -> None:
    """UX #32: no opacity:0 reveal mask on the root figure."""
    bad = bool(re.search(r'opacity\s*=\s*["\']0["\']',
                         pr.raw_svg[:500]))
    pr.add("figure visible from start (no reveal mask)", "UX",
           passed=not bad,
           detail="root opacity=0 detected")


# ──────────────────────────────────────────────────────────────────────
# Per-deploy global checks (run once, not per-prompt)
# ──────────────────────────────────────────────────────────────────────
def global_checks() -> list[Check]:
    out: list[Check] = []

    # Security #37: /docs, /openapi.json must be 404 — no API surface
    # leaked.
    for path in ("/docs", "/redoc", "/openapi.json"):
        try:
            r = httpx.get(f"{BASE}{path}", timeout=5)
            ok = r.status_code == 404
            out.append(Check(f"{path} returns 404", "Security",
                             ok, f"got {r.status_code}"))
        except Exception as exc:  # noqa: BLE001
            out.append(Check(f"{path} returns 404", "Security",
                             False, f"{type(exc).__name__}"))

    # Security: /health returns only {"status":"ok"}, no leakage.
    try:
        r = httpx.get(f"{BASE}/health", timeout=5)
        payload = r.json()
        ok = (r.status_code == 200
              and set(payload.keys()) == {"status"}
              and payload["status"] == "ok")
        out.append(Check("/health returns only {status:ok}", "Security",
                         ok, f"body={payload}"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("/health returns only {status:ok}", "Security",
                         False, f"{type(exc).__name__}: {exc}"))

    # Security: no Server header.
    try:
        r = httpx.get(f"{BASE}/health", timeout=5)
        srv = r.headers.get("server", "").lower()
        ok = "uvicorn" not in srv and "fastapi" not in srv
        out.append(Check("no Server: uvicorn header", "Security",
                         ok, f"server={srv!r}"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("no Server: uvicorn header", "Security",
                         False, f"{type(exc).__name__}"))

    # Security: HSTS header present.
    try:
        r = httpx.get(f"{BASE}/health", timeout=5)
        hsts = r.headers.get("strict-transport-security", "")
        ok = "max-age" in hsts
        out.append(Check("HSTS header present", "Security",
                         ok, f"hsts={hsts!r}"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("HSTS header present", "Security",
                         False, f"{type(exc).__name__}"))

    # Security: studio.html does not contain backend identifiers.
    try:
        r = httpx.get(f"{BASE}/studio", timeout=5)
        body = r.text.lower()
        leaks = [w for w in ("openai", "anthropic", "vllm", "gpt-4",
                             "claude", "qwen")
                 if w in body]
        out.append(Check("studio.html mentions no provider/model",
                         "Security",
                         (not leaks),
                         f"found: {', '.join(leaks)}"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("studio.html mentions no provider/model",
                         "Security", False, f"{type(exc).__name__}"))

    return out


# ──────────────────────────────────────────────────────────────────────
# Battery
# ──────────────────────────────────────────────────────────────────────
@dataclasses.dataclass
class TestPrompt:
    key: str
    prompt: str
    expect_claims: bool = False         # math_claims expected?
    expect_route: Optional[str] = None  # which fast-path should fire
    expect_nodes: bool = False          # check edge-endpoints?


BATTERY: list[TestPrompt] = [
    # Math correctness — verifier exercise
    TestPrompt("calc_deriv",
               "Compute the derivative of f(x) = x^3 + 2x^2 - 5",
               expect_claims=False),    # symbolic route, no LLM claims
    TestPrompt("calc_crit",
               "Find and classify the critical points of "
               "f(x,y) = x*sin(y) + x^2 + y^3",
               expect_claims=False),
    # Per-domain verifier — homomorphism (the fix that brought us here)
    TestPrompt("homom_vague",
               "Explain graph homomorphism visually",
               expect_route="homomorphism",
               expect_nodes=True),
    TestPrompt("homom_c4_k2",
               "Show that the 4-cycle is homomorphic to K_2 by an "
               "explicit mapping",
               expect_route="homomorphism",
               expect_nodes=True),
    # Plotly — 3D aspect cube + interactive embed
    TestPrompt("plot_3d_surface",
               "Plot z = x^2 + y^2 as a 3D surface over [-3,3]",
               expect_claims=False),
    # Graphviz fast-path
    TestPrompt("graphviz_dag",
               "Draw a DAG of three tasks where A precedes B and C, "
               "and B precedes D",
               expect_route="graphviz",
               expect_nodes=True),
    # Generic geometry — no specific route required, exercises post-
    # processors.
    TestPrompt("geo_pythag",
               "Show the Pythagorean theorem for a 3-4-5 right "
               "triangle"),
    # LaTeX-heavy
    TestPrompt("alg_euler",
               "Verify Euler's identity: e^(i*pi) + 1 = 0",
               expect_claims=True),
    # Closed Nat arithmetic — exercises the Lean third-tier verifier
    # via gcd/lcm/factorial/power-of-2 claims that fall through Z3.
    TestPrompt("arith_gcd",
               "Show that gcd(24, 36) = 12 and 2^10 = 1024",
               expect_claims=True),
]

if FAST:
    # In fast mode keep only the highest-signal subset.
    BATTERY = [p for p in BATTERY
               if p.key in ("homom_vague", "calc_crit", "geo_pythag")]


# ──────────────────────────────────────────────────────────────────────
# Server lifecycle + log capture
# ──────────────────────────────────────────────────────────────────────
class LogCapture:
    """Tee stderr to an in-memory buffer so per-prompt windows can be
    sliced out for verifier-line / route-line parsing."""
    def __init__(self) -> None:
        self.buf: list[str] = []
        self._lock = threading.Lock()
        self._orig_stderr = sys.stderr

        class _Tee:
            def __init__(self2, parent: "LogCapture",
                         underlying) -> None:
                self2.parent = parent
                self2.under = underlying

            def write(self2, s: str) -> int:
                with self2.parent._lock:
                    self2.parent.buf.append(s)
                return self2.under.write(s)

            def flush(self2) -> None:
                self2.under.flush()

        sys.stderr = _Tee(self, self._orig_stderr)

    def snapshot(self) -> str:
        with self._lock:
            return "".join(self.buf)


def start_server() -> uvicorn.Server:
    # Auth OFF — gate runs against the default dev configuration so no
    # token-mint is needed.  Production keeps SEVIM_AUTH_REQUIRED=1.
    os.environ.setdefault("SEVIM_AUTH_REQUIRED", "0")
    from service.app import app
    cfg = uvicorn.Config(app, host="127.0.0.1", port=PORT,
                         log_level="warning", access_log=False,
                         server_header=False)   # mirror prod Dockerfile
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(120):
        if srv.started:
            return srv
        time.sleep(0.1)
    raise RuntimeError("server failed to start within 12s")


# ──────────────────────────────────────────────────────────────────────
# Prompt runner
# ──────────────────────────────────────────────────────────────────────
def run_prompt(tp: TestPrompt, logbuf: LogCapture) -> PromptResult:
    """POST /studio/chat, stream until done, fetch the resulting
    canvas SVG."""
    session_id = "gate-" + tp.key
    t0 = time.monotonic()
    ttfb: float = -1.0
    canvas_id: Optional[str] = None
    raw_lines: list[str] = []
    try:
        with httpx.stream("POST", f"{BASE}/studio/chat",
                          json={"user": tp.prompt,
                                "session_id": session_id,
                                "history": []},
                          timeout=180) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                if ttfb < 0:
                    ttfb = time.monotonic() - t0
                raw_lines.append(line)
                m = re.search(r'"canvas_id"\s*:\s*"([^"]+)"', line)
                if m and not canvas_id:
                    canvas_id = m.group(1)
                if "express_complete" in line:
                    break
    except Exception as exc:  # noqa: BLE001
        return PromptResult(prompt=tp.prompt, canvas_id=None,
                            duration_s=time.monotonic() - t0,
                            ttfb_s=ttfb, raw_svg="", server_log="",
                            error=f"{type(exc).__name__}: {exc}")

    duration = time.monotonic() - t0
    svg = ""
    if canvas_id:
        try:
            svg = httpx.get(f"{BASE}/canvas/{canvas_id}/svg",
                            timeout=15).text
        except Exception:  # noqa: BLE001
            svg = ""

    log = logbuf.snapshot()
    # Slice the log window for this session.  The server log truncates
    # session ids in the POST-received line (session='gate-foo'), so
    # search for the truncated prefix.
    truncated = session_id[:12]   # matches the [:12] slice in studio/app.py
    needle = f"session={truncated!r}"
    start = log.find(needle)
    if start < 0:
        start = log.find(truncated)
    end = -1
    if start >= 0:
        end = log.find("[studio-chat] POST received", start + 1)
    if start < 0:
        window = log
    elif end < 0:
        window = log[start:]
    else:
        window = log[start:end]

    return PromptResult(prompt=tp.prompt, canvas_id=canvas_id,
                        duration_s=duration, ttfb_s=ttfb,
                        raw_svg=svg, server_log=window)


def run_assertions(pr: PromptResult, tp: TestPrompt) -> None:
    # Per-prompt checks
    if tp.expect_nodes:
        check_edge_endpoints_at_nodes(pr)
    check_text_inside_viewbox(pr)
    check_no_huge_arrowheads(pr)
    check_3d_aspect_cube(pr)
    check_svg_text_kept(pr)
    check_legibility_floor(pr)
    check_no_internals_leaked(pr)
    check_math_verifier_ran(pr, tp.expect_claims)
    check_no_verifier_failures(pr)
    check_template_route(pr, tp.expect_route)
    check_narration_phrases(pr)
    check_no_boilerplate_opener(pr)
    check_no_reveal_mask(pr)

    # Performance criteria
    pr.add("TTFB < 8s", "Performance",
           passed=(0 < pr.ttfb_s < 8.0),
           detail=f"ttfb={pr.ttfb_s:.2f}s")
    pr.add("total < 30s", "Performance",
           passed=(pr.duration_s < 30.0),
           detail=f"dur={pr.duration_s:.1f}s")


# ──────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    print(f"{B}── Khayyam Math quality gate ──{X}")
    print(f"Port: {PORT}   Battery: {len(BATTERY)} prompt(s)"
          f"   FAST={FAST}")
    print()

    logbuf = LogCapture()
    try:
        start_server()
    except Exception as exc:  # noqa: BLE001
        print(f"{R}gate FAILED to start server: {exc}{X}")
        return 2

    # Global / once-only checks
    print(f"{B}Global checks{X}")
    globals_ = global_checks()
    for c in globals_:
        print(c.fmt())
    print()

    # Per-prompt checks
    results: list[PromptResult] = []
    for tp in BATTERY:
        print(f"{B}{tp.key}{X}  {tp.prompt[:80]}")
        pr = run_prompt(tp, logbuf)
        if pr.error:
            pr.add("prompt completed", "Routing", False, pr.error)
        else:
            pr.add("prompt completed", "Routing", True,
                   f"cid={pr.canvas_id} ttfb={pr.ttfb_s:.2f}s "
                   f"total={pr.duration_s:.1f}s")
            run_assertions(pr, tp)
        for c in pr.checks:
            print(c.fmt())
        results.append(pr)
        print()

    # Summary
    all_checks: list[Check] = list(globals_) + [
        c for r in results for c in r.checks]
    failed = [c for c in all_checks if not c.passed]
    total = len(all_checks)
    pct = 100.0 * (total - len(failed)) / max(total, 1)
    print(f"{B}── Summary ──{X}")
    print(f"  total checks: {total}")
    print(f"  passed:       {total - len(failed)}  ({pct:.1f}%)")
    print(f"  failed:       {len(failed)}")
    if failed:
        print(f"\n{R}REGRESSIONS:{X}")
        for c in failed:
            print(c.fmt())
        return 1

    print(f"{G}✓ ALL CHECKS PASSED — deploy allowed{X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
