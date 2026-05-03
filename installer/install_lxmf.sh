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
# Loaded from docs/radio_settings/presets.toml via Python (tomllib, stdlib ≥3.11).
# Python writes bash-sourceable REGION_NAMES and SETTINGS_N arrays to a tempfile.
# Downstream code (region menu, sub-preset menu) is unchanged.
_PRESETS_TMP="$(mktemp)"
"$VENV_PYTHON" - "$PROJECT_DIR/docs/radio_settings/presets.toml" "$_PRESETS_TMP" <<'PYEOF'
import sys, tomllib

toml_path, out_path = sys.argv[1], sys.argv[2]
with open(toml_path, "rb") as fh:
    data = tomllib.load(fh)

presets = data["lxmf"]["presets"]
region_names = [p["region"] for p in presets] + ["Manual entry (custom values)"]

lines = []
quoted_names = " ".join(f'"{n}"' for n in region_names)
lines.append(f"REGION_NAMES=({quoted_names})")

for idx, preset in enumerate(presets, start=1):
    entries = preset["nodes"]
    quoted_entries = " ".join(
        f'"{e["freq_hz"]}|{e["bw_hz"]}|{e["sf"]}|{e["description"]}"'
        for e in entries
    )
    lines.append(f"SETTINGS_{idx}=({quoted_entries})")

