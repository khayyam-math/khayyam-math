# Lean + Mathlib catalog verifier

This is the Lake project used by `studio/catalog_verifier.py` for the
**offline** Phase C catalog verifier.  It runs against the claims
stored in the `lean_verifications` table and writes back
`verified` / `failed` / `unsupported` / `timeout`.

## One-time setup (~45 min, ~4 GB disk)

```bash
cd lean_catalog
lake update     # downloads Mathlib (~3 GB)
lake build      # compiles Mathlib (~30 min on a decent dev box)
```

The toolchain version is pinned via `lean-toolchain`; `elan` will
auto-install matching Lean if needed.

## Running the verifier

```bash
# Verify all queued claims from canvases produced in the last week:
python -m studio.catalog_verifier --since 7d

# Verify a specific canvas only:
python -m studio.catalog_verifier --canvas-id express_abcdef…

# Retry previous failures:
python -m studio.catalog_verifier --requeue-failed --since 30d
```

Per-claim timing is roughly 1–5 s when Mathlib is precompiled.

## Failure policy

Per user preference, failed Lean verifications **do NOT** pull figures
from production.  They are visible to administrators at
`/studio/admin/lean` for triage.  Most failures are *unsupported* —
the translator can't formalise the claim — rather than *wrong*.

## Architecture note

This service runs OFFLINE — outside the Fargate container — because
Mathlib is too large (~3 GB) and slow (~30 s import) to ship with the
runtime image.  The runtime image only carries Lean 4 *core* (no
Mathlib), used for closed-Nat-arithmetic decisions by
`studio/templates/lean_verifier.py`.
