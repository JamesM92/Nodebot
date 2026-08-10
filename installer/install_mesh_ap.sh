#!/bin/bash
# ============================================================
# NodeBot Mesh AP Installer
#
# Sets up a dedicated 2.4 GHz WiFi access point on a USB WiFi
# dongle (wlan1) for the Meshtastic companion boards to connect
# to.  The boards use TCPInterface over this private subnet;
# NodeBot connects to each board at its reserved IP.
#
# Prerequisites:
#   - Panda PAU05 (RT5370) or equivalent dongle plugged in as wlan1
#   - Meshtastic boards already configured with SSID/password below
#     (do this from the Meshtastic phone app before deploying)
#   - NodeBot installed (install_nodebot.sh already run)
#
# Run once:
#   sudo bash installer/install_mesh_ap.sh
#
# What it does:
#   1. Verifies wlan1 is present and supports AP mode
#   2. Creates a NetworkManager AP connection on wlan1
#   3. Configures dnsmasq for DHCP + static leases (by board MAC)
#   4. Enables the AP on boot via systemd-networkd
#   5. Prompts for board MAC addresses and updates config.ini
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_INI="$PROJECT_DIR/config.ini"

# ── AP settings (edit if you want a different network) ─────────────────────
AP_IFACE="wlan1"
AP_SSID="NodeBot-Mesh"
AP_PASSWORD=""            # set below — must be 8+ chars
AP_CHANNEL="6"            # 2.4 GHz channel (1, 6, or 11 recommended)
AP_IP="192.168.90.1"
AP_SUBNET="192.168.90.0/24"
AP_DHCP_START="192.168.90.10"
AP_DHCP_END="192.168.90.50"
AP_LEASE_TIME="24h"
BOARD0_IP="192.168.90.2"  # meshtastic0 reserved IP
BOARD1_IP="192.168.90.3"  # meshtastic1 reserved IP
TCP_PORT="4403"
# ───────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗${NC}  $*" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
    die "Run as root: sudo bash $0"
fi

echo ""
echo "================================================"
echo "  NodeBot Mesh AP Installer"
echo "================================================"
echo "  Project : $PROJECT_DIR"
echo "  AP iface: $AP_IFACE  SSID: $AP_SSID"
echo "  Subnet  : $AP_SUBNET  GW: $AP_IP"
echo ""

# ── 1. Verify wlan1 exists ─────────────────────────────────────────────────
echo "── Step 1: Checking for $AP_IFACE ──"
if ! ip link show "$AP_IFACE" &>/dev/null; then
    die "$AP_IFACE not found. Plug in the Panda PAU05 dongle and retry."
fi
ok "$AP_IFACE present"

# Verify AP mode is supported
if ! iw list 2>/dev/null | grep -A5 "Supported interface modes" | grep -q "\* AP"; then
    # Try phy for wlan1 specifically
    PHY=$(iw dev "$AP_IFACE" info 2>/dev/null | awk '/wiphy/{print "phy"$2}')
    if [[ -n "$PHY" ]] && ! iw phy "$PHY" info | grep -A10 "Supported interface modes" | grep -q "\* AP"; then
        die "$AP_IFACE does not support AP mode. Check the dongle chipset."
    fi
fi
ok "$AP_IFACE supports AP mode"

