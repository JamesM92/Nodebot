#!/bin/bash
# ============================================================
# NodeBot LXMF Installer
#
# - Installs NomadNet globally (pip3 --user, outside the project venv)
# - Probes USB ports to auto-detect rNode firmware
# - Creates udev symlinks (/dev/rnode0, /dev/rnode1 ...) so
#   devices are reachable at a stable path after any reconnect
# - Guides rNode frequency configuration by region
# - Writes ~/.reticulum/config
# - Installs nomadnet.service (RNS shared instance + rNode owner)
# - Installs nodebot.service  (depends on nomadnet, waits for RNS)
#
# Run as the normal user (sudo is invoked where needed):
#   bash installer/install_lxmf.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/.venv"
VENV_PYTHON="$VENV/bin/python3"
# NomadNet is installed globally (pip3 --user), not inside the project venv.
# python3 -m site --user-base gives the platform-correct prefix (~/.local on Linux).
USER_BIN="$(python3 -m site --user-base)/bin"
NOMADNET_BIN="$USER_BIN/nomadnet"
RNODECONF_BIN="$USER_BIN/rnodeconf"
WAIT_SCRIPT="$SCRIPT_DIR/wait_for_rns.sh"
SERVICE_USER="$(whoami)"
RNS_CONFIG="$HOME/.reticulum/config"
UDEV_RULES="/etc/udev/rules.d/99-rnode.rules"

echo ""
echo "================================================"
echo "  NodeBot LXMF Installer"
echo "================================================"
echo "  Project : $PROJECT_DIR"
echo "  Venv    : $VENV"
echo "  User    : $SERVICE_USER"
echo "================================================"
echo ""

# ── Legal disclaimer ──────────────────────────────────────────
echo "  ╔═════════════════════════════════════════════════════╗"
echo "  ║               ⚠  LEGAL NOTICE  ⚠                   ║"
echo "  ║                                                     ║"
echo "  ║  LoRa frequency, bandwidth, and power settings      ║"
echo "  ║  are regulated by law and vary by country.          ║"
echo "  ║                                                     ║"
echo "  ║  The settings offered below are community-reported  ║"
echo "  ║  starting points from the Reticulum wiki.           ║"
echo "  ║  They are NOT official, endorsed, or guaranteed to  ║"
echo "  ║  be legal in your jurisdiction.                     ║"
echo "  ║                                                     ║"
echo "  ║  YOU are solely responsible for ensuring your       ║"
echo "  ║  chosen settings comply with local radio laws.      ║"
echo "  ║  Consult your national telecommunications           ║"
echo "  ║  authority before transmitting.                     ║"
echo "  ╚═════════════════════════════════════════════════════╝"
echo ""
printf "  I understand and accept responsibility (yes/no): "
read -r ACCEPT || true
if [[ "${ACCEPT,,}" != "yes" ]]; then
    echo ""
    echo "  Aborted. Please review local radio regulations before proceeding."
    exit 1
fi
echo ""

# ── Region / frequency data ───────────────────────────────────
# Format: "frequency_hz|bandwidth_hz|spreading_factor|description"
# Source: https://github.com/markqvist/Reticulum/wiki/Popular-RNode-Settings

REGION_NAMES=(
    "Australia"
    "Belgium"
    "China"
    "Finland"
    "Germany"
    "Italy"
    "Malaysia / Singapore / Thailand (AS923)"
    "Netherlands"
    "Norway"
    "Spain"
    "Sweden"
    "Switzerland"
    "United Kingdom"
    "United States"
    "Manual entry (custom values)"
)

