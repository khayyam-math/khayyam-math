#!/usr/bin/env bash
# Pre-deploy verifier — runs the ACTUAL prod container against a
# REAL Postgres locally, hits every critical endpoint, and runs a
# mini audit before allowing the production deploy to proceed.
#
# What it catches that "pytest" alone misses:
#   * SQL-syntax / schema bugs that only Postgres trips on (SQLite
#     is more permissive — the executescript ';-in-comment' bug
#     would have been caught here).
#   * Container-start crashes (missing env var, broken import).
#   * Endpoints that 500 on first call (telemetry init crash, etc).
#   * Auth flow regressions (login page renders, admin endpoint
#     returns 404 to anonymous callers).
#   * The /health endpoint actually responding.
#
# Exits 0 on success, non-zero on any failure. deploy.sh refuses to
# proceed if this returns non-zero.
#
# Usage:
#   infra/verify_before_deploy.sh
#
# Skip the verifier (emergency hotfix only):
#   SEVIM_SKIP_VERIFIER=1 ./infra/deploy.sh
#
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."   # repo root

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[0;33m"
RESET="\033[0m"

ok()    { printf "${GREEN}✓${RESET} %s\n" "$*"; }
fail()  { printf "${RED}✗${RESET} %s\n" "$*"; exit 1; }
info()  { printf "${YELLOW}…${RESET} %s\n" "$*"; }

PG_NAME="khayyam-verify-pg-$$"
APP_NAME="khayyam-verify-app-$$"
APP_PORT=18080
PG_PORT=15432
NETWORK="khayyam-verify-net-$$"

cleanup() {
    info "cleanup: stopping containers"
    docker rm -f "$APP_NAME" "$PG_NAME" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

info "Stage 1/5 — building prod container image"
IMG_TAG="khayyam-verify:$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
if ! docker build -t "$IMG_TAG" \
       --build-arg "SEVIM_GIT_SHA=$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)" \
       . >/tmp/verify-build.log 2>&1; then
    tail -30 /tmp/verify-build.log
    fail "docker build failed (full log: /tmp/verify-build.log)"
fi
ok "container built: $IMG_TAG"

info "Stage 2/5 — starting Postgres 16 + running schema migration"
docker network create "$NETWORK" >/dev/null
docker run -d --name "$PG_NAME" --network "$NETWORK" \
    -e POSTGRES_PASSWORD=verifypass -e POSTGRES_DB=sevim \
    -p "${PG_PORT}:5432" postgres:16-alpine >/dev/null
# Wait for PG to be ready
for _ in $(seq 1 30); do
    if docker exec "$PG_NAME" pg_isready -U postgres >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! docker exec "$PG_NAME" pg_isready -U postgres >/dev/null 2>&1; then
    docker logs "$PG_NAME" | tail -20
    fail "Postgres failed to start"
fi
ok "Postgres up on port $PG_PORT"

info "Stage 3/5 — booting app container against the real Postgres"
DB_JSON="{\"username\":\"postgres\",\"password\":\"verifypass\",\"host\":\"$PG_NAME\",\"port\":5432}"
docker run -d --name "$APP_NAME" --network "$NETWORK" \
    -p "${APP_PORT}:8080" \
    -e OPENAI_API_KEY="${OPENAI_API_KEY:-sk-fake-verifier-key}" \
    -e SEVIM_AUTH_SECRET="verifier-secret-not-prod" \
    -e SEVIM_IP_HASH_SALT="verifier-salt-not-prod" \
    -e SEVIM_TELEMETRY=1 \
    -e SEVIM_DB_SECRET_JSON="$DB_JSON" \
    -e SEVIM_AUTH_REQUIRED=0 \
    -e SEVIM_NO_BROWSER=1 \
    -e SEVIM_RATE_LIMIT=0 \
    -e SEVIM_COST_GUARD=0 \
    -e SEVIM_ADMIN_EMAILS="verifier@example.com" \
    "$IMG_TAG" >/dev/null

# Wait for app to be responsive (or fail early on crash).
for i in $(seq 1 60); do
    if curl -s -o /dev/null -m 2 "http://127.0.0.1:${APP_PORT}/health"; then
        break
    fi
    if ! docker ps --filter "name=$APP_NAME" --filter "status=running" -q | grep -q .; then
        echo "--- app container died during startup ---"
        docker logs "$APP_NAME" | tail -40
        fail "app container crashed within ${i}s of startup (most likely a schema or import error)"
    fi
    sleep 1
done

# One final health check that MUST return 200
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${APP_PORT}/health")
if [ "$HEALTH" != "200" ]; then
    docker logs "$APP_NAME" | tail -40
    fail "/health returned $HEALTH (expected 200)"
fi
ok "app container healthy on port $APP_PORT"

info "Stage 4/5 — endpoint smoke tests (this is where the SQL-init crash from earlier sessions would surface)"

# /health
if [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${APP_PORT}/health")" = "200" ]; then
    ok "/health → 200"
else
    fail "/health did NOT return 200"
fi

# /studio/health
if [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${APP_PORT}/studio/health")" = "200" ]; then
    ok "/studio/health → 200"
else
    fail "/studio/health did NOT return 200"
fi

# /studio/auth/login should render the new-copy page (we test 'Email me a link' text)
LOGIN_BODY=$(curl -s "http://127.0.0.1:${APP_PORT}/studio/auth/login")
if printf '%s' "$LOGIN_BODY" | grep -q "Email me a link"; then
    ok "/studio/auth/login renders the expected copy"
else
    echo "$LOGIN_BODY" | head -5
    fail "/studio/auth/login response missing 'Email me a link' marker"
fi

# /studio/admin/users-summary anonymous → MUST be 404 (not 401, not 500)
ADMIN_ANON=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${APP_PORT}/studio/admin/users-summary")
if [ "$ADMIN_ANON" = "404" ]; then
    ok "/studio/admin/users-summary → 404 to anonymous (correct)"
else
    fail "/studio/admin/users-summary anonymous → $ADMIN_ANON (expected 404; 500 would indicate telemetry init crash)"
fi

# /studio/admin/users-summary as admin → 200 with valid JSON
# (proves telemetry init + a real Postgres roundtrip work end-to-end)
COOKIE=$(SEVIM_AUTH_SECRET="verifier-secret-not-prod" .venv/bin/python -c "from studio.auth import sign; print(sign({'sub': 'verifier@example.com'}, ttl_s=120))")
ADMIN_BODY=$(curl -s -H "Cookie: sevim_auth=$COOKIE" "http://127.0.0.1:${APP_PORT}/studio/admin/users-summary")
if printf '%s' "$ADMIN_BODY" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d.get('available') is True; assert 'distinct_emails' in d" 2>/dev/null; then
    ok "/studio/admin/users-summary as admin → valid JSON (telemetry init + Postgres query work)"
else
    echo "  body: $ADMIN_BODY"
    docker logs "$APP_NAME" | tail -30
    fail "/studio/admin/users-summary as admin did NOT return the expected schema"
fi

# Landing page renders + has the IJAIED-era copy
LANDING=$(curl -s "http://127.0.0.1:${APP_PORT}/")
if printf '%s' "$LANDING" | grep -q "Khayyam Math"; then
    ok "/ landing renders"
else
    fail "/ landing missing 'Khayyam Math' marker"
fi

# Contact page (the cookie-banner-was-broken regression)
CONTACT=$(curl -s "http://127.0.0.1:${APP_PORT}/contact")
if printf '%s' "$CONTACT" | grep -q "Open Studio"; then
    ok "/contact has 'Open Studio' nav (no cookie-banner regression)"
else
    fail "/contact missing 'Open Studio' marker"
fi

info "Stage 5/5 — quick Postgres-table audit (proves all tables were created + are queryable)"
for table in sessions turns canvases users feedback repairs settings lean_verifications; do
    n=$(docker exec "$PG_NAME" psql -U postgres -d sevim -t -A \
        -c "SELECT COUNT(*) FROM $table" 2>&1 | tr -d ' ' || echo "ERROR")
    if [ "$n" = "ERROR" ] || ! [[ "$n" =~ ^[0-9]+$ ]]; then
        docker exec "$PG_NAME" psql -U postgres -d sevim -c "\d $table" 2>&1 | tail -5
        fail "table '$table' not queryable in Postgres (schema migration failed?)"
    fi
    ok "table $table queryable ($n rows)"
done

echo
printf "${GREEN}══════════════════════════════════════════════════════════${RESET}\n"
printf "${GREEN}  ✅  All pre-deploy checks passed.  Safe to deploy.${RESET}\n"
printf "${GREEN}══════════════════════════════════════════════════════════${RESET}\n"
