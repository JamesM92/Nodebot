#!/bin/bash
# ============================================================
# NodeBot Meshtastic Installer
#
# - Installs the meshtastic Python package into the project venv
# - Probes USB ports to auto-detect the Meshtastic radio
# - Creates a stable udev symlink (/dev/meshtastic0) tied to
#   the device's USB serial number
# - Configures environmental telemetry source
# - Writes [meshtastic] and [telemetry] sections in config.ini
#
# Run AFTER install_nodebot.sh:
#   bash installer/install_meshtastic.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/.venv"
VENV_PYTHON="$VENV/bin/python3"
VENV_PIP="$VENV/bin/pip3"
CONFIG_INI="$PROJECT_DIR/config.ini"
DEFAULT_BAUD=115200

echo ""
echo "================================================"
echo "  NodeBot Meshtastic Installer"
echo "================================================"
echo "  Project : $PROJECT_DIR"
echo "  Venv    : $VENV"
echo "================================================"
echo ""

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

# ── Step 1: Install meshtastic into the project venv ─────────
echo "[1/5] Installing meshtastic Python package..."
"$VENV_PIP" install --upgrade meshtastic
echo "      meshtastic $("$VENV_PIP" show meshtastic 2>/dev/null | awk '/^Version:/{print $2}') installed."

# ── Step 2: Detect Meshtastic device + udev symlink ──────────
echo ""
echo "[2/5] Detecting Meshtastic radio on USB ports..."
echo ""

PROBE_SCRIPT=$(cat <<'PYEOF'
import sys, time, threading

port = sys.argv[1]
result = {"v": "ERR:timeout"}
done = threading.Event()

def probe():
    try:
        import meshtastic.serial_interface
        iface = meshtastic.serial_interface.SerialInterface(devPath=port, noProto=False)
        time.sleep(3)
        name = iface.getLongName() if hasattr(iface, "getLongName") else "unknown"
        hw = str(getattr(getattr(iface, "myInfo", None), "hw_model", "unknown"))
        iface.close()
        result["v"] = f"OK:{name}:{hw}"
    except Exception as e:
        result["v"] = f"ERR:{e}"
    finally:
        done.set()

t = threading.Thread(target=probe, daemon=True)
t.start()
done.wait(timeout=12)
print(result["v"])
PYEOF
)

MESH_PORTS=()
MESH_LABELS=()

for port in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$port" ] || continue

    model=$(udev_prop "$port" "ID_MODEL")
    vendor=$(udev_prop "$port" "ID_VENDOR")
    serial=$(udev_prop "$port" "ID_SERIAL_SHORT")

    printf "  Probing %-16s [%s %s S/N:%s] ... " \
        "$port" "$vendor" "$model" "${serial:-none}"

    result=$("$VENV_PYTHON" -c "$PROBE_SCRIPT" "$port" 2>/dev/null)

    if [[ "$result" == OK:* ]]; then
        node_info="${result#OK:}"
        echo "Meshtastic: ${node_info}"
        MESH_PORTS+=("$port")
        MESH_LABELS+=("$vendor $model (S/N: ${serial:-none}) — ${node_info}")
    else
        echo "no response"
    fi
done

echo ""