SETTINGS_1=("925875000|250000|9|Western Sydney (sydney.reticulum.au)" "925875000|250000|11|Sydney (VK2DIO T-Beam)" "925875000|250000|9|Brisbane")
SETTINGS_2=("867200000|125000|8|Duffel (SiSCD-Node)")
SETTINGS_3=("470300000|125000|9|Beijing (BJ-RNS-Node)" "471500000|125000|10|Shanghai (SH-LoRa-Gateway)" "472700000|250000|8|Guangzhou (GZ-Reticulum-Hub)" "473900000|125000|11|Chengdu (CD-LoRa-Node)")
SETTINGS_4=("869420000|125000|8|Turku (TurkuFI)")
SETTINGS_5=("869400000|250000|7|Darmstadt (CCC Darmstadt)" "869525000|125000|8|Wiesbaden (data.haus Germany)")
SETTINGS_6=("869525000|250000|8|Salerno (F LoRa Node)" "867200000|125000|7|Brescia (N0SIGNAL)" "867200000|125000|7|Treviso (Arg0net RRP)" "433600000|125000|12|Genova (XZ Group LoRa)")
SETTINGS_7=("920500000|125000|8|AS923 LoRaWAN Regional Parameters")
SETTINGS_8=("867200000|125000|8|Rotterdam Nesselande (Undique)" "869400000|125000|8|Brugge (RNS Brugge Gateway)")
SETTINGS_9=("869431250|62500|7|Norway")
SETTINGS_10=("868200000|125000|8|Madrid (Quixote Radio Shack)")
SETTINGS_11=("869525000|250000|10|Gothenburg / Borås / Älvsered (868 MHz)" "433575000|125000|8|Gothenburg / Borås / Älvsered (433 MHz)" "866000000|125000|8|Mörbylånga/Bredinge")
SETTINGS_12=("868000000|250000|8|Bern (Swisslibertarians)")
SETTINGS_13=("867500000|125000|9|Various / St. Helens / Edinburgh (868 MHz)" "2427000000|812500|7|Edinburgh (VonChaos 2.4 GHz)")
SETTINGS_14=("914875000|125000|8|Portsmouth NH / Olympia WA / Chicago IL")

# ── Helper: pick from a numbered list ────────────────────────
pick() {
    local prompt="$1" max="$2" choice
    while true; do
        printf "  %s [1-%d]: " "$prompt" "$max" >&2
        read -r choice || true
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= max )); then
            echo "$choice"; return
        fi
        echo "  Please enter a number between 1 and $max." >&2
    done
}

# ── Helper: get a single udev env property for a port ────────
udev_prop() {
    udevadm info --name="$1" 2>/dev/null | awk -F= "/^E: ${2}=/{print \$2}"
}

# ── Step 1: Fix venv execute permissions ─────────────────────
echo "[1/7] Fixing venv permissions..."
find "$VENV/bin" -type f ! -perm /111 -exec chmod +x {} \;
echo "      Done."

# ── Step 2: Install / upgrade NomadNet ───────────────────────
echo "[2/7] Installing NomadNet globally (pip3 --user)..."
# Try plain install first; fall back to --break-system-packages on systems
# that enforce PEP 668 (Debian Bookworm / Raspberry Pi OS 12+).
if ! pip3 install --user --upgrade "nomadnet>=0.9.9" 2>/dev/null; then
    pip3 install --user --upgrade --break-system-packages "nomadnet>=0.9.9"
fi
echo "      NomadNet installed to $USER_BIN"

# ── Step 3: Detect rNodes on USB ports ───────────────────────
echo ""
echo "[3/7] Detecting rNodes on USB ports..."
echo ""

# Temporarily stop nomadnet so its serial port is free to probe
NOMADNET_WAS_RUNNING=false
if systemctl is-active --quiet nomadnet 2>/dev/null; then
    echo "      Stopping nomadnet to free serial ports for probing..."
    sudo systemctl stop nomadnet nodebot 2>/dev/null || true
    NOMADNET_WAS_RUNNING=true
    sleep 2
fi

RNODE_PORTS=()   # ports confirmed as rNodes
RNODE_LABELS=()  # human-readable label for each

