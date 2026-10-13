#!/bin/bash
# ============================================================
# MeshCore TCP stuck-connection watchdog
#
# Confirmed incident (2026-10-10 through 2026-10-12): the MeshCore TCP
# socket (to the radio via 192.168.90.10:5000) died with no traceback
# and no clean disconnect — the underlying meshcore-py library kept
# retrying sends into the dead socket every ~15s forever
# ("WARNING:asyncio:socket.send() raised exception."), without ever
# triggering NodeBot's own internal reconnect logic. Radio stats read
# all-zero (noise=0, tx_air=0, rx_air=0) throughout. The outage lasted
# 2+ days before anyone noticed; only a full `systemctl restart
# nodebot` (fresh TCP connection + complete re-init sequence) cleared
# it — the library's own auto-reconnect only does a lightweight
# handshake, not enough after this kind of failure. See project
# memory (project_meshcore_rflog.md / TCP auto-reconnect section) for
# the full incident writeup.
#
# This script is run periodically by meshcore-watchdog.timer. It
# restarts nodebot.service if:
#   - systemd itself reports it down, or
#   - the recent journal shows the dead-socket signature repeating
#     persistently, AND no genuine RX evidence appears in that same
#     window (so a real, resolving blip doesn't trigger a restart).
# ============================================================

set -uo pipefail

SERVICE="nodebot"
LOOKBACK="10 min ago"
MIN_SOCKET_ERRORS=10   # ~15s apart -> ~2.5 min of sustained failure
COOLDOWN_SECS=900      # 15 min — don't re-restart faster than this
STATE_DIR="/home/penguin/.nodebot/logs"
STATE_FILE="$STATE_DIR/meshcore_watchdog_last_restart"
LOG_FILE="$STATE_DIR/meshcore_watchdog.log"

mkdir -p "$STATE_DIR"
touch "$STATE_FILE"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"
}

last_restart() {
    cat "$STATE_FILE" 2>/dev/null || echo 0
}

record_restart() {
    date +%s > "$STATE_FILE"
}

restart_nodebot() {
    local reason="$1" now last
    now=$(date +%s)
    last=$(last_restart)
    last=${last:-0}
    if (( now - last < COOLDOWN_SECS )); then
        log "SKIP $SERVICE — restarted $((now-last))s ago, within ${COOLDOWN_SECS}s cooldown (reason: $reason)"
        return
    fi
    log "RESTART $SERVICE — $reason"
    systemctl restart "$SERVICE"
    record_restart
}

if ! systemctl is-active --quiet "$SERVICE"; then
    restart_nodebot "systemd reports not active"
    exit 0
fi

RECENT_LOG="$(journalctl -u "$SERVICE" --no-pager -S "$LOOKBACK" 2>/dev/null)"

SOCKET_ERROR_COUNT=$(grep -c "socket.send() raised exception" <<<"$RECENT_LOG")
RX_EVIDENCE=$(grep -ciE "radio healthy: \+|\(via rflog\)|\(via push\)|RF chan msg" <<<"$RECENT_LOG")

if (( SOCKET_ERROR_COUNT >= MIN_SOCKET_ERRORS )) && (( RX_EVIDENCE == 0 )); then
    restart_nodebot "MeshCore dead-socket signature: ${SOCKET_ERROR_COUNT} socket.send() errors in last ${LOOKBACK}, zero RX evidence"
fi
