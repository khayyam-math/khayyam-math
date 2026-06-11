#!/usr/bin/env python3
"""Offline taxonomy curation pass: find gaps → cluster → propose candidates.

Run periodically (cron) or manually; the admin then reviews candidates at
/studio/admin/taxonomy and approves/rejects them.

    OPENAI_API_KEY=... SEVIM_DB_URL=... python scripts/curate_taxonomy.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sevim import embeddings as emb       # noqa: E402
from sevim.telemetry import get_telemetry  # noqa: E402
from studio import curation                # noqa: E402
from studio.taxonomy import get_taxonomy   # noqa: E402


def main() -> int:
    if not emb.available():
        print("No embedding API key.", file=sys.stderr)
        return 2
    tel = get_telemetry()
    if tel is None:
        print("Telemetry not configured (SEVIM_DB_URL).", file=sys.stderr)
        return 2
    tax = get_taxonomy()
    tax.load()
    gaps = curation.find_gaps(tel, tax)
    clusters = curation.cluster_gaps(gaps)
    created = curation.propose(tel, tax, clusters)
    dups = curation.dedup_templates(tel)
    migs = curation.suggest_migrations(tel, tax)
    print(f"gaps={len(gaps)} clusters={len(clusters)} "
          f"candidates_created={len(created)} "
          f"duplicate_flags={len(dups)} migration_suggestions={len(migs)}")
    for c in created:
        print(f"  candidate: {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