with open(out_path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
PYEOF
# shellcheck source=/dev/null
source "$_PRESETS_TMP"
rm -f "$_PRESETS_TMP"

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
echo "[1/8] Fixing venv permissions..."
find "$VENV/bin" -type f ! -perm /111 -exec chmod +x {} \;
echo "      Done."

# ── Step 2: Install / upgrade NomadNet ───────────────────────
echo "[2/8] Installing NomadNet globally (pip3 --user)..."
# Try plain install first; fall back to --break-system-packages on systems
# that enforce PEP 668 (Debian Bookworm / Raspberry Pi OS 12+).
if ! pip3 install --user --upgrade "nomadnet>=0.9.9" 2>/dev/null; then
    pip3 install --user --upgrade --break-system-packages "nomadnet>=0.9.9"
fi
echo "      NomadNet installed to $USER_BIN"

# ── Step 3: Connection mode + rNode detection ────────────────
echo ""
echo "[3/8] Reticulum connection setup"
echo ""
echo "  How should this node connect to the Reticulum network?"
echo "    1) rNode only  (LoRa radio hardware)"
echo "    2) TCP only    (no radio hardware required)"
echo "    3) rNode + TCP (radio hardware plus an upstream TCP server)"
echo ""
CONN_MODE=$(pick "Connection mode" 3)
USE_RNODE=false
USE_TCP=false
[[ "$CONN_MODE" == 1 || "$CONN_MODE" == 3 ]] && USE_RNODE=true
[[ "$CONN_MODE" == 2 || "$CONN_MODE" == 3 ]] && USE_TCP=true

RNODE_PORTS=()
RNODE_LABELS=()
RNODE_FW_VERS=()
CHOSEN_RNODE_PORT=""
CHOSEN_RNODE_IDX=0

if $USE_RNODE; then
    echo ""
    echo "  Detecting rNodes on USB ports..."
    echo ""

    NOMADNET_WAS_RUNNING=false
    if systemctl is-active --quiet nomadnet 2>/dev/null; then
        echo "      Stopping nomadnet to free serial ports for probing..."
        sudo systemctl stop nomadnet nodebot 2>/dev/null || true
        NOMADNET_WAS_RUNNING=true
        sleep 2
    fi

    for port in /dev/ttyUSB* /dev/ttyACM*; do
        [ -e "$port" ] || continue
        model=$(udev_prop "$port" "ID_MODEL")
        vendor=$(udev_prop "$port" "ID_VENDOR")
        serial=$(udev_prop "$port" "ID_SERIAL_SHORT")
        printf "  Probing %-16s [%s %s S/N:%s] ... " \
            "$port" "$vendor" "$model" "${serial:-none}"
        rnode_info=$(timeout 6 "$RNODECONF_BIN" "$port" --info 2>/dev/null)
        if echo "$rnode_info" | grep -qi "firmware"; then
            fw_ver=$(echo "$rnode_info" | grep -i "Firmware version" | awk '{print $NF}')
            echo "rNode detected (firmware ${fw_ver:-unknown})"
            RNODE_PORTS+=("$port")
            RNODE_LABELS+=("$vendor $model (S/N: ${serial:-none})")
            RNODE_FW_VERS+=("${fw_ver:-unknown}")
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
        printf "  Continue with manual port entry? (yes/no): "
        read -r CONTINUE || true
        if [[ "${CONTINUE,,}" != "yes" ]]; then
            exit 1
        fi
        printf "  Enter port (e.g. /dev/ttyUSB0): "
        read -r MANUAL_PORT || true
        RNODE_PORTS=("$MANUAL_PORT")
        RNODE_LABELS=("manual entry")
    fi
fi

# ── Step 4: udev rules ───────────────────────────────────────
echo ""
if $USE_RNODE; then
    echo "[4/8] Creating udev symlinks for stable device naming..."
    echo ""
    echo "  This creates /dev/rnode0, /dev/rnode1 ... symlinks that"
    echo "  follow each device regardless of which USB port it uses."
    echo "  RNS will connect to /dev/rnode0 (or the chosen device)"
    echo "  and reconnect automatically when the device is re-attached."
    echo ""

    sudo tee "$UDEV_RULES" > /dev/null <<'UDEV_HEADER'
# rNode stable device naming — written by NodeBot LXMF installer
# Each rule creates /dev/rnodeN tied to a specific device's identity.
# When the device is unplugged and replugged (any USB port), the
# symlink is recreated and RNS reconnects automatically.
UDEV_HEADER

    for i in "${!RNODE_PORTS[@]}"; do
        port="${RNODE_PORTS[$i]}"
        label="${RNODE_LABELS[$i]}"
        symlink="rnode${i}"

        id_serial=$(udev_prop "$port" "ID_SERIAL")
        id_serial_short=$(udev_prop "$port" "ID_SERIAL_SHORT")
        id_path=$(udev_prop "$port" "ID_PATH")

        echo "  Device $((i+1)): $port — $label"

        if [[ -n "$id_serial" ]]; then
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
                RULE="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"${id_path}\", GROUP=\"dialout\", MODE=\"0660\", SYMLINK+=\"${symlink}\""
            else
                echo "    Unique serial detected — symlink will follow device across ports."
                RULE="SUBSYSTEM==\"tty\", ENV{ID_SERIAL}==\"${id_serial}\", GROUP=\"dialout\", MODE=\"0660\", SYMLINK+=\"${symlink}\""
            fi
        else
            echo "    No USB serial info found, using physical port path."
            RULE="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"${id_path}\", GROUP=\"dialout\", MODE=\"0660\", SYMLINK+=\"${symlink}\""
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

    # ── Firmware compatibility check ─────────────────────────────
    # Firmware 1.86 introduced a radio state response timing change
    # that causes RNS 1.2.x to fail with "Radio state mismatch" and
    # refuse to bring the rNode interface online.
    # Primary fix: patch RNS to add a 0.5s sleep (applied later in
    # step 8).  Fallback: downgrade to KNOWN_GOOD_FW if the user prefers.
    KNOWN_GOOD_FW="1.85"
    CHOSEN_FW="${RNODE_FW_VERS[$CHOSEN_RNODE_IDX]:-unknown}"
    CHOSEN_PHYS="${RNODE_PORTS[$CHOSEN_RNODE_IDX]}"

    _fw_newer_than() {
        # returns 0 (true) if $1 > $2 as dot-separated version numbers
        local a="$1" b="$2"
        [[ "$(printf '%s\n' "$a" "$b" | sort -V | tail -1)" == "$a" && "$a" != "$b" ]]
    }

    if [[ "$CHOSEN_FW" != "unknown" ]] && _fw_newer_than "$CHOSEN_FW" "$KNOWN_GOOD_FW"; then
        echo "  ⚠  Firmware $CHOSEN_FW detected on $CHOSEN_PHYS."
        echo "     Firmware $CHOSEN_FW has a timing change that causes a Radio state"
        echo "     mismatch with RNS 1.2.x.  A patch will be applied to RNS later"
        echo "     in the install to fix this — no firmware downgrade needed."
        echo ""
        echo "     If you experience rNode startup failures after install, you can"
        printf "     manually downgrade: rnodeconf %s -U --fw-version %s\n" "$CHOSEN_PHYS" "$KNOWN_GOOD_FW"
        echo ""
    fi

    # Sign the device so future rnodeconf operations work without
    # the "unverified device" warning blocking flashes/updates.
    "$RNODECONF_BIN" "$CHOSEN_PHYS" --sign 2>/dev/null || true
else
    echo "[4/8] Skipping udev setup (no rNode configured)."
fi

# ── Step 5: Interface configuration ──────────────────────────
echo ""
echo "[5/8] Reticulum interface configuration"
echo ""

FREQ="" BW="" SF="" TXPOWER="" LOCATION="" REGION_IDX=0

if $USE_RNODE; then
    echo "  ── rNode frequency ──────────────────────────────────"
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
fi

DEFAULT_TCP_NAME="Michmesh Testnet"
DEFAULT_TCP_HOST="RNS.MichMesh.net"
DEFAULT_TCP_PORT="7822"

TCP_NAME=""
TCP_HOST=""
TCP_PORT=""
if $USE_TCP; then
    echo "  ── TCP server ───────────────────────────────────────"
    echo ""
    echo "  Default server: $DEFAULT_TCP_NAME"
    echo "    Host : $DEFAULT_TCP_HOST"
    echo "    Port : $DEFAULT_TCP_PORT"
    echo ""
    printf "  Press Enter to use the default, or type a different host: "
    read -r TCP_HOST || true
    if [[ -z "$TCP_HOST" ]]; then
        TCP_HOST="$DEFAULT_TCP_HOST"
        TCP_PORT="$DEFAULT_TCP_PORT"
        TCP_NAME="$DEFAULT_TCP_NAME"
    else
        TCP_NAME="$TCP_HOST"
        printf "  Port (default 7822): "
        read -r TCP_PORT || true
        TCP_PORT="${TCP_PORT:-7822}"
        [[ "$TCP_PORT" =~ ^[0-9]+$ ]] || { echo "  Invalid port, using 7822."; TCP_PORT=7822; }
    fi
    echo ""
    echo "  Note: additional TCP servers can be added later by editing"
    echo "  ~/.reticulum/config and adding more [[TCPClientInterface]] blocks."
    echo ""
fi

# ── Transport / bridging ─────────────────────────────
ENABLE_TRANSPORT=false
if $USE_RNODE && $USE_TCP; then
    echo "  ── Network transport ────────────────────────────────"
    echo ""
    echo "  Enable Reticulum transport? This forwards packets between"
    echo "  the LoRa (rNode) and TCP interfaces, bridging them so that"
    echo "  LoRa mesh traffic reaches the TCP network and vice versa."
    echo ""
    printf "  Enable transport / bridging? (yes/no): "
    read -r TRANSPORT_ANS || true
    [[ "${TRANSPORT_ANS,,}" == "yes" ]] && ENABLE_TRANSPORT=true
    echo ""
fi

echo "  ── Selected configuration ──────────────────────────"
if $USE_RNODE; then
    printf "  Device      : %s\n" "$CHOSEN_RNODE_PORT"
    printf "  Frequency   : %s Hz\n" "$FREQ"
    printf "  Bandwidth   : %s Hz\n" "$BW"
    printf "  Spreading   : SF%s\n" "$SF"
    printf "  TX Power    : %s dBm\n" "$TXPOWER"
    printf "  Location ref: %s\n" "$LOCATION"
fi
[[ -n "$TCP_HOST" ]] && printf "  TCP server  : %s  (%s:%s)\n" "$TCP_NAME" "$TCP_HOST" "$TCP_PORT"
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

    if $USE_RNODE; then
        HEADER_COMMENT="# Region: ${REGION_NAMES[$((REGION_IDX-1))]}  |  Ref: $LOCATION
# Using stable udev symlink: $CHOSEN_RNODE_PORT"
    else
        HEADER_COMMENT="# TCP-only mode — no LoRa radio hardware"
    fi

    RNS_TRANSPORT_VALUE="False"
    $ENABLE_TRANSPORT && RNS_TRANSPORT_VALUE="True"

    cat > "$RNS_CONFIG" <<RNSEOF
# Reticulum configuration — written by NodeBot LXMF installer
$HEADER_COMMENT

[reticulum]
  enable_transport = $RNS_TRANSPORT_VALUE
  share_instance = Yes
  shared_instance_port = 37428
  instance_control_port = 37429
  panic_on_interface_error = No

[logging]
  loglevel = 4

[interfaces]
RNSEOF

    if $USE_RNODE; then
        cat >> "$RNS_CONFIG" <<RNODEEOF

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
RNODEEOF
    fi

    if [[ -n "$TCP_HOST" ]]; then
        cat >> "$RNS_CONFIG" <<TCPEOF

  [[$TCP_NAME]]
    type = TCPClientInterface
    interface_enabled = True
    outgoing = True
    target_host = $TCP_HOST
    target_port = $TCP_PORT
    # To add more TCP servers, copy this block and change the name, host, and port.
    # See: https://reticulum.network/manual/interfaces.html#tcp-client-interface
TCPEOF
    fi

    echo "  Written: $RNS_CONFIG"
fi

# ── Step 6: Install nomadnet.service ─────────────────────────
echo ""
echo "[6/8] Installing nomadnet.service..."

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

# Start NomadNet now so it generates its config file, then we can patch it.
NOMADNET_CONFIG="$HOME/.nomadnetwork/config"
BOT_NAME=$(python3 -c "
import configparser
c = configparser.ConfigParser()
c.read('$PROJECT_DIR/config.ini')
print(c.get('bot', 'name', fallback='NodeBot').strip())
")
echo ""
echo "      Starting NomadNet to generate initial config..."
sudo systemctl daemon-reload
sudo systemctl start nomadnet
for i in $(seq 1 20); do
    [ -f "$NOMADNET_CONFIG" ] && break
    sleep 1
done

if [ -f "$NOMADNET_CONFIG" ]; then
    sed -i "s/^node_name = .*/node_name = $BOT_NAME/" "$NOMADNET_CONFIG"
    echo "      NomadNet node_name set to: $BOT_NAME"
else
    echo "      Warning: NomadNet config was not generated in time."
    echo "      After first start, run:"
    echo "        sed -i 's/^node_name = .*/node_name = $BOT_NAME/' $NOMADNET_CONFIG"
fi

# ── Step 7: NomadNet node page + propagation node ────────────
echo ""
echo "[7/8] NomadNet node configuration"
echo ""

# Propagation node prompt
echo "  ── LXMF propagation node ───────────────────────────"
echo ""
echo "  A propagation node stores and forwards LXMF messages for"
echo "  nodes that are temporarily offline. It also bridges messages"
echo "  between LoRa and TCP — nodes on LoRa can receive messages"
echo "  sent via TCP, and vice versa."
echo ""
printf "  Run as an LXMF propagation node? (yes/no): "
read -r RUN_PROP_NODE || true
echo ""

# Node page prompt
echo "  ── Node page ───────────────────────────────────────"
echo ""
echo "  A node page lets LXMF users browse this node's addresses,"
echo "  supported protocols, and README directly from the network."
echo "  NomadNet will announce the page to the Reticulum network."
echo ""
printf "  Host a node page? (yes/no): "
read -r HOST_PAGE || true
echo ""

PAGES_DIR="$HOME/.nomadnetwork/storage/pages"
NODE_PAGE="$PAGES_DIR/nodebot/nodebot.mu"

if [[ "${HOST_PAGE,,}" == "yes" ]]; then

echo "  Writing node pages..."

# installer/lxmf_pages/ mirrors the deployed pages/ directory structure.
# Each .mu file is copied to the same relative path under PAGES_DIR, with
# PROJECT_DIR_PLACEHOLDER substituted. index.mu is skipped if already present
# (preserves any custom landing page the user may have configured).
LXMF_PAGES_DIR="$PROJECT_DIR/installer/lxmf_pages"
while IFS= read -r -d '' _template; do
    _rel="${_template#"$LXMF_PAGES_DIR/"}"
    _page="$PAGES_DIR/$_rel"
    mkdir -p "$(dirname "$_page")"

    # index.mu: write only if absent so custom landing pages are preserved
    if [[ "$(basename "$_template")" == "index.mu" ]] && [ -f "$_page" ]; then
        echo "      Skipped: $_page (custom page preserved)"
        echo "      Node pages accessible at: /page/nodebot/nodebot.mu"
        continue
    fi

    sed "s|PROJECT_DIR_PLACEHOLDER|$PROJECT_DIR|g" "$_template" > "$_page"
    chmod +x "$_page"
    echo "      Written: $_page"
done < <(find "$LXMF_PAGES_DIR" -name "*.mu" -print0)

# Enable node hosting in NomadNet config
if [ -f "$NOMADNET_CONFIG" ]; then
    sed -i "s/^enable_node = .*/enable_node = yes/" "$NOMADNET_CONFIG"
    echo "      NomadNet node hosting enabled."
else
    echo "      Note: patch NomadNet config manually once generated:"
    echo "        sed -i 's/^enable_node = .*/enable_node = yes/' $NOMADNET_CONFIG"
fi

else
    # User declined hosting — ensure node page serving is off
    if [ -f "$NOMADNET_CONFIG" ]; then
        sed -i "s/^enable_node = .*/enable_node = No/" "$NOMADNET_CONFIG"
    fi
    echo "  Node page skipped. NomadNet will run without a hosted page."
    echo "  To enable later: set 'enable_node = yes' in $NOMADNET_CONFIG"
fi

# Apply propagation node setting
if [ -f "$NOMADNET_CONFIG" ]; then
    if [[ "${RUN_PROP_NODE,,}" == "yes" ]]; then
        sed -i "s/^disable_propagation = .*/disable_propagation = No/" "$NOMADNET_CONFIG"
        echo "  LXMF propagation node enabled."
    else
        sed -i "s/^disable_propagation = .*/disable_propagation = Yes/" "$NOMADNET_CONFIG"
        echo "  LXMF propagation node disabled."
    fi
else
    echo "  Note: set 'disable_propagation = No' in $NOMADNET_CONFIG to enable propagation node."
fi

# ── RNS firmware 1.86 compatibility patch ────────────────────
# Firmware 1.86+ takes slightly longer to confirm RADIO_STATE_ON.
# RNS 1.2.x calls validateRadioState() immediately after initRadio()
# with no gap, causing a spurious "Radio state mismatch" failure.
# A 0.5s sleep between the two calls resolves this.  The patch is
# applied to both the NodeBot venv and the system RNS used by
# NomadNet.  Applied here (before NomadNet restart) so it is active
# on first boot, and re-applied on each install run to survive
# 'pip upgrade rns'.
_patch_rns_interface() {
    local rns_iface="$1"
    [ -f "$rns_iface" ] || return 0
    if grep -q "Allow firmware time to confirm RADIO_STATE_ON" "$rns_iface"; then
        echo "  Already patched: $rns_iface"
        return 0
    fi
    # Insert sleep(0.5) between initRadio() and validateRadioState()
    sed -i 's/^\( *\)self\.initRadio()\( *\)$/\1self.initRadio()\n\1sleep(0.5)  # Allow firmware time to confirm RADIO_STATE_ON (needed for fw >= 1.86)/' \
        "$rns_iface" && echo "  Patched: $rns_iface" || echo "  Patch failed: $rns_iface"
}

echo ""
echo "Applying RNS rNode firmware compatibility patch..."
VENV_RNS_IFACE="$VENV_DIR/lib/python*/site-packages/RNS/Interfaces/RNodeInterface.py"
SYS_RNS_IFACE=$(python3 -c "import RNS, os; print(os.path.join(os.path.dirname(RNS.__file__), 'Interfaces', 'RNodeInterface.py'))" 2>/dev/null || true)

for f in $VENV_RNS_IFACE; do _patch_rns_interface "$f"; done
[ -n "$SYS_RNS_IFACE" ] && _patch_rns_interface "$SYS_RNS_IFACE"

# Restart nomadnet to apply all config changes (with RNS patch already in place)
sudo systemctl restart nomadnet
echo "  NomadNet restarted with updated config."

# ── rNode interface sanity check ─────────────────────────────
if $USE_RNODE; then
    echo ""
    echo "  Checking rNode interface..."
    RNODE_OK=false
    for i in $(seq 1 15); do
        if journalctl -u nomadnet -n 50 --no-pager 2>/dev/null \
                | grep -q "is configured and powered up"; then
            RNODE_OK=true
            break
        fi
        sleep 1
    done

    if $RNODE_OK; then
        echo "  ✓ rNode interface is up."
    else
        # Check for the known firmware mismatch error
        if journalctl -u nomadnet -n 50 --no-pager 2>/dev/null \
                | grep -q "Radio state mismatch"; then
            echo ""
            echo "  ✗ ERROR: Radio state mismatch — rNode firmware timing"
            echo "    incompatibility with RNS 1.2.x. The RNS patch should"
            echo "    have fixed this; it may not have taken effect yet."
            echo ""
            echo "    Try restarting NomadNet manually:"
            echo "      sudo systemctl restart nomadnet"
            echo ""
            echo "    If the error persists, downgrade the firmware:"
            echo "      sudo systemctl stop nomadnet"
            printf "      rnodeconf %s --sign\n" \
                "${RNODE_PORTS[$CHOSEN_RNODE_IDX]:-/dev/rnode0}"
            printf "      rnodeconf %s -U --fw-version %s\n" \
                "${RNODE_PORTS[$CHOSEN_RNODE_IDX]:-/dev/rnode0}" "$KNOWN_GOOD_FW"
            echo "      sudo systemctl start nomadnet"
        else
            echo "  ⚠  rNode interface did not come up within 15 seconds."
            echo "     Check: journalctl -u nomadnet -n 30"
        fi
    fi
fi

# ── Step 8: Install nodebot.service ──────────────────────────
echo ""
echo "[8/8] Installing nodebot.service..."

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
WorkingDirectory=$PROJECT_DIR
ExecStartPre=/bin/bash $WAIT_SCRIPT 30
ExecStart=$VENV_PYTHON $PROJECT_DIR/runbot.py
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
