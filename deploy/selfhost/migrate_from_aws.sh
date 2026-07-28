#!/usr/bin/env bash
# One-way data copy: AWS production → this self-hosted stack.
#
# READ-ONLY on the AWS side.  Nothing here deletes, modifies, or
# destroys any AWS resource — the live Fargate service keeps serving
# khayyammath.com throughout, so a failed migration costs nothing but
# the time to re-run it.  Teardown is a separate, manual step you run
# only after verifying the self-hosted site (see README.md §6).
#
# Steps (each independently runnable):
#   --secrets    Secrets Manager  → deploy/selfhost/.env
#   --db         RDS PostgreSQL   → the local `db` container
#   --canvases   S3 canvas bucket → the local `canvases` volume
#   --all        all three, in that order
#
# Requirements:
#   * AWS_PROFILE (or ambient credentials) with read access to
#     Secrets Manager, RDS, and S3 in the production account.  The
#     default `polly` profile in ~/.aws is NOT sufficient — it has no
#     rds:/s3:/secretsmanager: permissions.
#   * The local stack already up:  docker compose up -d db
#
# Usage:
#   AWS_PROFILE=sevim-deploy ./migrate_from_aws.sh --secrets
#   AWS_PROFILE=sevim-deploy ./migrate_from_aws.sh --all
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

STACK_NAME="${SEVIM_STACK_NAME:-SevimStack}"
REGION="${CDK_DEFAULT_REGION:-us-east-1}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

DO_SECRETS=0; DO_DB=0; DO_CANVASES=0
for arg in "$@"; do
    case "$arg" in
        --secrets)   DO_SECRETS=1 ;;
        --db)        DO_DB=1 ;;
        --canvases)  DO_CANVASES=1 ;;
        --all)       DO_SECRETS=1; DO_DB=1; DO_CANVASES=1 ;;
        *) echo "Unknown option: $arg" >&2
           sed -n '2,30p' "$0" >&2
           exit 1 ;;
    esac
done
if [ $((DO_SECRETS + DO_DB + DO_CANVASES)) -eq 0 ]; then
    sed -n '2,30p' "$0" >&2
    exit 1
fi

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }

echo "[migrate] account: $(aws sts get-caller-identity --query Account --output text)"
echo "[migrate] region:  $REGION"
echo


# ─────────────────────────────────────────────────────────────────────
# Secrets Manager → .env
# ─────────────────────────────────────────────────────────────────────
if [ "$DO_SECRETS" = 1 ]; then
    echo "[migrate] ── secrets ──────────────────────────────────────"
    [ -f .env ] || cp env.example .env
    chmod 600 .env

    # Replace (or append) a KEY=VALUE line in .env without disturbing
    # the comments that explain each setting.
    set_env() {
        local key="$1" val="$2"
        [ -z "$val" ] && return 0
        if grep -qE "^${key}=" .env; then
            # Values can contain / and & — use python for the rewrite
            # rather than fighting sed's delimiter and escape rules.
            python3 - "$key" "$val" <<'PY'
import sys, pathlib
key, val = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env")
lines = p.read_text().splitlines()
out = [f"{key}={val}" if l.startswith(f"{key}=") else l for l in lines]
p.write_text("\n".join(out) + "\n")
PY
        else
            printf '%s=%s\n' "$key" "$val" >> .env
        fi
        echo "  set $key"
    }

    fetch() {  # secret-id → plaintext, or "" when absent
        aws secretsmanager get-secret-value --secret-id "$1" \
            --query SecretString --output text 2>/dev/null || echo ""
    }

    # Carrying these two across is what keeps existing users signed in
    # and keeps telemetry IP hashes comparable to historical rows.
    # Generating fresh ones instead silently breaks both.
    set_env SEVIM_AUTH_SECRET   "$(fetch sevim/auth_secret)"
    set_env SEVIM_IP_HASH_SALT  "$(fetch sevim/ip_hash_salt)"
    set_env OPENAI_API_KEY      "$(fetch sevim/openai)"
    ses_from="$(fetch sevim/ses_from)"
    set_env SEVIM_MAIL_FROM     "$ses_from"

    echo "[migrate] ✅ secrets copied into .env"
    echo "[migrate] Still to fill in by hand: CF_TUNNEL_TOKEN,"
    echo "[migrate] POSTGRES_PASSWORD, SEVIM_SMTP_USER, SEVIM_SMTP_PASSWORD."
    echo
fi


# Everything below needs the local .env.
[ -f .env ] || { echo "❌ no .env — run with --secrets first" >&2; exit 1; }
set -a; . ./.env; set +a


