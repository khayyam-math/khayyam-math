"""Self-aware conversational refinement.

The live system used to treat every chat turn as a fresh, stateless
generation.  When a learner corrected a figure ("no, this is nonsense — I
need nodes! two graphs with nodes!") the correction message alone lost the
ORIGINAL subject ("reduce clique to vertex cover"), so routing could not
re-reach the right deterministic renderer and the LLM-SVG path simply
redrew the same defect.  Five identical complaints produced five identical
failures, with nothing noticing.

This module makes corrections behave like a conversation:

  Layer 1 — topic carry-forward.  On a correction, fold the prior figure's
    genesis prompt into the routing string so deterministic renderers can
    still match the carried subject.  (`carry_topic`)

  Layer 2 — repeat-failure circuit breaker.  A per-session tracker counts
    consecutive complaints about the SAME subject.  Once they pile up, the
    system stops regenerating the same way: it forces a strategy change and
    tells the model its previous attempts were rejected for the same
    reason, or surfaces an honest "here is the closest I can do" instead of
    a sixth identical failure.  (`Tracker`, `escalation_directive`)

  Layer 3 — self-constructive learning (admin-gated).  Each refinement
    outcome (subject, route, whether the user complained again) is logged
    so an offline curation pass can surface recurring (subject, failed
    route) pairs as routing hints.  Promotion of a hint to the live router
    stays behind the existing admin gate — the system never rewrites its
    own routing autonomously.  (`record_outcome`, `preferred_route_hint`)
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

# ── Dissatisfaction / correction cues ───────────────────────────────────
# Phrases that signal "your previous answer was wrong / not what I asked".
# Deliberately broad: a false positive only attaches more context or nudges
# a strategy change, which is cheap; a false negative reproduces the bug.
_DISSATISFACTION_RE = re.compile(
    r"\b(no|nope|wrong|incorrect|nonsense|garbage|useless|"
    r"not\s+(?:a|an|the|quite|right|correct|what|how|good|working)|"
    r"isn'?t|aren'?t|doesn'?t|don'?t|didn'?t|"
    r"that'?s\s+not|this\s+is\s+not|that\s+is\s+not|"
    r"still\s+(?:wrong|not|no|missing|bad|broken)|"
    r"again|i\s+need|i\s+want|i\s+said|i\s+asked|"
    r"rather\s+than|instead(?:\s+of)?|should\s+(?:be|have|show))\b",
    re.I,
)


def is_dissatisfaction(prompt: str) -> bool:
    """True when the message reads like a complaint/correction, not a fresh
    or additive request."""
    p = (prompt or "").strip()
    if not p:
        return False
    return bool(_DISSATISFACTION_RE.search(p))


def topic_signature(s: str) -> str:
    """A stable, normalised key for "what subject is this about" so the same
    subject across several correction turns collapses to one bucket."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80]


def extract_prior_topic(context_canvases: list[dict[str, Any]] | None) -> str:
    """The genesis prompt of the most recent attached figure — the subject
    the learner is still talking about."""
    if not context_canvases:
        return ""
    for c in reversed(context_canvases):
        t = (c.get("prompt") or "").strip()
        if t:
            return t
    return ""


def carry_topic(routing_prompt: str,
                context_canvases: list[dict[str, Any]] | None,
                *, is_narrow_edit: bool) -> str:
    """Layer 1.  On a Case B/C refinement (context attached, NOT a narrow
    targeted edit), prepend the prior subject so a topic-less correction
    ("I need nodes!") still routes to the original subject's renderer.

    A narrow edit ("make the curve red") is left untouched — it must edit
    the existing figure, not re-route.  Context is only ever attached on a
    same-subject refinement (topic switches arrive with no context), so
    carrying the subject forward cannot cross-contaminate a new topic.
    """
    cur = (routing_prompt or "").strip()
    if is_narrow_edit or not context_canvases:
        return cur
    if os.environ.get("SEVIM_REFINE_TOPIC_CARRY", "on").strip().lower() == "off":
        return cur
    topic = extract_prior_topic(context_canvases)
    if not topic:
        return cur
    if topic.lower() in cur.lower() or not cur:
        return topic if not cur else cur
    return f"{topic}. {cur}"


# ── Layer 2: per-session repeat-failure tracker ─────────────────────────

# How many complaints about the SAME subject before we force a strategy
# change instead of regenerating the same way.
_ESCALATE_AFTER = int(os.environ.get("SEVIM_REFINE_ESCALATE_AFTER", "2") or "2")
_TTL_S = 3600.0


