#!/usr/bin/env python3
"""Daily e-mail digest of open "Not quite right?" feedback reports.

Runs as a scheduled ECS task (EventBridge, once a day).  It queries the
telemetry ``feedback`` table for OPEN reports and e-mails the operator a
digest, so user-reported problems are actually seen rather than sitting
unread in the admin queue.  It only surfaces reports --- it never edits a
figure or marks anything resolved.  No e-mail is sent when there are no
open reports (no empty-inbox noise), so a quiet inbox means no complaints.
"""
from __future__ import annotations

import datetime
import os
import sys

# Recipient comes from the environment only (shared with the probe alert).
ALERT_EMAIL = os.environ.get("SEVIM_PROBE_ALERT_EMAIL", "").strip()
_ADMIN_URL = "https://khayyammath.com/studio/admin/feedback"


def _fmt(ts) -> str:
    return datetime.datetime.fromtimestamp(
        float(ts), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _block(items) -> str:
    out: list[str] = []
    for fid, ts, prompt, desc, canvas, sha in items:
        out.append(f"  - [{_fmt(ts)}]  prompt: {(prompt or '(none)').strip()[:140]}")
        out.append(f"      \"{(desc or '').strip()[:320]}\"")
        out.append(f"      canvas={canvas or '-'}  sha={(sha or '-')[:12]}  "
                   f"report #{fid}")
        out.append("")
    return "\n".join(out)


def build_digest(rows, now):
    """Return ``(subject, body)`` or ``None`` when there is nothing to send.

    ``rows`` are tuples ``(feedback_id, timestamp, original_prompt,
    user_description, canvas_id, git_sha)`` for OPEN reports, newest first.
    """
    if not rows:
        return None
    day = 86400.0
    new = [r for r in rows if now - float(r[1]) <= day * 1.05]
    older = [r for r in rows if now - float(r[1]) > day * 1.05]
    body = [f"{len(rows)} open 'Not quite right?' report(s) on "
            f"khayyammath.com.", ""]
    if new:
        body += [f"NEW in the last 24 h ({len(new)}):", _block(new)]
    if older:
        body += [f"Still open from before ({len(older)}):", _block(older[:30])]
        if len(older) > 30:
            body += [f"  ...and {len(older) - 30} more open reports.", ""]
    body += ["To act: ask your assistant to fix the prompts above, or mark "
             "reports resolved at", _ADMIN_URL + "."]
    subject = (f"[Khayyam Math] {len(new)} new + {len(older)} older "
               f"'Not quite right?' report(s)")
    return subject, "\n".join(body)


def _send(subject: str, body: str) -> None:
    if not ALERT_EMAIL:
        print(f"[digest] no SEVIM_PROBE_ALERT_EMAIL set; would have sent:\n"
              f"{subject}\n{body}", flush=True)
        return
    from service.mailer import sender_address, send_email
    src = sender_address() or "Khayyam Math <noreply@khayyammath.com>"
    ok = send_email(
        to=ALERT_EMAIL,
        subject=subject[:200],
        text=body[:120000],
        sender=src,
    )
    if ok:
        print(f"[digest] e-mailed to {ALERT_EMAIL}", flush=True)
    else:
        print("[digest] FAILED to send", flush=True, file=sys.stderr)


def main() -> int:
    sys.path.insert(0, os.getcwd())
    # Hydrate SEVIM_TELEMETRY_DB from the RDS secret BEFORE touching
    # telemetry.  Run as `python -m studio.feedback_digest` we bypass
    # studio/__main__.py (which normally bootstraps), so without this the
    # telemetry layer falls back to an ephemeral per-container SQLite and
    # never sees the production feedback table.
    try:
        from service.secrets import bootstrap as _bootstrap_secrets
        _bootstrap_secrets()
    except Exception as exc:  # noqa: BLE001
        print(f"[digest] secret bootstrap skipped: {exc}", flush=True)
    try:
        from sevim.telemetry import get_telemetry
        tel = get_telemetry()
    except Exception as exc:  # noqa: BLE001
        print(f"[digest] telemetry unavailable: {exc}", flush=True)
        return 0
    if tel is None:
        print("[digest] no telemetry configured", flush=True)
        return 0
    rows = tel.query(
        "SELECT feedback_id, timestamp, original_prompt, user_description, "
        "canvas_id, git_sha FROM feedback WHERE status = ? "
        "ORDER BY timestamp DESC LIMIT 200", ("open",))
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    result = build_digest(rows, now)
    if result is None:
        print("[digest] no open feedback; nothing to send", flush=True)
        return 0
    subject, body = result
    _send(subject, body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