# ─────────────────────────────────────────────────────────────────────
# RDS → local Postgres
# ─────────────────────────────────────────────────────────────────────
if [ "$DO_DB" = 1 ]; then
    echo "[migrate] ── database ─────────────────────────────────────"

    RDS_JSON="$(aws secretsmanager get-secret-value \
        --secret-id "$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
            --query "Stacks[0].Outputs[?OutputKey=='DbSecretArn'].OutputValue" \
            --output text)" \
        --query SecretString --output text)"

    RDS_HOST=$(echo "$RDS_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["host"])')
    RDS_PORT=$(echo "$RDS_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("port",5432))')
    RDS_USER=$(echo "$RDS_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["username"])')
    RDS_PASS=$(echo "$RDS_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
    RDS_DB=$(echo   "$RDS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("dbname") or d.get("database") or "sevim")')

    echo "[migrate] source: $RDS_USER@$RDS_HOST:$RDS_PORT/$RDS_DB"

    # RDS sits in a private subnet with a security group that only
    # admits the Fargate tasks, so pg_dump from this laptop will hang
    # unless you have already opened a path (VPN, bastion + SSH tunnel,
    # or a temporary SG rule for your public IP).  Fail fast and say so
    # rather than sitting on a TCP connect for two minutes.
    if ! timeout 10 bash -c "</dev/tcp/$RDS_HOST/$RDS_PORT" 2>/dev/null; then
        cat >&2 <<EOF
[migrate] ❌ Cannot reach $RDS_HOST:$RDS_PORT from here.

The RDS instance is not publicly reachable by design.  Pick one:

  (a) Temporarily allow your current IP.  Find the DB security group,
      add an inbound rule for TCP $RDS_PORT from $(curl -s -m 5 https://api.ipify.org || echo YOUR.IP)/32,
      re-run this script, then REMOVE the rule immediately afterwards.

  (b) Tunnel through an existing bastion:
        ssh -N -L 15432:$RDS_HOST:$RDS_PORT <bastion>
      then re-run with:  RDS_HOST_OVERRIDE=127.0.0.1 RDS_PORT_OVERRIDE=15432

  (c) Run pg_dump from inside the VPC (an ECS exec session or a
      throwaway EC2 instance) and copy the dump file here, then:
        gunzip -c dump.sql.gz | docker compose exec -T db psql -U \$POSTGRES_USER -d \$POSTGRES_DB
EOF
        exit 3
    fi

    DUMP="/tmp/khayyam-rds-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
    echo "[migrate] dumping → $DUMP  (this can take a few minutes)"
    # pg_dump runs inside a postgres:16 container so the host needs no
    # client install and the version matches the server.
    docker run --rm -e PGPASSWORD="$RDS_PASS" postgres:16-alpine \
        pg_dump -h "${RDS_HOST_OVERRIDE:-$RDS_HOST}" \
                -p "${RDS_PORT_OVERRIDE:-$RDS_PORT}" \
                -U "$RDS_USER" -d "$RDS_DB" \
                --no-owner --no-acl \
      | gzip -9 > "$DUMP"

    gzip -t "$DUMP" || { echo "[migrate] ❌ dump is corrupt" >&2; exit 3; }
    echo "[migrate] dump size: $(numfmt --to=iec "$(stat -c%s "$DUMP")")"

    # Restore into the local container.  The schema is CREATE TABLE IF
    # NOT EXISTS everywhere, so restoring over a freshly-booted (already
    # self-initialised) database is safe.
    echo "[migrate] restoring into the local db container…"
    gunzip -c "$DUMP" | docker compose exec -T db \
        psql -v ON_ERROR_STOP=0 -U "${POSTGRES_USER:-sevim}" -d "${POSTGRES_DB:-sevim}" \
        > /tmp/khayyam-restore.log 2>&1 || true

    echo "[migrate] row counts after restore:"
    docker compose exec -T db psql -qtA -U "${POSTGRES_USER:-sevim}" -d "${POSTGRES_DB:-sevim}" -c "
        SELECT 'users='      || (SELECT count(*) FROM users)
            || ' sessions='  || (SELECT count(*) FROM sessions)
            || ' turns='     || (SELECT count(*) FROM turns)
            || ' canvases='  || (SELECT count(*) FROM canvases)
            || ' feedback='  || (SELECT count(*) FROM feedback);" 2>&1 | sed 's/^/  /'

    echo "[migrate] restore log: /tmp/khayyam-restore.log  (dump kept at $DUMP)"
    echo
fi


# ─────────────────────────────────────────────────────────────────────
# S3 canvas bucket → local volume
# ─────────────────────────────────────────────────────────────────────
if [ "$DO_CANVASES" = 1 ]; then
    echo "[migrate] ── canvases ─────────────────────────────────────"

    BUCKET="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='CanvasBucketName'].OutputValue" \
        --output text)"
    if [ -z "$BUCKET" ] || [ "$BUCKET" = "None" ]; then
        echo "[migrate] ❌ could not resolve CanvasBucketName from $STACK_NAME" >&2
        exit 4
    fi
    echo "[migrate] source: s3://$BUCKET"

    STAGE="$(mktemp -d /tmp/khayyam-canvases-XXXX)"
    aws s3 sync "s3://$BUCKET" "$STAGE" --only-show-errors
    echo "[migrate] pulled $(find "$STAGE" -type f | wc -l) objects "\
"($(du -sh "$STAGE" | cut -f1))"

    # Copy into the named volume through a throwaway container — the
    # volume has no host path we should be poking at directly.
    VOLUME="$(docker volume ls -q --filter name=khayyam-math_canvases | head -1)"
    VOLUME="${VOLUME:-khayyam-math_canvases}"
    docker run --rm -v "${VOLUME}:/dest" -v "$STAGE:/src:ro" \
        alpine:3 sh -c 'cp -a /src/. /dest/ && chown -R 1001:1001 /dest'

    echo "[migrate] volume now holds $(docker run --rm -v "${VOLUME}:/d:ro" alpine:3 \
        sh -c 'find /d -type f | wc -l') files"
    rm -rf "$STAGE"
    echo
fi

echo "[migrate] ✅ done.  AWS was not modified — the Fargate service is"
echo "[migrate]    still serving production.  Verify locally, then cut"
echo "[migrate]    DNS over (README.md §5) before considering teardown."
