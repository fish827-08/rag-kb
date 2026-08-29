#!/usr/bin/env bash
# ============================================================
#  kb — install systemd service (Linux)
#  一键安装 kb 为 systemd 服务：systemctl start/stop/restart/status kb
#
#  Two modes:
#    system level (default, needs sudo):  unit at /etc/systemd/system/kb.service
#                                          service runs as the INVOKING user (never root)
#    user level        (--user, no sudo):  unit at ~/.config/systemd/user/kb.service
#
#  Usage:
#    sudo ./install_kb_service.sh [--enable] [--no-start] [--dry-run]
#    ./install_kb_service.sh --user [--enable] [--no-start] [--dry-run]
#
#  Options:
#    --enable      also systemctl enable (autostart on boot)
#    --no-start    install but do not start the service
#    --dry-run     print actions only, change nothing
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_NAME="install_kb_service.sh"

MODE_USER=0
ENABLE=0
NOSTART=0
DRYRUN=0

for arg in "$@"; do
    case "$arg" in
        --user)    MODE_USER=1 ;;
        --enable)  ENABLE=1 ;;
        --no-start) NOSTART=1 ;;
        --dry-run) DRYRUN=1 ;;
        -h|--help) grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "[ERROR] unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

log()  { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
die()  { echo "[ERROR] $*" >&2; exit 1; }
run()  { # run <cmd...> — dry-run only prints
    if [ "$DRYRUN" -eq 1 ]; then echo "[DRY-RUN] $*"; else "$@"; fi
}

# ---------- 0. guards ----------
[ "$(uname -s)" = "Linux" ] || die "This script is for Linux only."

SYSTEMD_SYSTEM=$(command -v systemctl >/dev/null 2>&1 && echo yes || echo no)
[ "$SYSTEMD_SYSTEM" = yes ] || die "systemctl not found — systemd is required."

if [ "$MODE_USER" -eq 0 ]; then
    # system level: allow only via sudo, but NEVER run the service as root
    if [ "$(id -u)" -eq 0 ] && [ -z "${SUDO_USER:-}" ]; then
        die "Do not run as root directly. Use:  sudo $SCRIPT_NAME   (service will run as your normal user)"
    fi
    UNIT_DIR="/etc/systemd/system"
    RUN_USER="${SUDO_USER:-$(id -un)}"
else
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    RUN_USER="$(id -un)"
fi

# ---------- 1. locate python (venv preferred) ----------
PY=""
for cand in "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/venv/bin/python"; do
    if [ -x "$cand" ]; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    PY=$(command -v python3 || true)
    [ -n "$PY" ] || die "No python found: expected .venv/bin/python, venv/bin/python, or python3 on PATH."
fi
case "$PY" in *" "*) die "Python path contains spaces — unsupported: $PY" ;; esac
log "Python: $PY"

# ---------- 2. data/log dirs, owned by run user ----------
DATA_DIR="$REPO_ROOT/kb_data"
LOG_DIR="$REPO_ROOT/logs"
if [ "$DRYRUN" -eq 0 ]; then
    mkdir -p "$DATA_DIR" "$LOG_DIR"
    if [ "$MODE_USER" -eq 0 ]; then
        chown -R "$RUN_USER" "$DATA_DIR" "$LOG_DIR" 2>/dev/null || \
            warn "chown to $RUN_USER failed (may need sudo) — check dir ownership before start"
    fi
fi
log "Data dir: $DATA_DIR (owner: $RUN_USER)"
log "Log dir : $LOG_DIR"

# ---------- 3. render unit file ----------
UNIT="$UNIT_DIR/kb.service"
run mkdir -p "$UNIT_DIR"
if [ "$DRYRUN" -eq 0 ]; then
    if [ "$MODE_USER" -eq 1 ]; then
        # user-level service: drop User= line and use default.target
        sed -e "s|^User=.*|# User= dropped for user-level service|" \
            -e "s|__REPO__|$REPO_ROOT|g" \
            -e "s|__PYTHON__|$PY|g" \
            -e "s|multi-user.target|default.target|" \
            "$REPO_ROOT/scripts/linux/kb.service" > "$UNIT"
    else
        sed -e "s|__USER__|$RUN_USER|g" \
            -e "s|__REPO__|$REPO_ROOT|g" \
            -e "s|__PYTHON__|$PY|g" \
            "$REPO_ROOT/scripts/linux/kb.service" > "$UNIT"
    fi
    chmod 644 "$UNIT"
fi
log "Unit file: $UNIT"
if [ "$DRYRUN" -eq 1 ]; then
    sed -e "s|__USER__|$RUN_USER|g" -e "s|__REPO__|$REPO_ROOT|g" -e "s|__PYTHON__|$PY|g" \
        "$REPO_ROOT/scripts/linux/kb.service"
fi

# ---------- 4. reload + optional enable + start ----------
SYSCMD="systemctl"
[ "$MODE_USER" -eq 1 ] && SYSCMD="systemctl --user"

run $SYSCMD daemon-reload
if [ "$ENABLE" -eq 1 ]; then
    run $SYSCMD enable kb.service
fi
if [ "$NOSTART" -eq 0 ]; then
    run $SYSCMD restart kb.service
fi

echo
if [ "$DRYRUN" -eq 1 ]; then
    log "Dry-run complete — nothing changed. Remove --dry-run to apply."
else
    log "Done."
    log "  status : $SYSCMD status kb"
    log "  start  : $SYSCMD start kb     stop: $SYSCMD stop kb"
    log "  logs   : journalctl -u kb -f   (plus $LOG_DIR/kb_*.log)"
    [ "$ENABLE" -eq 1 ] && log "  autostart: enabled"
    [ "$MODE_USER" -eq 1 ] && warn "User-level service needs a login session (XDG_RUNTIME_DIR). SSH users: export XDG_RUNTIME_DIR=/run/user/\$UID"
    [ "$NOSTART" -eq 0 ] && log "  Next   : curl http://127.0.0.1:8000/api/v1/healthz"
fi