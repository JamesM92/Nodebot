#!/bin/bash
# ============================================================
# RNS shared-instance watchdog — nomadnet + rnsh
#
# Both nomadnet (RNS shared instance owner) and rnsh (shell listener,
# a client of that shared instance) are long-running daemons that can
# get stuck in a state systemd reports as "active" while they are no
# longer actually serving new work. Confirmed cause (2026-10-12): an
# unhandled KeyError in RNS.Transport.inbound()'s path_table lookup
# left rnsh wedged — alive, but not responding to new connections,
# with a knock-on "Could not save known destinations to storage"
# error a few minutes later. A plain reconnect is not enough to clear
# this; only a full service restart does. See project memory
# (project_meshcore_rflog.md / reticulum section) for the full
# incident writeup.
#
# This script is run periodically by rns-watchdog.timer. It:
#   1. Restarts any of the two services systemd itself reports as down.
#   2. Scans each service's recent journal for the known bad-state
#      signatures and restarts it if found, respecting a cooldown so
#      a single incident doesn't trigger a restart loop.
#
# Must run as root (systemctl restart requires it) — the installed
# rns-watchdog.service runs with no User= line, so it defaults to root.
# ============================================================

set -uo pipefail

SERVICES=("nomadnet" "rnsh")
LOOKBACK="10 min ago"
COOLDOWN_SECS=900   # 15 min — don't re-restart the same service faster than this
STATE_DIR="/home/penguin/.nodebot/logs"
STATE_FILE="$STATE_DIR/rns_watchdog_last_restart"
LOG_FILE="$STATE_DIR/rns_watchdog.log"

mkdir -p "$STATE_DIR"
touch "$STATE_FILE"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"
}

last_restart_of() {
    # Prints the unix timestamp of the last recorded restart for $1, or 0.
    grep "^$1 " "$STATE_FILE" 2>/dev/null | tail -1 | awk '{print $2}' || echo 0
}

record_restart() {
    { grep -v "^$1 " "$STATE_FILE" 2>/dev/null; echo "$1 $(date +%s)"; } > "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

restart_service() {
    local svc="$1" reason="$2"
    local now last
    now=$(date +%s)
    last=$(last_restart_of "$svc")
    last=${last:-0}
    if (( now - last < COOLDOWN_SECS )); then
        log "SKIP $svc — restarted ${last:+$((now-last))s ago}, within ${COOLDOWN_SECS}s cooldown (reason: $reason)"
        return
    fi
    log "RESTART $svc — $reason"
    systemctl restart "$svc"
    record_restart "$svc"
}

# Known bad-state log signatures. Matched case-insensitively against each
# service's own recent journal.
BAD_PATTERNS='Traceback \(most recent call last\)|unhandled.*exception occurred|Could not save known destinations to storage'

for svc in "${SERVICES[@]}"; do
    if ! systemctl is-active --quiet "$svc"; then
        restart_service "$svc" "systemd reports not active"
        continue
    fi

    if journalctl -u "$svc" --no-pager -S "$LOOKBACK" 2>/dev/null \
            | grep -qiE "$BAD_PATTERNS"; then
        restart_service "$svc" "known stuck-state signature in last $LOOKBACK of journal"
    fi
done
