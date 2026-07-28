#!/usr/bin/env bash
# Deploy the current working tree to the self-hosted stack.
#
# The self-host counterpart of `infra/deploy.sh`.  Same posture: the
# quality gate and pre-deploy verifier run BEFORE anything reaches the
# live service, and a failed health check rolls back to the previous
# image instead of leaving the site down.
#
# Usage:
#   ./redeploy.sh                     # gate + build + restart + verify
#   SEVIM_SKIP_QUALITY_GATE=1 ./redeploy.sh   # emergency hotfix only
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
REPO_ROOT="$(cd ../.. && pwd)"

if [ ! -f .env ]; then
    echo "❌ No .env here.  cp env.example .env and fill it in." >&2
    exit 1
fi

SEVIM_GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
export SEVIM_GIT_SHA
echo "[redeploy] deploying $SEVIM_GIT_SHA"

# Make .env available to the steps below (MaxMind credentials in
# particular).  The app itself reads .env through compose's env_file;
# this is only so the build-time helpers can see it.
set -a; . ./.env; set +a

# ── GeoLite2 refresh ─────────────────────────────────────────────────
# The mmdb is gitignored, so a fresh clone has no
# infra/geolite/GeoLite2-City.mmdb and the Docker build fails at that
# COPY.  infra/deploy.sh refreshes it before every AWS build; do the
# same here so the two paths cannot drift.
echo "[redeploy] Refreshing GeoLite2 database…"
if ! "$REPO_ROOT/infra/refresh_geolite.sh"; then
    if [ -s "$REPO_ROOT/infra/geolite/GeoLite2-City.mmdb" ]; then
        echo "[redeploy] ⚠️  refresh failed — building with the existing mmdb."
    else
        echo "[redeploy] ❌ No GeoLite2 mmdb and refresh failed." >&2
        echo "[redeploy]    Set MAXMIND_ACCOUNT_ID + MAXMIND_LICENSE_KEY in .env." >&2
        exit 2
    fi
fi

# ── Quality gate ─────────────────────────────────────────────────────
# Same gate the AWS deploy runs.  A regression that would have been
# blocked from Fargate must be blocked from here too — self-hosting
# changes where the code runs, not what is allowed to ship.
#
# The gate drives real figure generation, so it needs the dev toolchain
# (uv + the project venv).  On a production server that toolchain is
# deliberately absent: the gate belongs on the machine you develop on,
# BEFORE you push, which is where infra/deploy.sh ran it too.  Skipping
# it here is therefore expected on a server and alarming on a laptop —
# hence the loud message rather than silence.
if [[ "${SEVIM_SKIP_QUALITY_GATE:-0}" == "1" ]]; then
    echo "[redeploy] ⚠️  SEVIM_SKIP_QUALITY_GATE=1 — skipping quality gate."
elif ! command -v uv >/dev/null 2>&1; then
    echo "[redeploy] ⚠️  'uv' not installed — skipping the quality gate."
    echo "[redeploy]    This build is NOT gate-verified.  Run the gate on your"
    echo "[redeploy]    dev machine before pushing:  uv run python infra/quality_gate.py"
else
    echo "[redeploy] Running quality gate…"
    if ! (cd "$REPO_ROOT" && uv run python infra/quality_gate.py); then
        echo "[redeploy] ❌ Quality gate FAILED — deploy blocked." >&2
        echo "[redeploy] Fix the regression, or SEVIM_SKIP_QUALITY_GATE=1 ./redeploy.sh" >&2
        exit 2
    fi
fi

# Remember the image currently serving traffic, so a bad build can be
# reverted without a rebuild.  This is the self-host equivalent of the
# ECS previous-task-definition rollback.
PREV_IMAGE="$(docker compose images -q app 2>/dev/null | head -1 || true)"
if [ -n "$PREV_IMAGE" ]; then
    docker tag "$PREV_IMAGE" khayyam-math-app:rollback
    echo "[redeploy] rollback image tagged: khayyam-math-app:rollback"
fi

echo "[redeploy] Building…"
docker compose build app

echo "[redeploy] Restarting app…"
docker compose up -d --no-deps app

# ── Post-deploy health watch ─────────────────────────────────────────
PORT="$(grep -E '^APP_HOST_PORT=' .env | cut -d= -f2)"
PORT="${PORT:-8080}"
URL="http://127.0.0.1:${PORT}/health"
echo "[redeploy] Probing $URL for 60 s…"
consec_fail=0
healthy=0
for _ in $(seq 1 12); do
    code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" "$URL" || echo "000")
    if [[ "$code" == "200" ]]; then
        healthy=1
        echo "[redeploy]   /health → 200"
        break
    fi
    consec_fail=$((consec_fail + 1))
    echo "[redeploy]   /health → $code  (failure $consec_fail)"
    sleep 5
done

if [ "$healthy" -ne 1 ]; then
    echo "[redeploy] 🚨 app never became healthy." >&2
    if [ -n "$PREV_IMAGE" ]; then
        echo "[redeploy] Rolling back to the previous image…" >&2
        # compose.yml pins `image: khayyam-math-app:latest`, so restoring
        # that tag and recreating the container is the whole rollback.
        docker tag khayyam-math-app:rollback khayyam-math-app:latest
        docker compose up -d --no-deps --force-recreate app
        echo "[redeploy] ⏪ Rolled back.  Inspect: docker compose logs --tail=200 app" >&2
    else
        echo "[redeploy] No previous image recorded — cannot auto-roll back." >&2
    fi
    exit 4
fi

echo "[redeploy] ✅ healthy at $SEVIM_GIT_SHA"
echo "[redeploy] Public check: curl -sI https://$(grep -E '^SEVIM_DOMAIN=' .env | cut -d= -f2)/health"
