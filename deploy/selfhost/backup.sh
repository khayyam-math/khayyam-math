#!/usr/bin/env bash
# Nightly backup — replaces RDS automated backups + S3 durability.
#
# RDS gave us point-in-time recovery and S3 gave us eleven nines for the
# canvas WAVs.  A single self-hosted box gives neither by default, so
# this script is not optional housekeeping: it is the replacement for
# two managed durability guarantees we deliberately gave up.
#
# Produces, under $BACKUP_DIR (default ~/khayyam-backups):
#   YYYY-MM-DD/db.sql.gz        — full pg_dump of the telemetry database
#   YYYY-MM-DD/canvases.tar.gz  — the canvas volume (narration WAVs, SVGs)
#   YYYY-MM-DD/env.enc          — the .env, age-encrypted, IF `age` and
#                                 BACKUP_AGE_RECIPIENT are configured
#
# Retention: keeps the last $BACKUP_KEEP_DAYS daily snapshots (default 14).
#
# Off-box copy: set BACKUP_REMOTE to an rclone remote (e.g.
# "b2:khayyam-backups") and the day's directory is synced there.  A
# backup that only exists on the machine it protects is not a backup.
#
# Usage:  ./backup.sh            (run by khayyam-backup.timer nightly)
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

BACKUP_DIR="${BACKUP_DIR:-$HOME/khayyam-backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date -u +%Y-%m-%d)"
DEST="$BACKUP_DIR/$STAMP"

if [ ! -f .env ]; then
    echo "[backup] no .env next to compose.yml — nothing to back up" >&2
    exit 1
fi
# Parse .env, never source it.  It is a Docker Compose env_file, which
# permits unquoted values containing spaces (SEVIM_MAIL_FROM_NAME=Khayyam
# Math).  `source` would execute that line and try to run `Math`.
env_get() { sed -n "s/^$1=//p" ./.env | head -1; }
POSTGRES_USER="$(env_get POSTGRES_USER)";           POSTGRES_USER="${POSTGRES_USER:-sevim}"
POSTGRES_DB="$(env_get POSTGRES_DB)";               POSTGRES_DB="${POSTGRES_DB:-sevim}"
BACKUP_AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-$(env_get BACKUP_AGE_RECIPIENT)}"
BACKUP_REMOTE="${BACKUP_REMOTE:-$(env_get BACKUP_REMOTE)}"

mkdir -p "$DEST"
chmod 700 "$BACKUP_DIR" "$DEST"

echo "[backup] $STAMP → $DEST"

# ── PostgreSQL ───────────────────────────────────────────────────────
# pg_dump runs INSIDE the db container so the host needs no postgres
# client, and the version always matches the server.
echo "[backup] dumping database…"
docker compose exec -T db \
    pg_dump -U "${POSTGRES_USER:-sevim}" -d "${POSTGRES_DB:-sevim}" \
            --clean --if-exists \
  | gzip -9 > "$DEST/db.sql.gz"

# A dump that failed halfway still produces a file, so check it is a
# valid gzip stream with plausible content before rotating older ones out.
if ! gzip -t "$DEST/db.sql.gz" 2>/dev/null; then
    echo "[backup] ❌ database dump is corrupt — keeping older backups" >&2
    exit 2
fi
size=$(stat -c%s "$DEST/db.sql.gz")
if [ "$size" -lt 1024 ]; then
    echo "[backup] ❌ database dump is only ${size} bytes — treating as failed" >&2
    exit 2
fi
echo "[backup]   db.sql.gz  $(numfmt --to=iec "$size")"

# ── Canvas volume (the S3 replacement) ───────────────────────────────
echo "[backup] archiving canvases volume…"
VOLUME="$(docker compose config --format json 2>/dev/null \
          | python3 -c "import json,sys; print(json.load(sys.stdin)['volumes']['canvases'].get('name','khayyam-math_canvases'))" \
          2>/dev/null || echo "khayyam-math_canvases")"
docker run --rm \
    -v "${VOLUME}:/data:ro" \
    -v "$DEST:/backup" \
    alpine:3 tar czf /backup/canvases.tar.gz -C /data . 2>/dev/null
echo "[backup]   canvases.tar.gz  $(numfmt --to=iec "$(stat -c%s "$DEST/canvases.tar.gz")")"

# ── Secrets ──────────────────────────────────────────────────────────
# The .env replaces Secrets Manager, so losing it loses the auth secret
# (every session invalidated) and the IP-hash salt (telemetry continuity).
# Only ever stored encrypted — a plaintext credentials file inside a
# backup archive is how credential leaks happen.
if command -v age >/dev/null 2>&1 && [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
    age -r "$BACKUP_AGE_RECIPIENT" -o "$DEST/env.enc" .env
    echo "[backup]   env.enc (age-encrypted)"
else
    echo "[backup]   ⚠️  .env NOT backed up — install \`age\` and set" >&2
    echo "[backup]      BACKUP_AGE_RECIPIENT to include it encrypted." >&2
fi

# ── Off-box copy ─────────────────────────────────────────────────────
if [ -n "${BACKUP_REMOTE:-}" ]; then
    if command -v rclone >/dev/null 2>&1; then
        echo "[backup] syncing to $BACKUP_REMOTE …"
        rclone copy "$DEST" "$BACKUP_REMOTE/$STAMP" --stats-one-line
    else
        echo "[backup] ⚠️  BACKUP_REMOTE set but rclone not installed" >&2
    fi
else
    echo "[backup] ⚠️  BACKUP_REMOTE unset — backups live only on this box." >&2
fi

# ── Retention ────────────────────────────────────────────────────────
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d \
     -name '20*-*-*' -mtime "+$BACKUP_KEEP_DAYS" \
     -exec rm -rf {} + 2>/dev/null || true

echo "[backup] ✅ done  ($(du -sh "$DEST" | cut -f1))"