for port in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$port" ] || continue

    model=$(udev_prop "$port" "ID_MODEL")
    vendor=$(udev_prop "$port" "ID_VENDOR")
    serial=$(udev_prop "$port" "ID_SERIAL_SHORT")

    printf "  Probing %-16s [%s %s S/N:%s] ... " \
        "$port" "$vendor" "$model" "${serial:-none}"

    if timeout 6 "$RNODECONF_BIN" "$port" --info 2>/dev/null | grep -qiE "firmware|product|rnode"; then
        echo "rNode detected"
        RNODE_PORTS+=("$port")
        RNODE_LABELS+=("$vendor $model (S/N: ${serial:-none})")
    else
        echo "not an rNode"
    fi
done

if $NOMADNET_WAS_RUNNING; then
    echo ""
    echo "      Restarting nomadnet..."
    sudo systemctl start nomadnet 2>/dev/null || true
fi

echo ""

if (( ${#RNODE_PORTS[@]} == 0 )); then
    echo "  No rNodes detected. Make sure your device is plugged in and"
    echo "  flashed with rNode firmware, then re-run this installer."
    echo ""
    printf "  Continue anyway with manual port entry? (yes/no): "
    read -r CONTINUE || true
    if [[ "${CONTINUE,,}" != "yes" ]]; then
        exit 1
    fi
    printf "  Enter port (e.g. /dev/ttyUSB0): "
    read -r MANUAL_PORT || true
    RNODE_PORTS=("$MANUAL_PORT")
    RNODE_LABELS=("manual entry")
fi

# ── Step 4: Create udev rules for stable /dev/rnodeN names ───
echo "[4/7] Creating udev symlinks for stable device naming..."
echo ""
echo "  This creates /dev/rnode0, /dev/rnode1 ... symlinks that"
echo "  follow each device regardless of which USB port it uses."
echo "  RNS will connect to /dev/rnode0 (or the chosen device)"
echo "  and reconnect automatically when the device is re-attached."
echo ""

# Start fresh udev rules file for rNodes
sudo tee "$UDEV_RULES" > /dev/null <<'UDEV_HEADER'
# rNode stable device naming — written by NodeBot LXMF installer
# Each rule creates /dev/rnodeN tied to a specific device's identity.
# When the device is unplugged and replugged (any USB port), the
# symlink is recreated and RNS reconnects automatically.
UDEV_HEADER

CHOSEN_RNODE_PORT=""
CHOSEN_RNODE_IDX=0

for i in "${!RNODE_PORTS[@]}"; do
    port="${RNODE_PORTS[$i]}"
    label="${RNODE_LABELS[$i]}"
    symlink="rnode${i}"

    id_serial=$(udev_prop "$port" "ID_SERIAL")
    id_serial_short=$(udev_prop "$port" "ID_SERIAL_SHORT")
    id_path=$(udev_prop "$port" "ID_PATH")

    echo "  Device $((i+1)): $port — $label"

    if [[ -n "$id_serial" ]]; then
        # Prefer full ID_SERIAL (vendor+model+serial combined) — unique per device
        # if the serial chip was programmed with a unique serial number.
        # Fall back to ID_PATH (physical USB slot) if the serial is generic.
        generic_serials=("0001" "0000" "1234567890" "ABCDEF" "")
        is_generic=false
        for g in "${generic_serials[@]}"; do
            if [[ "$id_serial_short" == "$g" ]]; then
                is_generic=true; break
            fi
        done

        if $is_generic; then
            echo "    ⚠  Serial S/N '$id_serial_short' is a generic factory default."
            echo "       Using physical USB port path instead."
            echo "       This device must stay in the same USB port to be recognised."
            RULE="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"${id_path}\", SYMLINK+=\"${symlink}\""
        else
            echo "    Unique serial detected — symlink will follow device across ports."
            RULE="SUBSYSTEM==\"tty\", ENV{ID_SERIAL}==\"${id_serial}\", SYMLINK+=\"${symlink}\""
        fi
    else
        echo "    No USB serial info found, using physical port path."
        RULE="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"${id_path}\", SYMLINK+=\"${symlink}\""
    fi

    echo "    Rule: $RULE"
    printf "# Device: %s — %s\n%s\n\n" "$port" "$label" "$RULE" | sudo tee -a "$UDEV_RULES" > /dev/null
    echo "    -> /dev/${symlink}"
    echo ""

    if (( i == 0 )); then
        CHOSEN_RNODE_PORT="/dev/${symlink}"
        CHOSEN_RNODE_IDX=0
    fi
done

if (( ${#RNODE_PORTS[@]} > 1 )); then
    echo "  Multiple rNodes found. Which one should NodeBot use for LXMF?"
    for i in "${!RNODE_PORTS[@]}"; do
        printf "    %d) /dev/rnode%d  (%s)\n" $((i+1)) "$i" "${RNODE_LABELS[$i]}"
    done
    echo ""
    CHOSEN=$(pick "Primary rNode for LXMF" "${#RNODE_PORTS[@]}")
    CHOSEN_RNODE_IDX=$((CHOSEN-1))
    CHOSEN_RNODE_PORT="/dev/rnode${CHOSEN_RNODE_IDX}"
fi

# Apply udev rules immediately
sudo udevadm control --reload-rules
sudo udevadm trigger
sleep 1
echo "      udev rules applied: $UDEV_RULES"
if [ -e "$CHOSEN_RNODE_PORT" ]; then
    echo "      Symlink active: $CHOSEN_RNODE_PORT -> $(readlink -f "$CHOSEN_RNODE_PORT")"
else
    echo "      Note: $CHOSEN_RNODE_PORT will appear once the device is plugged in."
fi
echo ""

# ── Step 5: Frequency configuration ──────────────────────────
echo "[5/7] rNode frequency configuration"
echo ""
echo "  Select your region:"
for i in "${!REGION_NAMES[@]}"; do
    printf "    %2d) %s\n" $((i+1)) "${REGION_NAMES[$i]}"
done
echo ""

REGION_IDX=$(pick "Region" "${#REGION_NAMES[@]}")

if (( REGION_IDX == ${#REGION_NAMES[@]} )); then
    printf "  Frequency (Hz, e.g. 915000000): "; read -r FREQ || true
    printf "  Bandwidth (Hz, e.g. 125000):    "; read -r BW   || true
    printf "  Spreading factor (e.g. 8):      "; read -r SF   || true
    LOCATION="Custom"
else
    arr_name="SETTINGS_${REGION_IDX}[@]"
    region_settings=("${!arr_name}")
    count="${#region_settings[@]}"

    if (( count == 1 )); then
        SETTING="${region_settings[0]}"
    else
        echo ""
        echo "  Available settings for ${REGION_NAMES[$((REGION_IDX-1))]}:"
        for i in "${!region_settings[@]}"; do
            IFS='|' read -r f b s d <<< "${region_settings[$i]}"
            printf "    %d) freq=%-12s bw=%-8s sf=%-3s  %s\n" $((i+1)) "$f" "$b" "$s" "$d"
        done
        echo ""
        SETTING_IDX=$(pick "Setting" "$count")
        SETTING="${region_settings[$((SETTING_IDX-1))]}"
    fi
    IFS='|' read -r FREQ BW SF LOCATION <<< "$SETTING"
fi

printf "  TX power in dBm (default 17, max 22 for most rNodes): "
read -r TXPOWER || true
TXPOWER="${TXPOWER:-17}"
[[ "$TXPOWER" =~ ^[0-9]+$ ]] || { echo "  Invalid, using 17."; TXPOWER=17; }

echo ""
echo "  ── Selected configuration ──────────────────────────"
printf "  Device      : %s\n" "$CHOSEN_RNODE_PORT"
printf "  Frequency   : %s Hz\n" "$FREQ"
printf "  Bandwidth   : %s Hz\n" "$BW"
printf "  Spreading   : SF%s\n" "$SF"
printf "  TX Power    : %s dBm\n" "$TXPOWER"
printf "  Location ref: %s\n" "$LOCATION"
echo "  ────────────────────────────────────────────────────"
echo ""
printf "  Write this to ~/.reticulum/config? (yes/no): "
read -r CONFIRM || true
if [[ "${CONFIRM,,}" != "yes" ]]; then
    echo "  Skipping Reticulum config write."
else
    if [ -f "$RNS_CONFIG" ]; then
        cp "$RNS_CONFIG" "${RNS_CONFIG}.bak"
        echo "  Existing config backed up to ${RNS_CONFIG}.bak"
    fi
    mkdir -p "$(dirname "$RNS_CONFIG")"
    cat > "$RNS_CONFIG" <<RNSEOF
# Reticulum configuration — written by NodeBot LXMF installer
# Region: ${REGION_NAMES[$((REGION_IDX-1))]}  |  Ref: $LOCATION
# Using stable udev symlink: $CHOSEN_RNODE_PORT
# Devices reconnect automatically when unplugged/replugged.

[reticulum]
  enable_transport = False
  share_instance = Yes
  shared_instance_port = 37428
  instance_control_port = 37429
  panic_on_interface_error = No

[logging]
  loglevel = 4

[interfaces]

  [[RNodeInterface]]
    type = RNodeInterface
    interface_enabled = True
    outgoing = True
    port = $CHOSEN_RNODE_PORT
    frequency = $FREQ
    bandwidth = $BW
    spreadingfactor = $SF
    txpower = $TXPOWER
    codingrate = 5
RNSEOF
    echo "  Written: $RNS_CONFIG"
fi

# ── Step 6: Install nomadnet.service ─────────────────────────
echo ""
echo "[6/7] Installing nomadnet.service..."

sudo tee /etc/systemd/system/nomadnet.service > /dev/null <<EOF
[Unit]
Description=NomadNet LXMF Node (RNS shared instance owner)
Documentation=https://github.com/markqvist/NomadNet
After=network.target
Wants=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$HOME
ExecStart=$NOMADNET_BIN --daemon --console
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

echo "      Written: /etc/systemd/system/nomadnet.service"

# ── Step 7: Install nodebot.service ──────────────────────────
echo "[7/7] Installing nodebot.service..."

sudo tee /etc/systemd/system/nodebot.service > /dev/null <<EOF
[Unit]
Description=NodeBot Multi-Protocol Mesh Relay System
Documentation=https://github.com/JamesM92/NodeBot
After=network.target nomadnet.service
Wants=network.target
Requires=nomadnet.service

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR/src
ExecStartPre=/bin/bash $WAIT_SCRIPT 30
ExecStart=$VENV_PYTHON $PROJECT_DIR/src/runbot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

echo "      Written: /etc/systemd/system/nodebot.service"

# ── Enable services ───────────────────────────────────────────
echo ""
echo "Enabling services..."
sudo systemctl daemon-reload
sudo systemctl enable nomadnet.service
sudo systemctl enable nodebot.service
echo "  nomadnet.service — enabled"
echo "  nodebot.service  — enabled"

echo ""
echo "================================================"
echo "  Installation complete."
echo "================================================"
echo ""
echo "  rNode stable paths:"
for i in "${!RNODE_PORTS[@]}"; do
    printf "    /dev/rnode%d  (%s)\n" "$i" "${RNODE_LABELS[$i]}"
done
echo ""
echo "  Start services now:"
echo "    sudo systemctl start nomadnet"
echo "    sudo systemctl start nodebot"
echo ""
echo "  Or reboot and both will start automatically."
echo ""
echo "  Useful commands:"
echo "    systemctl status nomadnet nodebot"
echo "    journalctl -u nomadnet -f"
echo "    journalctl -u nodebot -f"
echo ""