# ── 2. Get AP password ─────────────────────────────────────────────────────
echo ""
echo "── Step 2: AP Password ──"
while true; do
    read -rsp "Enter WiFi password for '$AP_SSID' (8+ chars): " AP_PASSWORD; echo
    if [[ ${#AP_PASSWORD} -ge 8 ]]; then
        break
    fi
    warn "Password must be at least 8 characters."
done

# ── 3. Get board MAC addresses for DHCP reservations ──────────────────────
echo ""
echo "── Step 3: Board MAC addresses ──"
echo "  Find each board's WiFi MAC in the Meshtastic app:"
echo "  Settings → Radio Config → Network → WiFi MAC address"
echo ""
read -rp "  MAC address for meshtastic0 board (or press Enter to skip): " BOARD0_MAC
read -rp "  MAC address for meshtastic1 board (or press Enter to skip): " BOARD1_MAC

# Normalise MAC addresses to lowercase colon-separated
normalise_mac() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/-/:/g'
}
[[ -n "$BOARD0_MAC" ]] && BOARD0_MAC=$(normalise_mac "$BOARD0_MAC")
[[ -n "$BOARD1_MAC" ]] && BOARD1_MAC=$(normalise_mac "$BOARD1_MAC")

# ── 4. Install dnsmasq ─────────────────────────────────────────────────────
echo ""
echo "── Step 4: Installing dnsmasq ──"
if ! command -v dnsmasq &>/dev/null; then
    apt-get install -y dnsmasq
fi
ok "dnsmasq installed"

# Write dnsmasq config
DNSMASQ_CONF="/etc/dnsmasq.d/nodebot-mesh-ap.conf"
cat > "$DNSMASQ_CONF" << DNSMASQ_EOF
# NodeBot Mesh AP — DHCP for Meshtastic boards
interface=$AP_IFACE
bind-interfaces
dhcp-range=$AP_DHCP_START,$AP_DHCP_END,$AP_LEASE_TIME
# Gateway / DNS for clients
dhcp-option=3,$AP_IP
dhcp-option=6,$AP_IP
DNSMASQ_EOF

# Static leases (only if MACs were provided)
if [[ -n "$BOARD0_MAC" ]]; then
    echo "dhcp-host=$BOARD0_MAC,$BOARD0_IP,meshtastic0,$AP_LEASE_TIME" >> "$DNSMASQ_CONF"
    ok "Reserved $BOARD0_IP for meshtastic0 ($BOARD0_MAC)"
else
    warn "No MAC for meshtastic0 — board will get a dynamic IP (update config.ini manually)"
fi

if [[ -n "$BOARD1_MAC" ]]; then
    echo "dhcp-host=$BOARD1_MAC,$BOARD1_IP,meshtastic1,$AP_LEASE_TIME" >> "$DNSMASQ_CONF"
    ok "Reserved $BOARD1_IP for meshtastic1 ($BOARD1_MAC)"
else
    warn "No MAC for meshtastic1 — board will get a dynamic IP (update config.ini manually)"
fi

ok "dnsmasq configured: $DNSMASQ_CONF"

# ── 5. Create NetworkManager AP connection ─────────────────────────────────
echo ""
echo "── Step 5: Creating NetworkManager AP on $AP_IFACE ──"

# Remove existing connection with same name if present
nmcli connection delete "NodeBot-Mesh-AP" 2>/dev/null || true

nmcli connection add \
    type wifi \
    ifname "$AP_IFACE" \
    con-name "NodeBot-Mesh-AP" \
    ssid "$AP_SSID" \
    mode ap \
    band bg \
    channel "$AP_CHANNEL" \
    ipv4.method manual \
    ipv4.addresses "$AP_IP/24" \
    ipv4.gateway "" \
    ipv4.dns "" \
    ipv6.method disabled \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$AP_PASSWORD" \
    connection.autoconnect yes \
    connection.autoconnect-priority 10

nmcli connection up "NodeBot-Mesh-AP"
ok "AP '$AP_SSID' is up on $AP_IFACE ($AP_IP)"

# Restart dnsmasq now that the interface is up
systemctl restart dnsmasq
ok "dnsmasq restarted"

# ── 6. Update config.ini ───────────────────────────────────────────────────
echo ""
echo "── Step 6: Updating config.ini ──"

update_host() {
    local section="$1"
    local host_ip="$2"
    local board_mac="$3"

    if [[ -z "$board_mac" ]]; then
        warn "[$section] skipped — no MAC provided; add 'host = <IP>' manually once board connects"
        return
    fi

    # Replace or insert host= in the section
    python3 - "$CONFIG_INI" "$section" "$host_ip" "$TCP_PORT" << 'PYEOF'
import sys, configparser, re

config_path, section, host, tcp_port = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

cfg = configparser.RawConfigParser()
cfg.read(config_path)

if not cfg.has_section(section):
    cfg.add_section(section)

cfg.set(section, 'host', host)
cfg.set(section, 'tcp_port', tcp_port)

# Comment out port= if present (keep for reference)
with open(config_path) as f:
    content = f.read()

# Write config
with open(config_path, 'w') as f:
    cfg.write(f)

print(f"  [{section}] host={host} tcp_port={tcp_port}")
PYEOF
}

update_host "meshtastic"  "$BOARD0_IP" "$BOARD0_MAC"
update_host "meshtastic1" "$BOARD1_IP" "$BOARD1_MAC"

# ── 7. Summary ─────────────────────────────────────────────────────────────
echo ""
echo "================================================"
echo -e "  ${GREEN}Mesh AP setup complete${NC}"
echo "================================================"
echo ""
echo "  SSID    : $AP_SSID"
echo "  Password: $AP_PASSWORD"
echo "  Channel : $AP_CHANNEL (2.4 GHz)"
echo "  Pi IP   : $AP_IP"
[[ -n "$BOARD0_MAC" ]] && echo "  Board 0 : $BOARD0_IP ($BOARD0_MAC)"
[[ -n "$BOARD1_MAC" ]] && echo "  Board 1 : $BOARD1_IP ($BOARD1_MAC)"
echo ""
echo "  Next steps:"
echo "  1. In the Meshtastic app on each board:"
echo "     Settings → Radio Config → Network"
echo "     → WiFi SSID: $AP_SSID"
echo "     → WiFi PSK: $AP_PASSWORD"
echo "     → WiFi enabled: ON"
echo "     → TCP Server port: $TCP_PORT (should be default)"
echo "  2. Restart NodeBot: sudo systemctl restart nodebot"
echo "  3. Watch logs:      journalctl -fu nodebot"
echo ""
