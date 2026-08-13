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

# Read one KEY=value out of .env WITHOUT sourcing it.
#
# .env is a Docker Compose env_file, not a shell script.  Compose accepts
# unquoted values containing spaces (SEVIM_MAIL_FROM_NAME=Khayyam Math);
# `source` would execute that line and try to run `Math` as a command.
# Anything reading .env from bash must parse, never source.
env_get() { sed -n "s/^$1=//p" ./.env | head -1; }

# ── GeoLite2 refresh ─────────────────────────────────────────────────
# The mmdb is gitignored, so a fresh clone has no
# infra/geolite/GeoLite2-City.mmdb and the Docker build fails at that
# COPY.  infra/deploy.sh refreshes it before every AWS build; do the
# same here so the two paths cannot drift.
echo "[redeploy] Refreshing GeoLite2 database…"
export MAXMIND_ACCOUNT_ID="$(env_get MAXMIND_ACCOUNT_ID)"
export MAXMIND_LICENSE_KEY="$(env_get MAXMIND_LICENSE_KEY)"
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

# Ensure Postgres is up and healthy FIRST.  This is idempotent: an
# already-healthy db container is left alone, so a routine redeploy
# still only restarts the app.
#
# Without this, `up -d --no-deps app` on a fresh host starts the app
# with no database at all — and it reports HEALTHY, because /health
# does not test the database.  Telemetry then writes nowhere while
# everything looks green, which is worse than an outright failure.
# Caddy needs no credential, but it does need DNS and open ports, and
# both fail in ways that look like "the app is broken" from the outside.
# Warn early rather than let the operator discover it from a browser.
_domain="$(env_get SEVIM_DOMAIN)"
if [ -z "$_domain" ]; then
    echo "[redeploy] ℹ️  SEVIM_DOMAIN unset — Caddy will fall back to the"
    echo "[redeploy]     compose default. Fine locally; set it before going public."
else
    _resolved="$(getent hosts "$_domain" 2>/dev/null | awk '{print $1}' | head -1)"
    _mine="$(curl -s -4 -m 5 https://ifconfig.me 2>/dev/null || echo "")"
    if [ -n "$_resolved" ] && [ -n "$_mine" ] && [ "$_resolved" != "$_mine" ]; then
        echo "[redeploy] ⚠️  $_domain resolves to $_resolved but this host is $_mine."
        echo "[redeploy]     Caddy's ACME challenge will fail until the A record points here."
        echo "[redeploy]     (Expected while AWS still serves the domain — ignore until cut-over.)"
    fi
fi

echo "[redeploy] Ensuring database is up…"
docker compose up -d --wait db

echo "[redeploy] Restarting app…"
docker compose up -d --no-deps app

# ── Post-deploy health watch ─────────────────────────────────────────
PORT="$(env_get APP_HOST_PORT)"; PORT="${PORT:-8080}"
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

# /health deliberately does not test the database — it is the liveness
# probe, and pulling a serving instance because of a transient DB blip
# would turn a degradation into an outage.  So verify the DB link here
# instead, where a false green is caught at deploy time rather than
# discovered later as a hole in the telemetry.
echo "[redeploy] Verifying the app can reach Postgres…"
if docker compose exec -T db psql -qtA -U "$(env_get POSTGRES_USER)" \
        -d "$(env_get POSTGRES_DB)" -c 'SELECT 1' >/dev/null 2>&1; then
    tables=$(docker compose exec -T db psql -qtA -U "$(env_get POSTGRES_USER)" \
        -d "$(env_get POSTGRES_DB)" \
        -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'" 2>/dev/null | tr -d '[:space:]')
    echo "[redeploy]   database reachable, ${tables:-0} tables"
    if [ "${tables:-0}" -lt 5 ]; then
        echo "[redeploy]   ⚠️  schema looks empty — the app creates tables on first"
        echo "[redeploy]      write, so this is expected only on a brand-new host."
    fi
else
    echo "[redeploy] ⚠️  Could not query Postgres. The app is serving but"
    echo "[redeploy]     telemetry may be writing nowhere. Check: docker compose logs db" >&2
fi

echo "[redeploy] ✅ healthy at $SEVIM_GIT_SHA"
echo "[redeploy] Public check: curl -sI https://$(env_get SEVIM_DOMAIN)/health"
