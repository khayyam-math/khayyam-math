#!/usr/bin/env bash
# Refresh infra/geolite/GeoLite2-City.mmdb from MaxMind.
#
# Credentials precedence:
#   1. AWS Secrets Manager secret  sevim/maxmind_key  — JSON shape:
#        { "account_id": "1234567", "license_key": "abc..." }
#      Pulled when AWS_PROFILE is set.
#   2. Env vars  MAXMIND_ACCOUNT_ID + MAXMIND_LICENSE_KEY
#      (sourced from .env for local dev; the .env file is gitignored).
#
# Idempotent: the deploy wrapper calls this every deploy so the image
# always ships with this week's mmdb.  Manual usage:
#     infra/refresh_geolite.sh
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
DEST="$(pwd)/geolite/GeoLite2-City.mmdb"
mkdir -p "$(dirname "$DEST")"

ACCOUNT_ID="${MAXMIND_ACCOUNT_ID:-}"
LICENSE_KEY="${MAXMIND_LICENSE_KEY:-}"

# Try AWS Secrets Manager first if the profile is set.
if [ -z "${LICENSE_KEY}" ] && [ -n "${AWS_PROFILE:-}" ]; then
    if command -v aws >/dev/null 2>&1; then
        json="$(aws secretsmanager get-secret-value \
                  --secret-id sevim/maxmind_key \
                  --query SecretString --output text 2>/dev/null || true)"
        if [ -n "$json" ]; then
            ACCOUNT_ID="$(printf '%s' "$json" | python3 -c \
                'import json,sys;print(json.load(sys.stdin)["account_id"])')"
            LICENSE_KEY="$(printf '%s' "$json" | python3 -c \
                'import json,sys;print(json.load(sys.stdin)["license_key"])')"
        fi
    fi
fi

# Fall back to .env for local builds.
if [ -z "${LICENSE_KEY}" ] && [ -f "../.env" ]; then
    # shellcheck disable=SC1091
    set -a; . "../.env"; set +a
    ACCOUNT_ID="${MAXMIND_ACCOUNT_ID:-$ACCOUNT_ID}"
    LICENSE_KEY="${MAXMIND_LICENSE_KEY:-$LICENSE_KEY}"
fi

if [ -z "${LICENSE_KEY}" ] || [ -z "${ACCOUNT_ID}" ]; then
    if [ -f "$DEST" ]; then
        echo "[refresh_geolite] no credentials available — keeping existing $DEST" >&2
        exit 0
    fi
    echo "[refresh_geolite] ERROR: no credentials and no existing mmdb." >&2
    echo "                  Set MAXMIND_ACCOUNT_ID + MAXMIND_LICENSE_KEY in .env" >&2
    echo "                  or create AWS secret  sevim/maxmind_key." >&2
    exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "[refresh_geolite] downloading GeoLite2-City from MaxMind..." >&2
curl -fsSL \
    -u "${ACCOUNT_ID}:${LICENSE_KEY}" \
    "https://download.maxmind.com/geoip/databases/GeoLite2-City/download?suffix=tar.gz" \
    -o "$tmp/city.tar.gz"

tar -xzf "$tmp/city.tar.gz" -C "$tmp"
found="$(find "$tmp" -name 'GeoLite2-City.mmdb' -print -quit)"
if [ -z "$found" ]; then
    echo "[refresh_geolite] ERROR: extracted tarball had no .mmdb" >&2
    exit 1
fi

mv "$found" "$DEST"
size_mb=$(( $(stat -c %s "$DEST") / 1024 / 1024 ))
echo "[refresh_geolite] installed $DEST (${size_mb} MB)" >&2
