#!/usr/bin/env bash
# Sync assistant memory between this repo and Claude's local memory dir.
#
# Claude reads/writes memory from a machine-local path derived from the repo
# location, NOT from inside the repo. This script keeps the two in sync so the
# memory travels with the repo across machines.
#
# Usage:
#   ./.claude/memory/sync-memory.sh pull    # repo  -> local  (after a git pull)
#   ./.claude/memory/sync-memory.sh push    # local -> repo   (before a git commit)
set -euo pipefail

REPO_MEM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$REPO_MEM" rev-parse --show-toplevel)"

# Claude derives the local memory dir from the repo path: '/' and '_' -> '-'.
SLUG="$(printf '%s' "$REPO_ROOT" | sed 's:[/_]:-:g')"
LOCAL_MEM="$HOME/.claude/projects/${SLUG}/memory"

case "${1:-}" in
  pull)
    mkdir -p "$LOCAL_MEM"
    rsync -a --delete --exclude 'sync-memory.sh' "$REPO_MEM"/ "$LOCAL_MEM"/
    echo "Synced repo -> $LOCAL_MEM"
    ;;
  push)
    mkdir -p "$REPO_MEM"
    rsync -a --delete --exclude 'sync-memory.sh' "$LOCAL_MEM"/ "$REPO_MEM"/
    echo "Synced $LOCAL_MEM -> repo"
    ;;
  *)
    echo "usage: $0 {pull|push}" >&2
    exit 1
    ;;
esac
