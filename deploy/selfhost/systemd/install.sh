#!/usr/bin/env bash
# Install (or refresh) the systemd units that replace the AWS
# EventBridge schedules and Fargate's implicit "keep it running".
#
# The .service files in this directory are TEMPLATES carrying
# @@WORKDIR@@ / @@USER@@ placeholders.  This script substitutes the
# real checkout path and the account that owns it, then writes the
# result to /etc/systemd/system.  That is what lets the same repo serve
# a workstation checkout under ~/ and a server checkout under
# /opt/khayyam-math without editing anything by hand.
#
# Units are written (not symlinked) so a `git checkout` of another
# branch can't silently change what root executes.
#
# Usage:  sudo systemd/install.sh
#         sudo systemd/install.sh --uninstall
#         sudo SERVICE_USER=khayyam systemd/install.sh   # override owner
set -euo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
WORKDIR="$(dirname "$HERE")"
UNIT_DIR=/etc/systemd/system

SERVICES=(khayyam-math khayyam-probe khayyam-digest khayyam-backup)
TIMERS=(khayyam-probe.timer khayyam-digest.timer khayyam-backup.timer)

if [ "$(id -u)" -ne 0 ]; then
    echo "This script writes to $UNIT_DIR — re-run with sudo." >&2
    exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
    echo "Removing Khayyam Math units…"
    for t in "${TIMERS[@]}"; do
        systemctl disable --now "$t" 2>/dev/null || true
    done
    systemctl disable --now khayyam-math.service 2>/dev/null || true
    for s in "${SERVICES[@]}"; do rm -f "$UNIT_DIR/$s.service"; done
    for t in "${TIMERS[@]}";   do rm -f "$UNIT_DIR/$t"; done
    systemctl daemon-reload
    echo "✅ Removed.  The containers themselves are untouched; stop them"
    echo "   with: cd $WORKDIR && docker compose down"
    exit 0
fi

# Who should own the containers?  Default to the user who invoked sudo
# rather than root — the compose stack does not need root, and running
# it as root would put root-owned files in the bind-mounted repo.
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-root}}"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "❌ User '$SERVICE_USER' does not exist." >&2
    echo "   Set SERVICE_USER=<name> or create the account first." >&2
    exit 1
fi
if ! id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx docker; then
    echo "❌ User '$SERVICE_USER' is not in the 'docker' group, so the units" >&2
    echo "   would fail at runtime with a permission error on the socket." >&2
    echo "   Fix with:  usermod -aG docker $SERVICE_USER" >&2
    exit 1
fi
if [ ! -f "$WORKDIR/.env" ]; then
    echo "❌ No .env at $WORKDIR/.env — the stack cannot start without it." >&2
    echo "   cp $WORKDIR/env.example $WORKDIR/.env and fill it in first." >&2
    exit 1
fi

echo "Installing units into $UNIT_DIR"
echo "  WorkingDirectory = $WORKDIR"
echo "  User             = $SERVICE_USER"
echo

for s in "${SERVICES[@]}"; do
    sed -e "s|@@WORKDIR@@|$WORKDIR|g" \
        -e "s|@@USER@@|$SERVICE_USER|g" \
        "$HERE/$s.service" > "$UNIT_DIR/$s.service"
    chmod 0644 "$UNIT_DIR/$s.service"
    echo "  $s.service"
done
for t in "${TIMERS[@]}"; do
    install -m 0644 "$HERE/$t" "$UNIT_DIR/$t"
    echo "  $t"
done

# A leftover placeholder means a template gained a new one that this
# script does not know how to fill — fail loudly rather than install a
# unit that will not start.
if grep -l '@@' "$UNIT_DIR"/khayyam-*.service 2>/dev/null | grep -q .; then
    echo "❌ Unsubstituted @@PLACEHOLDER@@ left in an installed unit:" >&2
    grep -n '@@' "$UNIT_DIR"/khayyam-*.service >&2
    exit 1
fi

systemctl daemon-reload
systemctl enable khayyam-math.service
for t in "${TIMERS[@]}"; do
    systemctl enable --now "$t"
done

echo
echo "✅ Installed.  Current schedule:"
systemctl list-timers 'khayyam-*' --no-pager || true
echo
echo "The stack itself is NOT started by this script.  Start it with:"
echo "  systemctl start khayyam-math.service"
