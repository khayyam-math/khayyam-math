"""Offline Lean + Mathlib catalog verifier (Phase C).

Reads queued claims from the ``lean_verifications`` table, formalises
each in Lean 4 + Mathlib via ``lean_translator``, runs ``lake build``
under a prepared Lake project (``lean_catalog/``), and records the
result.

DESIGN:
  * Runs OFFLINE — never in the production Fargate container.  Mathlib
    is ~3 GB, take 30+ s to import.  Suitable as a nightly batch job
    on a dev box or sidecar EC2.
  * Failure is INFORMATIONAL, not a kill switch.  Per the user's
    policy ("tag privately for review; keep showing publicly"), the
    figure stays in production regardless; admins triage failures
    via /studio/admin/lean.

USAGE:
    # One-time setup (after `lake build` has been run in
    # lean_catalog/ — see lean_catalog/README.md):
    python -m studio.catalog_verifier --since 7d
    python -m studio.catalog_verifier --canvas-id express_<id>
    python -m studio.catalog_verifier --requeue-failed --since 30d

Each run:
  1. Selects queued (or --requeue-failed) rows from lean_verifications.
  2. Translates each claim via lean_translator.translate_claim.
  3. Writes the candidate proof into lean_catalog/Catalog/Probe.lean.
  4. Runs `lake env lean Catalog/Probe.lean` with each candidate tactic.
  5. Records status + lean_output back into the database.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

REPO = Path(__file__).resolve().parent.parent
LAKE_PROJECT = REPO / "lean_catalog"


# ──────────────────────────────────────────────────────────────────────
# DB access
# ──────────────────────────────────────────────────────────────────────
def _connect():
    """Get a telemetry connection.  Reuses sevim/telemetry.py's
    handles so SQLite (dev) and Postgres (prod) both work."""
    from sevim.telemetry import get_telemetry
    tel = get_telemetry()
    if tel is None:
        raise RuntimeError(
            "telemetry not configured — set SEVIM_TELEMETRY_DB.")
    return tel


def _select_queued(tel, since_seconds: Optional[float],
                   canvas_id: Optional[str],
                   requeue_failed: bool,
                   limit: int) -> list[tuple]:
    """Return (canvas_id, claim_idx, a, b, kind) rows to process."""
    if canvas_id:
        sql = (
            "SELECT canvas_id, claim_idx, claim_a, claim_b, claim_kind "
            "FROM lean_verifications "
            "WHERE canvas_id = ? "
            "ORDER BY claim_idx "
            "LIMIT ?"
        )
        return list(tel.query(sql, (canvas_id, limit)))

    statuses = "('queued')"
    if requeue_failed:
        statuses = "('queued', 'failed', 'timeout')"
    where = f"v.status IN {statuses}"
    args: tuple = ()
    if since_seconds is not None:
        # Join to canvases to use canvas timestamp; queued rows have
        # no verified_at.
        where += " AND c.timestamp >= ?"
        args = (time.time() - since_seconds,)
    sql = (
        "SELECT v.canvas_id, v.claim_idx, v.claim_a, v.claim_b, v.claim_kind "
        "FROM lean_verifications v "
        "JOIN canvases c ON c.canvas_id = v.canvas_id "
        f"WHERE {where} "
        "ORDER BY c.timestamp DESC "
        "LIMIT ?"
    )
    return list(tel.query(sql, args + (limit,)))


def _record_outcome(tel, canvas_id: str, claim_idx: int, *,
                    status: str, source: str, output: str,
                    duration_s: float) -> None:
    tel._exec(
        """
        UPDATE lean_verifications
        SET status = ?, lean_source = ?, lean_output = ?,
            duration_s = ?, verified_at = ?
        WHERE canvas_id = ? AND claim_idx = ?
        """,
        (status, source, output[:4000], duration_s, time.time(),
         canvas_id, claim_idx),
    )


# ──────────────────────────────────────────────────────────────────────
# Lake / Lean execution
# ──────────────────────────────────────────────────────────────────────
def _check_lake_project() -> Optional[str]:
    """Return None if the lake project is ready, or an error string."""
    if not LAKE_PROJECT.exists():
        return (f"lake project missing at {LAKE_PROJECT} — see "
                f"{LAKE_PROJECT}/README.md for one-time setup.")
    build_dir = LAKE_PROJECT / ".lake"
    if not build_dir.exists():
        return (f"lake build artefacts missing — run `cd "
                f"{LAKE_PROJECT} && lake update && lake build`")
    return None


def _try_proof(probe_file: Path, lake_dir: Path,
               timeout_s: float = 60.0) -> tuple[bool, str]:
    """Run `lake env lean Catalog/Probe.lean` under the lake project.
    Returns (ok, output)."""
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", "Catalog/Probe.lean"],
            cwd=lake_dir, capture_output=True, text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except FileNotFoundError:
        return False, "LAKE_BINARY_NOT_FOUND"
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0 and "error" not in output.lower(), output


# ──────────────────────────────────────────────────────────────────────
# Main driver
# ──────────────────────────────────────────────────────────────────────
def verify_one(tel, canvas_id: str, claim_idx: int,
               a_text: str, b_text: str, kind: str) -> str:
    from studio.templates.lean_translator import translate_claim
    proof = translate_claim({
        "a": a_text, "b": b_text, "kind": kind,
    })
    if proof is None:
        _record_outcome(
            tel, canvas_id, claim_idx, status="unsupported",
            source="", output="translator could not formalise",
            duration_s=0.0,
        )
        return "unsupported"

    t0 = time.monotonic()
    probe = LAKE_PROJECT / "Catalog" / "Probe.lean"
    probe.parent.mkdir(parents=True, exist_ok=True)
    last_output = ""
    for tactic in proof.tactics:
        src = proof.render(tactic)
        probe.write_text(src)
        ok, output = _try_proof(probe, LAKE_PROJECT)
        last_output = output
        if ok:
            _record_outcome(
                tel, canvas_id, claim_idx, status="verified",
                source=src, output=f"tactic={tactic!r}",
                duration_s=time.monotonic() - t0,
            )
            return "verified"
    # All tactics exhausted.
    final = ("timeout" if last_output == "TIMEOUT" else "failed")
    _record_outcome(
        tel, canvas_id, claim_idx, status=final,
        source=src, output=last_output[:4000],
        duration_s=time.monotonic() - t0,
    )
    return final


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline Lean+Mathlib catalog verifier",
    )
    parser.add_argument("--since", default=None,
                        help="Process claims from canvases newer than "
                             "this duration (e.g. 7d, 24h, 30m).")
    parser.add_argument("--canvas-id", default=None,
                        help="Process all claims for a single canvas.")
    parser.add_argument("--requeue-failed", action="store_true",
                        help="Retry rows with status=failed/timeout.")
    parser.add_argument("--limit", type=int, default=200,
                        help="Maximum claims per run (default 200).")
    args = parser.parse_args(list(argv) if argv else None)

    err = _check_lake_project()
    if err:
        print(f"[catalog] {err}", file=sys.stderr)
        return 2

    tel = _connect()
    since: Optional[float] = None
    if args.since:
        unit = args.since[-1]
        try:
            n = float(args.since[:-1])
        except ValueError:
            print(f"[catalog] invalid --since: {args.since!r}")
            return 1
        since = n * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]

    rows = _select_queued(tel, since, args.canvas_id,
                          args.requeue_failed, args.limit)
    if not rows:
        print("[catalog] nothing to process.")
        return 0

    print(f"[catalog] processing {len(rows)} claim(s)…")
    counts: dict[str, int] = {}
    for cid, idx, a, b, kind in rows:
        status = verify_one(tel, cid, idx, a or "", b or "", kind or "")
        counts[status] = counts.get(status, 0) + 1
        glyph = {"verified": "✓", "failed": "✗",
                 "unsupported": "·", "timeout": "T"}.get(status, "?")
        print(f"  {glyph} {cid[:24]} #{idx}  {status}  "
              f"a={(a or '')[:30]!r}  b={(b or '')[:20]!r}")
    print(f"[catalog] done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
