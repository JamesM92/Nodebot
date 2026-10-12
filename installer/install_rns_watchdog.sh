#!/bin/bash
# ============================================================
# RNS Watchdog Installer
#
# Installs two systemd timers that keep nomadnet + rnsh from silently
# getting stuck (confirmed 2026-10-12 incident: both can report "active"
# while no longer serving new work — a plain reconnect doesn't clear it,
# only a service restart does):
#
#   1. rns-watchdog.timer (every 5 min)
#      Runs scripts/rns_watchdog.sh, which restarts either service if
#      systemd reports it down, or if its recent log shows the known
#      stuck-state signatures (unhandled exception / storage-save
#      timeout). 15-minute cooldown per service to avoid restart loops.
#
#   2. rns-preventive-restart.timer (weekly, Sun 04:30 local)
#      Unconditionally restarts both services as a backstop against any
#      degradation the log-pattern check doesn't catch.
#
# Prerequisites:
#   - install_lxmf.sh already run (nomadnet.service exists)
#   - install_rnsh.sh already run (rnsh.service exists)
#
# Run as: bash installer/install_rns_watchdog.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
WATCHDOG_SCRIPT="$PROJECT_DIR/scripts/rns_watchdog.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗${NC}  $*" >&2; exit 1; }
info() { echo -e "${CYAN}→${NC} $*"; }

echo ""
echo "================================================"
echo "  NodeBot RNS Watchdog Installer"
echo "================================================"
echo ""

# ── Step 1: Prerequisites ────────────────────────────────────────────────
echo "[1/4] Checking prerequisites..."
for svc in nomadnet rnsh; do
    UNIT_LIST="$(systemctl list-unit-files "${svc}.service" 2>/dev/null)"
    if [[ "$UNIT_LIST" != *"${svc}.service"* ]]; then
        die "${svc}.service not found — run install_lxmf.sh / install_rnsh.sh first"
    fi
done
[[ -x "$WATCHDOG_SCRIPT" ]] || chmod +x "$WATCHDOG_SCRIPT"
ok "nomadnet.service and rnsh.service present; watchdog script executable"

# ── Step 2: Install watchdog timer (every 5 min) ─────────────────────────
echo ""
echo "[2/4] Installing rns-watchdog (5-min health check)..."
sudo cp "$SCRIPT_DIR/rns-watchdog.service" /etc/systemd/system/rns-watchdog.service
sudo cp "$SCRIPT_DIR/rns-watchdog.timer"   /etc/systemd/system/rns-watchdog.timer
sudo systemctl daemon-reload
sudo systemctl enable --now rns-watchdog.timer
ok "rns-watchdog.timer enabled (runs every 5 min)"

# ── Step 3: Install weekly preventive-restart timer ──────────────────────
echo ""
echo "[3/4] Installing rns-preventive-restart (weekly backstop)..."
sudo cp "$SCRIPT_DIR/rns-preventive-restart.service" /etc/systemd/system/rns-preventive-restart.service
sudo cp "$SCRIPT_DIR/rns-preventive-restart.timer"   /etc/systemd/system/rns-preventive-restart.timer
sudo systemctl daemon-reload
sudo systemctl enable --now rns-preventive-restart.timer
ok "rns-preventive-restart.timer enabled (Sun 04:30 local)"

# ── Step 4: Summary ───────────────────────────────────────────────────────
echo ""
echo "[4/4] Verifying..."
systemctl is-enabled --quiet rns-watchdog.timer && ok "rns-watchdog.timer enabled" \
    || warn "rns-watchdog.timer not enabled — check: systemctl status rns-watchdog.timer"
systemctl is-enabled --quiet rns-preventive-restart.timer && ok "rns-preventive-restart.timer enabled" \
    || warn "rns-preventive-restart.timer not enabled — check: systemctl status rns-preventive-restart.timer"

echo ""
echo "================================================"
echo -e "  ${GREEN}RNS watchdog installation complete${NC}"
echo "================================================"
echo ""
echo "  Watchdog log : /home/penguin/.nodebot/logs/rns_watchdog.log"
echo "  Next runs    : systemctl list-timers rns-watchdog.timer rns-preventive-restart.timer"
echo "  Manual run   : sudo systemctl start rns-watchdog.service"
echo ""
