#!/usr/bin/env bash
# ============================================================
#  kb — uninstall systemd service (Linux)
#  Usage:
#    sudo ./uninstall_kb_service.sh [--delete-data] [--dry-run]
#    ./uninstall_kb_service.sh --user [--delete-data] [--dry-run]
#  Options:
#    --user         uninstall user-level service (default: system level)
#    --delete-data  also delete kb_data/ and logs/ (default: keep)
#    --dry-run      print actions only, change nothing
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MODE_USER=0
DELETE_DATA=0
DRYRUN=0

for arg in "$@"; do
    case "$arg" in
        --user) MODE_USER=1 ;;
        --delete-data) DELETE_DATA=1 ;;
        --dry-run) DRYRUN=1 ;;
        -h|--help) grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "[ERROR] unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

log() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }
run() { if [ "$DRYRUN" -eq 1 ]; then echo "[DRY-RUN] $*"; else "$@"; fi }

[ "$(uname -s)" = "Linux" ] || die "This script is for Linux only."

SYSCMD="systemctl"
UNIT="/etc/systemd/system/kb.service"
if [ "$MODE_USER" -eq 1 ]; then
    SYSCMD="systemctl --user"
    UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/kb.service"
fi

[ -f "$UNIT" ] || log "Unit not present (nothing to uninstall): $UNIT"

# stop (tolerant) → disable → remove unit → reload
if [ -f "$UNIT" ]; then
    run $SYSCMD stop kb.service 2>/dev/null || true
    run $SYSCMD disable kb.service 2>/dev/null || true
    run rm -f "$UNIT"
fi
run $SYSCMD daemon-reload

# data dirs: keep by default
if [ "$DELETE_DATA" -eq 1 ]; then
    warn "Deleting data dirs: $REPO_ROOT/kb_data $REPO_ROOT/logs"
    run rm -rf "$REPO_ROOT/kb_data" "$REPO_ROOT/logs"
else
    log "Data dirs kept: $REPO_ROOT/kb_data $REPO_ROOT/logs (use --delete-data to remove)"
fi

echo
if [ "$DRYRUN" -eq 1 ]; then
    log "Dry-run complete — nothing changed. Remove --dry-run to apply."
else
    log "kb systemd service uninstalled."
fi