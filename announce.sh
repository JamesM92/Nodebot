#!/usr/bin/env bash
# Send SIGUSR1 to the running NodeBot process to trigger announce on all transports.
set -euo pipefail

PID=$(pgrep -f "runbot.py" 2>/dev/null | head -1 || true)

if [[ -z "$PID" ]]; then
    echo "NodeBot is not running (no runbot.py process found)" >&2
    exit 1
fi

kill -USR1 "$PID"
echo "Sent SIGUSR1 to NodeBot (pid $PID) — announcing on all transports"
echo "Check logs: journalctl -u nodebot -n 20"
