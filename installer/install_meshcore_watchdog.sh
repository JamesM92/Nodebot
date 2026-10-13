#!/bin/bash
# ============================================================
# MeshCore Watchdog Installer
#
# Installs meshcore-watchdog.timer, which runs every 5 minutes and
# restarts nodebot.service if the MeshCore TCP connection is stuck —
# confirmed 2026-10-10 through 2026-10-12: the underlying socket died
# with no clean disconnect, and the library's own auto-reconnect
# (a lightweight handshake only) never recovered it. The outage ran
# 2+ days before anyone noticed; only a full service restart cleared
# it. See scripts/meshcore_watchdog.sh for the detection logic.
#
# Prerequisites:
#   - install_nodebot.sh already run (nodebot.service exists)
#
# Run as: bash installer/install_meshcore_watchdog.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
WATCHDOG_SCRIPT="$PROJECT_DIR/scripts/meshcore_watchdog.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗${NC}  $*" >&2; exit 1; }
info() { echo -e "${CYAN}→${NC} $*"; }

echo ""
echo "================================================"
echo "  NodeBot MeshCore Watchdog Installer"
echo "================================================"
echo ""

# ── Step 1: Prerequisites ────────────────────────────────────────────────
echo "[1/3] Checking prerequisites..."
UNIT_LIST="$(systemctl list-unit-files "nodebot.service" 2>/dev/null)"
if [[ "$UNIT_LIST" != *"nodebot.service"* ]]; then
    die "nodebot.service not found — run install_nodebot.sh first"
fi
[[ -x "$WATCHDOG_SCRIPT" ]] || chmod +x "$WATCHDOG_SCRIPT"
ok "nodebot.service present; watchdog script executable"

# ── Step 2: Install timer ────────────────────────────────────────────────
echo ""
echo "[2/3] Installing meshcore-watchdog (5-min health check)..."
sudo cp "$SCRIPT_DIR/meshcore-watchdog.service" /etc/systemd/system/meshcore-watchdog.service
sudo cp "$SCRIPT_DIR/meshcore-watchdog.timer"   /etc/systemd/system/meshcore-watchdog.timer
sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-watchdog.timer
ok "meshcore-watchdog.timer enabled (runs every 5 min)"

# ── Step 3: Verify ────────────────────────────────────────────────────────
echo ""
echo "[3/3] Verifying..."
systemctl is-enabled --quiet meshcore-watchdog.timer && ok "meshcore-watchdog.timer enabled" \
    || warn "meshcore-watchdog.timer not enabled — check: systemctl status meshcore-watchdog.timer"

echo ""
echo "================================================"
echo -e "  ${GREEN}MeshCore watchdog installation complete${NC}"
echo "================================================"
echo ""
echo "  Watchdog log : /home/penguin/.nodebot/logs/meshcore_watchdog.log"
echo "  Next run     : systemctl list-timers meshcore-watchdog.timer"
echo "  Manual run   : sudo systemctl start meshcore-watchdog.service"
echo ""
