#!/bin/bash
# ============================================================
# NodeBot MeshCore Installer
#
# - Installs the meshcore Python package into the project venv
# - Scans for the MeshCore companion radio via BLE
# - Pairs the radio and stores the bond in BlueZ
# - Guides region/frequency selection and writes config.ini
#
# Hardware: Heltec V3 running stock MeshCore unified
#           companion+repeater firmware (v1.16 or later)
# Transport: BLE via bleak (Nordic UART Service)
#
# Run AFTER install_nodebot.sh:
#   bash installer/install_meshcore.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/.venv"
VENV_PYTHON="$VENV/bin/python3"
VENV_PIP="$VENV/bin/pip3"
CONFIG_INI="$PROJECT_DIR/config.ini"
PAIR_SCRIPT="$PROJECT_DIR/scripts/pair_meshcore.py"

# shellcheck source=../scripts/_meshcore_config.sh
source "$PROJECT_DIR/scripts/_meshcore_config.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗${NC}  $*" >&2; exit 1; }

echo ""
echo "================================================"
echo "  NodeBot MeshCore Installer"
echo "================================================"
echo "  Project : $PROJECT_DIR"
echo "  Venv    : $VENV"
echo "================================================"
echo ""

# ── Step 1: Install meshcore Python package ───────────────────────────────
echo "[1/4] Installing meshcore Python package..."
"$VENV_PIP" install --upgrade meshcore
ok "meshcore $("$VENV_PIP" show meshcore 2>/dev/null | awk '/^Version:/{print $2}') installed"

# ── Step 2: Scan for MeshCore BLE device ─────────────────────────────────
echo ""
echo "[2/4] Scanning for MeshCore radio via BLE..."
echo ""
echo "  Make sure the radio is:"
echo "    • Powered on"
echo "    • Running MeshCore firmware in BLE mode"
echo "    • Within 5 metres of this Pi"
echo ""

BLE_SCAN_SCRIPT=$(cat <<'PYEOF'
import asyncio
from bleak import BleakScanner

async def scan():
    print("  Scanning (10 s)...", flush=True)
    devices = await BleakScanner.discover(timeout=10.0)
    found = [(d.address, d.name or "(unnamed)") for d in devices
             if d.name and d.name.startswith("MeshCore")]
    if not found:
        print("NONE")
        return
    for addr, name in found:
        print(f"FOUND:{addr}:{name}")

asyncio.run(scan())
PYEOF
)

SCAN_OUTPUT=$("$VENV_PYTHON" -c "$BLE_SCAN_SCRIPT" 2>/dev/null)

FOUND_ADDRS=()
FOUND_NAMES=()

while IFS= read -r line; do
    if [[ "$line" == FOUND:* ]]; then
        rest="${line#FOUND:}"
        addr="${rest%%:*}"
        name="${rest#*:}"
        FOUND_ADDRS+=("$addr")
        FOUND_NAMES+=("$name")
    fi
done <<< "$SCAN_OUTPUT"

BLE_ADDRESS=""

if (( ${#FOUND_ADDRS[@]} == 0 )); then
    warn "No MeshCore BLE device found in scan."
    echo ""
    echo "  If the device is in range but not appearing, it may already be"
    echo "  paired with another device. Try the Meshtastic app or MeshCore"
    echo "  app on your phone to disconnect it first."
    echo ""
    printf "  Enter BLE address manually (e.g. 3C:0F:02:EC:E6:B9), or press Enter to abort: "
    read -r BLE_ADDRESS || true
    [[ -z "$BLE_ADDRESS" ]] && die "No BLE address entered — aborting."
elif (( ${#FOUND_ADDRS[@]} == 1 )); then
    BLE_ADDRESS="${FOUND_ADDRS[0]}"
    ok "Found: ${FOUND_NAMES[0]} (${BLE_ADDRESS})"
else
    echo "  Multiple MeshCore devices found:"
    for i in "${!FOUND_ADDRS[@]}"; do
        printf "    %d) %-25s  %s\n" $((i+1)) "${FOUND_ADDRS[$i]}" "${FOUND_NAMES[$i]}"
    done
    echo ""
    while true; do
        printf "  Select device [1-%d]: " "${#FOUND_ADDRS[@]}"
        read -r SEL || true
        if [[ "$SEL" =~ ^[0-9]+$ ]] && (( SEL >= 1 && SEL <= ${#FOUND_ADDRS[@]} )); then
            BLE_ADDRESS="${FOUND_ADDRS[$((SEL-1))]}"
            ok "Selected: ${FOUND_NAMES[$((SEL-1))]} (${BLE_ADDRESS})"
            break
        fi
        echo "  Invalid selection."
    done
fi

# ── Step 3: Pair BLE device ───────────────────────────────────────────────
echo ""
echo "[3/4] Pairing with MeshCore radio..."
echo ""
echo "  The PIN shown on the radio's screen will be used automatically."
echo "  Check the Heltec V3 display now — you should see a 6-digit PIN."
echo ""
printf "  Enter the PIN shown on the radio screen: "
read -r BLE_PIN || true

if [[ -z "$BLE_PIN" ]]; then
    warn "No PIN entered — skipping pairing."
    echo "  If the radio is already paired, NodeBot will connect normally."
    echo "  If not, run:  python3 $PAIR_SCRIPT <PIN>"
else
    echo ""
    echo "  Pairing..."
    if "$VENV_PYTHON" "$PAIR_SCRIPT" "$BLE_PIN"; then
        echo ""
        ok "Pairing complete"
        bluetoothctl trust "$BLE_ADDRESS" 2>/dev/null && ok "Device trusted in BlueZ" || \
            warn "Could not trust device automatically — run: bluetoothctl trust $BLE_ADDRESS"
    else
        warn "Pairing script returned an error."
        echo "  Check the PIN and try again manually:"
        echo "    python3 $PAIR_SCRIPT <PIN>"
    fi
fi

echo ""

# ── Step 4: Radio frequency configuration ────────────────────────────────
echo "[4/4] Radio frequency configuration"
meshcore_configure_radio "meshcore" "$BLE_ADDRESS" "$CONFIG_INI" "$VENV_PYTHON" "$PROJECT_DIR"

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo "================================================"
echo -e "  ${GREEN}MeshCore installation complete${NC}"
echo "================================================"
echo ""
printf "  BLE address : %s\n"   "$BLE_ADDRESS"
printf "  Region      : %s\n"   "${MESHCORE_REGION_LABEL:-configured}"
printf "  Frequency   : %s MHz\n" "${MESHCORE_FREQ_MHZ:-configured}"
printf "  Forwarding  : %s\n"   "${MESHCORE_FORWARD_LABEL:-configured}"
echo ""
echo "  If the bond is ever lost (radio shows new PIN on screen):"
echo "    python3 $PAIR_SCRIPT <new_PIN>"
echo "    bluetoothctl trust $BLE_ADDRESS"
echo ""
echo "  Restart NodeBot to activate:"
echo "    sudo systemctl restart nodebot"
echo ""
echo "  Live logs:"
echo "    journalctl -u nodebot -f"
echo ""