class _Entry:
    __slots__ = ("ts", "sig", "route", "complaint")

    def __init__(self, ts: float, sig: str, route: str, complaint: bool):
        self.ts = ts
        self.sig = sig
        self.route = route
        self.complaint = complaint


class Tracker:
    """In-memory, per-session history of (subject, route, was-complaint).
    Lives for the process; entries older than an hour are pruned lazily.
    Telemetry (Layer 3) is the durable record — this is just the live
    circuit breaker."""

    def __init__(self) -> None:
        self._by_session: dict[str, list[_Entry]] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.time()

    def record(self, session_id: str, subject: str, route: str,
               *, complaint: bool) -> None:
        if not session_id:
            return
        sig = topic_signature(subject)
        with self._lock:
            lst = self._by_session.setdefault(session_id, [])
            lst.append(_Entry(self._now(), sig, route or "", complaint))
            cutoff = self._now() - _TTL_S
            self._by_session[session_id] = [e for e in lst if e.ts >= cutoff][-40:]

    def consecutive_complaints(self, session_id: str, subject: str) -> int:
        """How many times in a row the learner has complained about THIS
        subject (counting only complaint turns on the matching signature)."""
        sig = topic_signature(subject)
        with self._lock:
            lst = self._by_session.get(session_id, [])
        n = 0
        for e in reversed(lst):
            if e.sig != sig:
                continue
            if e.complaint:
                n += 1
            else:
                break
        return n

    def last_route(self, session_id: str, subject: str) -> str:
        sig = topic_signature(subject)
        with self._lock:
            lst = self._by_session.get(session_id, [])
        for e in reversed(lst):
            if e.sig == sig:
                return e.route
        return ""

    def should_escalate(self, session_id: str, subject: str) -> bool:
        return self.consecutive_complaints(session_id, subject) >= _ESCALATE_AFTER


_TRACKER = Tracker()


def get_tracker() -> Tracker:
    return _TRACKER


def escalation_directive(consecutive: int, prior_route: str,
                         deterministic_available: bool) -> str:
    """A blunt instruction injected into the refinement LLM context once the
    learner has complained repeatedly: stop repeating, change approach."""
    base = (
        f"\n=== REPEATED CORRECTION ({consecutive} in a row on this subject) ===\n"
        f"Your previous attempt(s) were REJECTED by the learner for the same "
        f"reason. Do NOT produce a variation of the same figure. Change the "
        f"APPROACH: if they asked for a graph with nodes, draw actual labelled "
        f"node-and-edge graphs; if they asked for a different representation, "
        f"switch to it. Re-read the original request literally and satisfy the "
        f"specific thing they keep asking for."
    )
    if not deterministic_available and prior_route in ("", "llm_svg", "llm-svg"):
        base += (
            "\nIf you genuinely cannot draw exactly what is requested, say so "
            "plainly in the narration and draw the closest faithful version "
            "rather than repeating the rejected one."
        )
    return base


# ── Layer 3: self-constructive learning (admin-gated) ───────────────────

def record_outcome(session_id: str, subject: str, route: str,
                   *, complaint: bool) -> None:
    """Update the live tracker AND, when telemetry is available, persist the
    refinement outcome so an offline curation pass can spot recurring
    (subject, failed-route) pairs.  Best-effort; never raises."""
    try:
        _TRACKER.record(session_id, subject, route, complaint=complaint)
    except Exception:  # noqa: BLE001
        pass
    try:
        from sevim.telemetry import get_telemetry
        tel = get_telemetry()
        if tel is not None and hasattr(tel, "record_refinement_outcome"):
            tel.record_refinement_outcome(
                session_id=session_id,
                subject=topic_signature(subject),
                route=route or "",
                complaint=complaint,
            )
    except Exception:  # noqa: BLE001
        pass


def preferred_route_hint(subject: str) -> str | None:
    """Layer 3 read path.  Returns an admin-PROMOTED preferred route for a
    subject signature, or None.  OFF by default: the hint table is only
    consulted when SEVIM_REFINE_HINTS=on, and entries are written only
    through the admin curation gate — never autonomously from the live
    request path.  This keeps 'learn over time' inside the same
    operator-gated safety model as template-taxonomy promotion."""
    if os.environ.get("SEVIM_REFINE_HINTS", "off").strip().lower() != "on":
        return None
    try:
        from sevim.telemetry import get_telemetry
        tel = get_telemetry()
        if tel is None or not hasattr(tel, "preferred_route_for"):
            return None
        return tel.preferred_route_for(topic_signature(subject)) or None
    except Exception:  # noqa: BLE001
        return None
