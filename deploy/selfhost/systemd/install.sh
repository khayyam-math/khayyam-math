#!/usr/bin/env bash
# Install (or refresh) the systemd units that replace the AWS
# EventBridge schedules and Fargate's implicit "keep it running".
#
# Units are copied rather than symlinked so a `git checkout` of another
# branch can't silently change what root executes.
#
# Usage:  sudo deploy/selfhost/systemd/install.sh
#         sudo deploy/selfhost/systemd/install.sh --uninstall
set -euo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
UNIT_DIR=/etc/systemd/system
UNITS=(
    khayyam-math.service
    khayyam-probe.service   khayyam-probe.timer
    khayyam-digest.service  khayyam-digest.timer
    khayyam-backup.service  khayyam-backup.timer
)
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
    for u in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$u"; done
    systemctl daemon-reload
    echo "✅ Removed.  The containers themselves are untouched; stop them"
    echo "   with: cd deploy/selfhost && docker compose down"
    exit 0
fi

# The units hardcode WorkingDirectory and User.  A clone in a different
# path or a different operator account would fail at runtime with a
# confusing error, so catch it here instead.
EXPECTED_DIR="$(dirname "$HERE")"
if ! grep -q "WorkingDirectory=$EXPECTED_DIR" "$HERE/khayyam-math.service"; then
    echo "⚠️  The unit files point at a different directory than this checkout:"
    grep -h '^WorkingDirectory=' "$HERE"/*.service | sort -u
    echo "   This checkout is at: $EXPECTED_DIR"
    echo "   Edit the WorkingDirectory / ExecStart lines (and User=) before installing."
    exit 1
fi

echo "Installing units into $UNIT_DIR…"
for u in "${UNITS[@]}"; do
    install -m 0644 "$HERE/$u" "$UNIT_DIR/$u"
    echo "  $u"
done

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
echo "  sudo systemctl start khayyam-math.service"