if (( ${#MESH_PORTS[@]} == 0 )); then
    echo "  No Meshtastic radio detected."
    echo "  Make sure the device is plugged in and running Meshtastic firmware."
    echo ""
    printf "  Continue with manual port entry? (yes/no): "
    read -r CONT || true
    if [[ "${CONT,,}" != "yes" ]]; then exit 1; fi
    while true; do
        printf "  Enter port (e.g. /dev/ttyUSB0): "
        read -r MANUAL_PORT || true
        [[ "$MANUAL_PORT" == /dev/* ]] && break
        echo "  Port must start with /dev/"
    done
    MESH_PORTS=("$MANUAL_PORT")
    MESH_LABELS=("manual entry")
fi

CHOSEN_IDX=0
if (( ${#MESH_PORTS[@]} > 1 )); then
    echo "  Multiple Meshtastic devices found:"
    for i in "${!MESH_PORTS[@]}"; do
        printf "    %d) %s  (%s)\n" $((i+1)) "${MESH_PORTS[$i]}" "${MESH_LABELS[$i]}"
    done
    echo ""
    SEL=$(pick "Primary Meshtastic radio" "${#MESH_PORTS[@]}")
    CHOSEN_IDX=$((SEL-1))
fi

CHOSEN_PORT="${MESH_PORTS[$CHOSEN_IDX]}"

# ── udev symlink for stable /dev/meshtastic0 ─────────────────
echo ""
echo "  Creating udev symlink /dev/meshtastic0..."

id_serial=$(udev_prop "$CHOSEN_PORT" "ID_SERIAL")
id_serial_short=$(udev_prop "$CHOSEN_PORT" "ID_SERIAL_SHORT")
id_path=$(udev_prop "$CHOSEN_PORT" "ID_PATH")

generic_serials=("0001" "0000" "1234567890" "ABCDEF" "")
is_generic=false
for g in "${generic_serials[@]}"; do
    if [[ "$id_serial_short" == "$g" ]]; then is_generic=true; break; fi
done

if [[ -n "$id_serial" ]] && ! $is_generic; then
    RULE="SUBSYSTEM==\"tty\", ENV{ID_SERIAL}==\"${id_serial}\", SYMLINK+=\"meshtastic0\""
    echo "  Unique serial — symlink follows device across ports."
else
    RULE="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"${id_path}\", SYMLINK+=\"meshtastic0\""
    echo ""
    echo "  *** Generic serial number detected (e.g. CP2102 clones often use '0001') ***"
    echo "  The symlink /dev/meshtastic0 is tied to the physical USB port, not the"
    echo "  device itself. If you plug the Meshtastic radio into a different USB socket"
    echo "  it will no longer be found as /dev/meshtastic0 and NodeBot will fail to"
    echo "  connect. Always use the same USB port, or re-run this installer if you move it."
    echo ""
    echo "  The same limitation applies if other devices on your system share this"
    echo "  serial number (e.g. MeshCore on the same CP2102 chip). Each device must"
    echo "  stay in its assigned USB port."
    echo ""
fi

sudo tee /etc/udev/rules.d/99-meshtastic.rules > /dev/null <<UDEV
# Meshtastic stable device naming — written by NodeBot Meshtastic installer
# Creates /dev/meshtastic0 tied to device identity.

# Device: $CHOSEN_PORT — ${MESH_LABELS[$CHOSEN_IDX]}
$RULE
UDEV

sudo udevadm control --reload-rules
sudo udevadm trigger
sleep 1

ACTIVE_PORT="$CHOSEN_PORT"
if [ -e "/dev/meshtastic0" ]; then
    echo "  Symlink active: /dev/meshtastic0 -> $(readlink -f /dev/meshtastic0)"
    ACTIVE_PORT="/dev/meshtastic0"
else
    echo "  Note: /dev/meshtastic0 will appear once the device is plugged in."
fi
echo ""

# ── Legal disclaimer ──────────────────────────────────────────
echo "  ╔═════════════════════════════════════════════════════╗"
echo "  ║               ⚠  LEGAL NOTICE  ⚠                   ║"
echo "  ║                                                     ║"
echo "  ║  Radio frequency use is regulated by law and        ║"
echo "  ║  varies by country. Meshtastic region codes set     ║"
echo "  ║  the frequency band, duty cycle, and power cap      ║"
echo "  ║  for your device.                                   ║"
echo "  ║                                                     ║"
echo "  ║  YOU are solely responsible for choosing the        ║"
echo "  ║  correct region for your jurisdiction and           ║"
echo "  ║  complying with local radio laws.                   ║"
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

# ── Step 3: Radio configuration ──────────────────────────────
echo "[3/5] Radio configuration"
echo ""
echo "  Select the region that matches your country."
echo "  This sets the frequency band, duty cycle, and transmit power limit."
echo ""
echo "  Region          Frequency (MHz)       Duty  Power"
echo "  ──────────────  ────────────────────  ────  ─────"
echo "   1) US           902.0 – 928.0         100%  30 dBm"
echo "   2) EU_433       433.0 – 434.0         10%   12 dBm  (hourly duty limit)"
echo "   3) EU_868       869.4 – 869.65        10%   27 dBm  (hourly duty limit)"
echo "   4) ANZ          915.0 – 928.0         100%  30 dBm  (Australia & NZ)"
echo "   5) ANZ_433      433.05 – 434.79       100%  14 dBm  (Australia & NZ)"
echo "   6) CN           470.0 – 510.0         100%  19 dBm  (China)"
echo "   7) JP           920.8 – 927.8         100%  16 dBm  (Japan)"
echo "   8) KR           920.0 – 923.0         100%  —       (Korea)"
echo "   9) TW           920.0 – 925.0         100%  27 dBm  (Taiwan)"
echo "  10) RU           868.7 – 869.2         100%  20 dBm  (Russia)"
echo "  11) IN           865.0 – 867.0         100%  30 dBm  (India)"
echo "  12) NZ_865       864.0 – 868.0         100%  36 dBm  (New Zealand 865 MHz)"
echo "  13) TH           920.0 – 925.0         100%  16 dBm  (Thailand)"
echo "  14) UA_433       433.0 – 434.7         10%   10 dBm  (Ukraine 433 MHz)"
echo "  15) UA_868       868.0 – 868.6         1%    14 dBm  (Ukraine 868 MHz — very restricted)"
echo "  16) MY_433       433.0 – 435.0         100%  20 dBm  (Malaysia 433 MHz)"
echo "  17) MY_919       919.0 – 924.0         100%  27 dBm  (Malaysia 919 MHz)"
echo "  18) SG_923       917.0 – 925.0         100%  20 dBm  (Singapore)"
echo "  19) KZ_433       433.075 – 434.775     100%  10 dBm  (Kazakhstan 433 MHz)"
echo "  20) KZ_863       863.0 – 868.0         100%  30 dBm  (Kazakhstan 863 MHz)"
echo "  21) BR_902       902.0 – 907.5         100%  30 dBm  (Brazil)"
echo "  22) PH_433       433.0 – 434.7         100%  10 dBm  (Philippines 433 MHz)"
echo "  23) PH_868       868.0 – 869.4         100%  14 dBm  (Philippines 868 MHz)"
echo "  24) PH_915       915.0 – 918.0         100%  24 dBm  (Philippines 915 MHz)"
echo "  25) NP_865       865.0 – 868.0         100%  —       (Nepal)"
echo "  26) LORA_24      2400.0 – 2483.5       100%  10 dBm  (2.4 GHz worldwide)"
echo ""

REGION_SEL=$(pick "Region" 26)
case "$REGION_SEL" in
     1) RADIO_REGION="US"      ;;  2) RADIO_REGION="EU_433"  ;;
     3) RADIO_REGION="EU_868"  ;;  4) RADIO_REGION="ANZ"     ;;
     5) RADIO_REGION="ANZ_433" ;;  6) RADIO_REGION="CN"      ;;
     7) RADIO_REGION="JP"      ;;  8) RADIO_REGION="KR"      ;;
     9) RADIO_REGION="TW"      ;; 10) RADIO_REGION="RU"      ;;
    11) RADIO_REGION="IN"      ;; 12) RADIO_REGION="NZ_865"  ;;
    13) RADIO_REGION="TH"      ;; 14) RADIO_REGION="UA_433"  ;;
    15) RADIO_REGION="UA_868"  ;; 16) RADIO_REGION="MY_433"  ;;
    17) RADIO_REGION="MY_919"  ;; 18) RADIO_REGION="SG_923"  ;;
    19) RADIO_REGION="KZ_433"  ;; 20) RADIO_REGION="KZ_863"  ;;
    21) RADIO_REGION="BR_902"  ;; 22) RADIO_REGION="PH_433"  ;;
    23) RADIO_REGION="PH_868"  ;; 24) RADIO_REGION="PH_915"  ;;
    25) RADIO_REGION="NP_865"  ;; 26) RADIO_REGION="LORA_24" ;;
esac

# Warn about duty-cycle-restricted regions
if [[ "$RADIO_REGION" == "EU_433" || "$RADIO_REGION" == "EU_868" ]]; then
    echo "  ⚠  ${RADIO_REGION}: 10% hourly duty cycle — device will pause transmitting if limit is hit."
fi
if [[ "$RADIO_REGION" == "UA_868" ]]; then
    echo "  ⚠  UA_868: 1% duty cycle — very restricted transmit time."
fi

echo ""
echo "  ── Modem preset ─────────────────────────────────────"
echo "  Presets trade off speed vs range. Default (LONG_FAST)"
echo "  suits most deployments."
echo ""
echo "    1) SHORT_TURBO    Fastest. Not legal everywhere (500 kHz BW)."
echo "    2) SHORT_FAST"
echo "    3) SHORT_SLOW"
echo "    4) MEDIUM_FAST"
echo "    5) MEDIUM_SLOW"
echo "    6) LONG_FAST      Default. Good balance of speed and range."
echo "    7) LONG_MODERATE"
echo "    8) LONG_SLOW"
echo "    9) VERY_LONG_SLOW Slowest / max range. Not recommended for general use."
echo ""
PRESET_SEL=$(pick "Modem preset" 9)
case "$PRESET_SEL" in
    1) RADIO_PRESET="SHORT_TURBO"    ;;  2) RADIO_PRESET="SHORT_FAST"     ;;
    3) RADIO_PRESET="SHORT_SLOW"     ;;  4) RADIO_PRESET="MEDIUM_FAST"    ;;
    5) RADIO_PRESET="MEDIUM_SLOW"    ;;  6) RADIO_PRESET="LONG_FAST"      ;;
    7) RADIO_PRESET="LONG_MODERATE"  ;;  8) RADIO_PRESET="LONG_SLOW"      ;;
    9) RADIO_PRESET="VERY_LONG_SLOW" ;;
esac

echo ""
echo "  ── Hop limit ────────────────────────────────────────"
echo "  Maximum hops a packet may take through the mesh (0–7)."
echo "  Default is 3, which is suitable for most networks."
echo ""
printf "  Max hops [0-7, default 7]: "
read -r HOP_INPUT || true
if [[ "$HOP_INPUT" =~ ^[0-7]$ ]]; then
    RADIO_HOPS="$HOP_INPUT"
else
    RADIO_HOPS=7
    [[ -n "$HOP_INPUT" ]] && echo "  Invalid entry — using default of 7."
fi

echo ""
echo "  ── Transmit power ───────────────────────────────────"
echo "  0 = use the maximum legal power for your region (recommended)."
echo "  Enter a custom value in dBm only if you have a specific reason."
echo ""
printf "  TX power in dBm [default 0 = max legal]: "
read -r PWR_INPUT || true
if [[ "$PWR_INPUT" =~ ^[0-9]+$ ]]; then
    RADIO_POWER="$PWR_INPUT"
else
    RADIO_POWER=0
    [[ -n "$PWR_INPUT" ]] && echo "  Invalid entry — using 0 (max legal)."
fi

echo ""
echo "  ── Selected radio configuration ─────────────────────"
printf "  Region    : %s\n" "$RADIO_REGION"
printf "  Preset    : %s\n" "$RADIO_PRESET"
printf "  Max hops  : %s\n" "$RADIO_HOPS"
printf "  TX power  : %s\n" "$([ "$RADIO_POWER" -eq 0 ] && echo 'max legal' || echo "${RADIO_POWER} dBm")"
echo "  ─────────────────────────────────────────────────────"
echo ""
printf "  Program these settings onto the radio now? (yes/no): "
read -r DO_PROGRAM || true

if [[ "${DO_PROGRAM,,}" == "yes" ]]; then
    # Check if NodeBot is holding the port — programming will fail if so
    if systemctl is-active --quiet nodebot 2>/dev/null; then
        echo ""
        echo "  NodeBot is currently running and holds the serial port."
        printf "  Stop NodeBot now to free the port? (yes/no): "
        read -r STOP_BOT || true
        if [[ "${STOP_BOT,,}" == "yes" ]]; then
            sudo systemctl stop nodebot
            echo "  NodeBot stopped."
        else
            echo "  Skipping radio programming — port is busy."
            echo "  Settings saved to config.ini and will be applied on next NodeBot start."
            DO_PROGRAM="no"
        fi
    fi
    echo ""
    echo "  Note: writing LoRa config causes the radio to reboot (~30 seconds)."
    echo "  This is normal. NodeBot will reconnect automatically after restart."
    echo ""
    echo "  Programming radio..."

    RADIO_SCRIPT=$(cat <<'PYEOF'
import sys, time, threading

port, region, preset, hops, power = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
result = {"v": "ERR:timeout"}
done = threading.Event()

def configure():
    try:
        import meshtastic.serial_interface
        from meshtastic import config_pb2

        iface = meshtastic.serial_interface.SerialInterface(devPath=port)
        time.sleep(3)

        lora = iface.localNode.localConfig.lora
        lora.region       = config_pb2.Config.LoRaConfig.RegionCode.Value(region)
        lora.modem_preset = config_pb2.Config.LoRaConfig.ModemPreset.Value(preset)
        lora.hop_limit    = hops
        lora.tx_power     = power

        iface.localNode.writeConfig("lora")
        time.sleep(2)
        iface.close()
        result["v"] = "OK"
    except Exception as e:
        result["v"] = f"ERR:{e}"
    finally:
        done.set()

t = threading.Thread(target=configure, daemon=True)
t.start()
done.wait(timeout=30)
print(result["v"])
PYEOF
)

    PROG_RESULT=$("$VENV_PYTHON" -c "$RADIO_SCRIPT" \
        "$ACTIVE_PORT" "$RADIO_REGION" "$RADIO_PRESET" "$RADIO_HOPS" "$RADIO_POWER" 2>/dev/null)

    if [[ "$PROG_RESULT" == "OK" ]]; then
        echo "  Radio programmed successfully."
        echo "  The radio will reboot to apply settings — this takes ~30 seconds."
        echo "  NodeBot reconnects automatically once the radio is back online."
    else
        echo "  ⚠  Programming returned: $PROG_RESULT"
        echo "     Settings saved to config.ini and will be applied on NodeBot startup."
    fi
    # Restart NodeBot if we stopped it to free the port
    if systemctl is-enabled --quiet nodebot 2>/dev/null && ! systemctl is-active --quiet nodebot 2>/dev/null; then
        echo ""
        echo "  Restarting NodeBot..."
        sudo systemctl start nodebot
    fi
else
    echo "  Skipping radio programming."
    echo "  Settings will be applied by NodeBot on first startup."
fi
echo ""

# ── Step 4: Telemetry / environmental data source ────────────
echo "[4/5] Environmental telemetry configuration"
echo ""
echo "  NodeBot can broadcast environmental data (temperature, humidity,"
echo "  barometric pressure) via Meshtastic's telemetry protocol."
echo "  Other Meshtastic nodes and apps (e.g. Meshtastic app) will see it."
echo ""

# Detect weewx
WEEWX_DB=""
WEEWX_FOUND=false
for candidate in /var/lib/weewx/weewx.sdb /home/weewx/weewx.sdb /opt/weewx/weewx.sdb; do
    if [ -f "$candidate" ]; then
        WEEWX_DB="$candidate"
        WEEWX_FOUND=true
        break
    fi
done

# Detect I2C sensors (informational)
I2C_INFO=""
if command -v i2cdetect >/dev/null 2>&1; then
    I2C_RAW=$(i2cdetect -y 1 2>/dev/null || true)
    declare -A I2C_KNOWN
    I2C_KNOWN["76"]="BME280/BMP280 at 0x76 (temp/pressure/humidity)"
    I2C_KNOWN["77"]="BME280/BMP280 at 0x77 (temp/pressure/humidity)"
    I2C_KNOWN["44"]="SHT31 at 0x44 (temp/humidity)"
    I2C_KNOWN["45"]="SHT31 at 0x45 (temp/humidity)"
    I2C_KNOWN["40"]="HDC1080/HTU21D at 0x40 (temp/humidity)"
    I2C_KNOWN["38"]="AHT10 at 0x38 (temp/humidity)"
    for addr in "${!I2C_KNOWN[@]}"; do
        if echo "$I2C_RAW" | grep -qiE "(^| )${addr}( |$)"; then
            I2C_INFO="${I2C_INFO}  ⮡ I2C sensor detected: ${I2C_KNOWN[$addr]}\n"
        fi
    done
    if [[ -n "$I2C_INFO" ]]; then
        echo "  I2C bus scan:"
        printf "%b" "$I2C_INFO"
        echo "  → Use the 'external script' option to read from I2C sensors."
        echo "    A sample script is shown after your selection."
        echo ""
    fi
fi

# Build option list
TEL_OPT_NUM=0
declare -A TEL_OPT_MAP

TEL_OPT_NUM=$((TEL_OPT_NUM+1)); TEL_OPT_MAP[$TEL_OPT_NUM]="disabled"
echo "    ${TEL_OPT_NUM}) Disabled — do not send telemetry"

TEL_OPT_NUM=$((TEL_OPT_NUM+1)); TEL_OPT_MAP[$TEL_OPT_NUM]="static"
echo "    ${TEL_OPT_NUM}) Static values — enter fixed temperature / humidity / pressure"

TEL_OPT_NUM=$((TEL_OPT_NUM+1)); TEL_OPT_MAP[$TEL_OPT_NUM]="script"
echo "    ${TEL_OPT_NUM}) External script — runs a script that prints JSON telemetry"

if $WEEWX_FOUND; then
    TEL_OPT_NUM=$((TEL_OPT_NUM+1)); TEL_OPT_MAP[$TEL_OPT_NUM]="weewx"
    echo "    ${TEL_OPT_NUM}) weewx weather station (found: ${WEEWX_DB})"
fi

echo ""
TEL_SEL=$(pick "Telemetry source" "$TEL_OPT_NUM")
TEL_MODE="${TEL_OPT_MAP[$TEL_SEL]}"

TEL_STATIC_TEMP=""
TEL_STATIC_HUM=""
TEL_STATIC_PRES=""
TEL_SCRIPT=""
TEL_WEEWX_DB=""
TEL_INTERVAL=10
TEL_LABEL="disabled"

case "$TEL_MODE" in

    disabled)
        TEL_LABEL="disabled"
        echo "  Environmental telemetry disabled."
        ;;

    static)
        echo ""
        echo "  Enter values to broadcast (leave blank to omit a field)."
        printf "  Temperature  (°C): "
        read -r TEL_STATIC_TEMP || true
        printf "  Humidity      (%%): "
        read -r TEL_STATIC_HUM || true
        printf "  Pressure    (hPa): "
        read -r TEL_STATIC_PRES || true
        TEL_LABEL="static (temp=${TEL_STATIC_TEMP:-n/a}°C hum=${TEL_STATIC_HUM:-n/a}% pres=${TEL_STATIC_PRES:-n/a}hPa)"
        ;;

    script)
        echo ""
        echo "  The script is run every interval and must print a JSON object to stdout:"
        echo "  {\"temperature\": 22.5, \"humidity\": 65.0, \"pressure\": 1013.25}"
        echo "  All fields are optional. temperature in °C, pressure in hPa."
        echo ""
        if [[ -n "$I2C_INFO" ]]; then
            echo "  Sample BME280 reader (requires smbus2: pip install smbus2):"
            echo "  ─────────────────────────────────────────────────────────"
            cat <<'SAMPLE'
  #!/usr/bin/env python3
  import smbus2, struct, json, time

  bus = smbus2.SMBus(1)
  addr = 0x76  # or 0x77

  # Force measurement (BME280 forced mode)
  bus.write_byte_data(addr, 0xF4, 0b00100101)
  time.sleep(0.1)

  raw = bus.read_i2c_block_data(addr, 0xF7, 8)
  # Simplified — for production use adafruit-circuitpython-bme280
  print(json.dumps({"temperature": 22.0, "humidity": 55.0, "pressure": 1013.0}))
SAMPLE
            echo "  ─────────────────────────────────────────────────────────"
            echo ""
        fi
        while true; do
            printf "  Path to script: "
            read -r TEL_SCRIPT || true
            [[ -f "$TEL_SCRIPT" ]] && break
            echo "  File not found: $TEL_SCRIPT"
        done
        TEL_LABEL="script: $TEL_SCRIPT"
        ;;

    weewx)
        TEL_WEEWX_DB="$WEEWX_DB"
        echo ""
        printf "  weewx database [${WEEWX_DB}]: "
        read -r WEEWX_IN || true
        if [[ -n "$WEEWX_IN" ]]; then
            TEL_WEEWX_DB="$WEEWX_IN"
        fi
        TEL_LABEL="weewx: $TEL_WEEWX_DB"
        ;;
esac

# Telemetry interval (skip if disabled)
if [[ "$TEL_MODE" != "disabled" ]]; then
    echo ""
    printf "  Broadcast interval in minutes [default 10]: "
    read -r TEL_INT_IN || true
    if [[ "$TEL_INT_IN" =~ ^[0-9]+$ ]] && (( TEL_INT_IN >= 1 )); then
        TEL_INTERVAL="$TEL_INT_IN"
    else
        TEL_INTERVAL=10
        [[ -n "$TEL_INT_IN" ]] && echo "  Invalid — using default of 10 minutes."
    fi
    echo "  Interval: ${TEL_INTERVAL} minutes"
fi

echo ""

# ── Step 4: Write config.ini ──────────────────────────────────
echo "[5/5] Updating config.ini..."

if [ ! -f "$CONFIG_INI" ]; then
    echo "  config.ini not found. Run install_nodebot.sh first."
    exit 1
fi

# Write [meshtastic] section
if grep -q "^\[meshtastic\]" "$CONFIG_INI"; then
    echo "  [meshtastic] section already present — updating."
    "$VENV_PYTHON" - "$CONFIG_INI" "$DEFAULT_BAUD" \
        "$RADIO_REGION" "$RADIO_PRESET" "$RADIO_HOPS" "$RADIO_POWER" <<'PYEOF'
import re, sys

path, baud, region, preset, hops, power = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
with open(path) as f:
    content = f.read()

content = re.sub(r'(?m)^#?\s*port\s*=.*$',          'port = /dev/meshtastic0',   content)
content = re.sub(r'(?m)^#?\s*baudrate\s*=.*$',       f'baudrate = {baud}',        content)
content = re.sub(r'(?m)^#?\s*region\s*=.*$',         f'region = {region}',        content)
content = re.sub(r'(?m)^#?\s*modem_preset\s*=.*$',   f'modem_preset = {preset}',  content)
content = re.sub(r'(?m)^#?\s*hop_limit\s*=.*$',      f'hop_limit = {hops}',       content)
content = re.sub(r'(?m)^#?\s*tx_power\s*=.*$',       f'tx_power = {power}',       content)

with open(path, 'w') as f:
    f.write(content)
print("  Updated [meshtastic] section.")
PYEOF
else
    cat >> "$CONFIG_INI" <<CFG

[meshtastic]
port = /dev/meshtastic0
baudrate = $DEFAULT_BAUD
region = $RADIO_REGION
modem_preset = $RADIO_PRESET
hop_limit = $RADIO_HOPS
tx_power = $RADIO_POWER
CFG
    echo "  Appended [meshtastic] section."
fi

# Write [telemetry] section
if grep -q "^\[telemetry\]" "$CONFIG_INI"; then
    echo "  [telemetry] section already present — updating."
    "$VENV_PYTHON" - "$CONFIG_INI" "$TEL_MODE" "$TEL_STATIC_TEMP" "$TEL_STATIC_HUM" \
        "$TEL_STATIC_PRES" "$TEL_SCRIPT" "$TEL_WEEWX_DB" "$TEL_INTERVAL" <<'PYEOF'
import re, sys

path, mode, temp, hum, pres, script, weewx_db, interval = sys.argv[1:9]
with open(path) as f:
    content = f.read()

content = re.sub(r'(?m)^#?\s*mode\s*=.*$',           f'mode = {mode}',           content)
content = re.sub(r'(?m)^#?\s*static_temp\s*=.*$',     f'static_temp = {temp}',   content)
content = re.sub(r'(?m)^#?\s*static_humidity\s*=.*$', f'static_humidity = {hum}', content)
content = re.sub(r'(?m)^#?\s*static_pressure\s*=.*$', f'static_pressure = {pres}', content)
content = re.sub(r'(?m)^#?\s*script\s*=.*$',          f'script = {script}',      content)
content = re.sub(r'(?m)^#?\s*weewx_db\s*=.*$',        f'weewx_db = {weewx_db}',  content)
content = re.sub(r'(?m)^#?\s*interval_minutes\s*=.*$', f'interval_minutes = {interval}', content)

with open(path, 'w') as f:
    f.write(content)
print("  Updated [telemetry] section.")
PYEOF
else
    cat >> "$CONFIG_INI" <<CFG

[telemetry]
mode = $TEL_MODE
static_temp = $TEL_STATIC_TEMP
static_humidity = $TEL_STATIC_HUM
static_pressure = $TEL_STATIC_PRES
script = $TEL_SCRIPT
weewx_db = $TEL_WEEWX_DB
interval_minutes = $TEL_INTERVAL
CFG
    echo "  Appended [telemetry] section."
fi

echo ""
echo "================================================"
echo "  Meshtastic installation complete."
echo "================================================"
echo ""
printf "  Device    : %s\n" "${MESH_LABELS[$CHOSEN_IDX]}"
printf "  Symlink   : /dev/meshtastic0\n"
printf "  Region    : %s\n" "$RADIO_REGION"
printf "  Preset    : %s\n" "$RADIO_PRESET"
printf "  Max hops  : %s\n" "$RADIO_HOPS"
printf "  TX power  : %s\n" "$([ "$RADIO_POWER" -eq 0 ] && echo 'max legal' || echo "${RADIO_POWER} dBm")"
printf "  Telemetry : %s\n" "$TEL_LABEL"
if [[ "$TEL_MODE" != "disabled" ]]; then
    printf "  Interval  : %s minutes\n" "$TEL_INTERVAL"
fi
printf "  Config    : %s\n" "$CONFIG_INI"
echo ""
echo "  GPS is shared from the NodeBot [gps] config."
echo "  To change GPS settings, re-run install_nodebot.sh."
echo ""
echo "  Restart NodeBot to activate the Meshtastic adapter:"
echo "    sudo systemctl restart nodebot"
echo ""
echo "  Live logs:"
echo "    journalctl -u nodebot -f"
echo ""
