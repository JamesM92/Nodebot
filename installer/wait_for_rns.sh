#!/bin/bash
# Wait for the RNS shared instance to be ready.
#
# RNS 1.1+ on Linux uses an abstract Unix socket (@rns/default) rather than
# a TCP port, so we check that socket — not localhost:37428.
#
# Usage: wait_for_rns.sh [TIMEOUT_SECONDS]

TIMEOUT="${1:-30}"

for i in $(seq 1 "$TIMEOUT"); do
    if ss -xl 2>/dev/null | grep -q "@rns/default"; then
        echo "[wait_for_rns] RNS shared instance ready (@rns/default)"
        exit 0
    fi
    echo "[wait_for_rns] waiting for RNS shared instance (${i}/${TIMEOUT})..."
    sleep 1
done

echo "[wait_for_rns] ERROR: RNS shared instance not available after ${TIMEOUT}s"
exit 1
