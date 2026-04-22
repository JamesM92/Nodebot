#!/bin/bash
# ============================================================
# NodeBot Base Installer
#
# Sets up NodeBot and its core dependencies on a fresh
# Raspberry Pi. Installs the default nodebot.service with no
# protocol-specific dependencies.
#
# To add NomadNet / LXMF / rNode support run afterwards:
#   bash installer/install_lxmf.sh
#
# Usage (from the project root or anywhere):
#   bash installer/install_nodebot.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/.venv"
VENV_PYTHON="$VENV/bin/python3"
SERVICE_USER="$(whoami)"

echo ""
echo "================================================"
echo "  NodeBot Installer"
echo "================================================"
echo "  Project : $PROJECT_DIR"
echo "  Venv    : $VENV"
echo "  User    : $SERVICE_USER"
echo "================================================"
echo ""

# ── Step 1: System packages ───────────────────────────────────
echo "[1/6] Checking system packages..."
MISSING=()
command -v python3 >/dev/null 2>&1 || MISSING+=("python3")
command -v pip3    >/dev/null 2>&1 || MISSING+=("python3-pip")

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  Installing: ${MISSING[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${MISSING[@]}"
else
    echo "  python3 and pip3 already present."
fi

# netcat is used by install_lxmf.sh's wait script — install now so it is
# available if the user runs the LXMF installer later.
if ! command -v nc >/dev/null 2>&1; then
    echo "  Installing netcat-openbsd (needed by install_lxmf.sh)..."
    sudo apt-get install -y netcat-openbsd
fi

# ── Step 2: Virtual environment ───────────────────────────────
echo "[2/6] Setting up virtual environment..."

if [ ! -d "$VENV" ]; then
    if command -v uv >/dev/null 2>&1; then
        echo "  Using uv..."
        cd "$PROJECT_DIR" && uv sync
    else
        echo "  Using python3 -m venv..."
        python3 -m venv "$VENV"
    fi
else
    echo "  Venv already exists, skipping creation."
fi

# Fix execute permissions — the venv can lose them after git clone or copy.
find "$VENV/bin" -type f ! -perm /111 -exec chmod +x {} \;
echo "  Permissions fixed."

# ── Step 3: Python dependencies ───────────────────────────────
echo "[3/6] Installing Python dependencies..."

if command -v uv >/dev/null 2>&1 && [ -f "$PROJECT_DIR/uv.lock" ]; then
    echo "  Using uv sync..."
    cd "$PROJECT_DIR" && uv sync
else
    echo "  Using pip install..."
    "$VENV/bin/pip3" install --upgrade pip --quiet
    "$VENV/bin/pip3" install -r "$PROJECT_DIR/requirements.txt"
fi

echo "  Dependencies installed."

# ── Step 4: Storage directory ─────────────────────────────────
echo "[4/6] Creating storage directory..."
mkdir -p "$HOME/.nodebot/lxmf_storage"
echo "  $HOME/.nodebot/lxmf_storage"

# ── Ensure config.ini exists ──────────────────────────────────
CONFIG_INI="$PROJECT_DIR/config.ini"
if [ ! -f "$CONFIG_INI" ]; then
    cp "$PROJECT_DIR/config.example" "$CONFIG_INI"
    echo "  Created config.ini from config.example"
fi

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

# ── Step 5: GPS configuration ────────────────────────────────
echo "[5/6] GPS location configuration"
echo ""
echo "  NodeBot can share your location on the network."
echo "  Any protocol that supports GPS (e.g. MeshCore) will use this setting."
echo ""

# Detect gpsd
GPSD_RUNNING=false
if systemctl is-active --quiet gpsd 2>/dev/null; then
    GPSD_RUNNING=true
fi

# Probe serial ports for NMEA GPS sentences
GPS_PROBE_SCRIPT=$(cat <<'PYEOF'
import sys, time
port = sys.argv[1]
try:
    import serial
    for baud in (9600, 4800, 115200):
        try:
            s = serial.Serial(port, baud, timeout=2)
            deadline = time.time() + 6
            while time.time() < deadline:
                line = s.readline().decode("ascii", errors="ignore").strip()
                if line.startswith(("$GPGGA", "$GNGGA", "$GPRMC", "$GNRMC")):
                    print(f"OK:{baud}")
                    s.close()
                    sys.exit(0)
            s.close()
        except Exception:
            pass
except ImportError:
    pass
print("NONE")
PYEOF
)

# Resolve any existing MeshCore radio port so we don't probe it as a GPS
MC_REAL=""
MC_PORT=$("$VENV_PYTHON" -c "
import configparser, os, sys
c = configparser.ConfigParser()
c.read(sys.argv[1])
p = c.get('meshcore','port',fallback='').strip()
print(os.path.realpath(p) if p and os.path.exists(p) else '')
" "$CONFIG_INI" 2>/dev/null || true)
[ -n "$MC_PORT" ] && MC_REAL="$MC_PORT"

GPS_SERIAL_PORTS=()
GPS_SERIAL_BAUDS=()

echo "  Scanning for GPS devices..."
for gps_port in /dev/ttyUSB* /dev/ttyACM* /dev/ttyAMA0 /dev/serial0; do
    [ -e "$gps_port" ] || continue
    real_port=$(readlink -f "$gps_port" 2>/dev/null || echo "$gps_port")
    [ -n "$MC_REAL" ] && [ "$real_port" = "$MC_REAL" ] && continue

    printf "  Probing %-20s ... " "$gps_port"
    gps_result=$("$VENV_PYTHON" -c "$GPS_PROBE_SCRIPT" "$gps_port" 2>/dev/null)

    if [[ "$gps_result" == OK:* ]]; then
        gps_baud="${gps_result#OK:}"
        echo "GPS NMEA detected (${gps_baud} baud)"
        GPS_SERIAL_PORTS+=("$gps_port")
        GPS_SERIAL_BAUDS+=("$gps_baud")
    else
        echo "no GPS"
    fi
done
echo ""

# Build option list
GPS_OPT_NUM=0
declare -A GPS_OPT_MAP

GPS_OPT_NUM=$((GPS_OPT_NUM+1)); GPS_OPT_MAP[$GPS_OPT_NUM]="disabled:"
echo "    ${GPS_OPT_NUM}) Disabled — do not share location"

GPS_OPT_NUM=$((GPS_OPT_NUM+1)); GPS_OPT_MAP[$GPS_OPT_NUM]="manual:"
echo "    ${GPS_OPT_NUM}) Enter coordinates manually"

if $GPSD_RUNNING; then
    GPS_OPT_NUM=$((GPS_OPT_NUM+1)); GPS_OPT_MAP[$GPS_OPT_NUM]="gpsd:"
    echo "    ${GPS_OPT_NUM}) Use gpsd (running — reads live fix)"
fi

for i in "${!GPS_SERIAL_PORTS[@]}"; do
    GPS_OPT_NUM=$((GPS_OPT_NUM+1))
    GPS_OPT_MAP[$GPS_OPT_NUM]="serial:${GPS_SERIAL_PORTS[$i]}:${GPS_SERIAL_BAUDS[$i]}"
    echo "    ${GPS_OPT_NUM}) Use ${GPS_SERIAL_PORTS[$i]} (${GPS_SERIAL_BAUDS[$i]} baud)"
done

GPS_OPT_NUM=$((GPS_OPT_NUM+1)); GPS_OPT_MAP[$GPS_OPT_NUM]="future:"
echo "    ${GPS_OPT_NUM}) No GPS yet — auto-detect when a device is plugged in"

echo ""
GPS_SEL=$(pick "GPS option" "$GPS_OPT_NUM")
GPS_CHOICE="${GPS_OPT_MAP[$GPS_SEL]}"
GPS_TYPE="${GPS_CHOICE%%:*}"

GPS_MODE="disabled"
GPS_LAT=""
GPS_LON=""
GPS_ALT="0"
GPS_DEVICE=""
GPS_LABEL="disabled"

case "$GPS_TYPE" in

    disabled)
        GPS_MODE="disabled"
        GPS_LABEL="disabled"
        echo "  GPS sharing disabled."
        ;;

    manual)
        GPS_MODE="manual"
        echo ""
        while true; do
            printf "  Latitude  (-90  to  90): "
            read -r GPS_LAT || true
            if [[ "$GPS_LAT" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
                awk -v v="$GPS_LAT" 'BEGIN{exit !(v>=-90 && v<=90)}' && break
            fi
            echo "  Invalid latitude. Enter a number between -90 and 90."
        done
        while true; do
            printf "  Longitude (-180 to 180): "
            read -r GPS_LON || true
            if [[ "$GPS_LON" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
                awk -v v="$GPS_LON" 'BEGIN{exit !(v>=-180 && v<=180)}' && break
            fi
            echo "  Invalid longitude. Enter a number between -180 and 180."
        done
        printf "  Altitude  (metres, default 0): "
        read -r GPS_ALT_IN || true
        if [[ "$GPS_ALT_IN" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
            GPS_ALT="$GPS_ALT_IN"
        else
            GPS_ALT="0"
        fi
        GPS_LABEL="manual (${GPS_LAT}, ${GPS_LON}, ${GPS_ALT}m)"
        echo "  Location set: lat=${GPS_LAT} lon=${GPS_LON} alt=${GPS_ALT}m"
        ;;

    gpsd)
        GPS_MODE="gpsd"
        GPS_DEVICE="gpsd"
        echo ""
        echo "  Reading fix from gpsd (up to 90 seconds)..."

        GPS_FIX_SCRIPT=$(cat <<'PYEOF'
import socket, json, time

def get_fix(timeout=90):
    try:
        s = socket.create_connection(("127.0.0.1", 2947), timeout=5)
        s.sendall(b'?WATCH={"enable":true,"json":true}\n')
        deadline = time.time() + timeout
        buf = ""
        while time.time() < deadline:
            s.settimeout(deadline - time.time())
            try:
                chunk = s.recv(4096).decode("utf-8", errors="ignore")
            except OSError:
                break
            buf += chunk
            for line in buf.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("class") == "TPV" and obj.get("mode", 0) >= 2:
                    lat = obj.get("lat")
                    lon = obj.get("lon")
                    alt = obj.get("alt", 0) or 0
                    if lat is not None and lon is not None:
                        print(f"{lat},{lon},{alt:.1f}")
                        s.close()
                        return
            buf = buf.split("\n")[-1]
        s.close()
        print("TIMEOUT")
    except Exception as e:
        print(f"ERR:{e}")

get_fix()
PYEOF
)
        GPS_FIX=$("$VENV_PYTHON" -c "$GPS_FIX_SCRIPT" 2>/dev/null)

        if [[ "$GPS_FIX" =~ ^-?[0-9] ]]; then
            IFS=',' read -r GPS_LAT GPS_LON GPS_ALT <<< "$GPS_FIX"
            echo "  Fix obtained: lat=${GPS_LAT} lon=${GPS_LON} alt=${GPS_ALT}m"
            GPS_LABEL="gpsd (${GPS_LAT}, ${GPS_LON}, ${GPS_ALT}m)"
        else
            echo "  ⚠  Could not get fix from gpsd: ${GPS_FIX}"
            echo "     Storing gpsd mode — NodeBot reads live coordinates at startup."
            GPS_LABEL="gpsd (live — no fix during install)"
        fi
        ;;

    serial)
        REST="${GPS_CHOICE#serial:}"
        GPS_DEVICE="${REST%%:*}"
        SERIAL_BAUD="${REST##*:}"
        GPS_MODE="serial"
        echo ""
        echo "  Reading fix from ${GPS_DEVICE} at ${SERIAL_BAUD} baud (up to 90 seconds)..."

        GPS_SERIAL_SCRIPT=$(cat <<'PYEOF'
import sys, serial, time

port, baud = sys.argv[1], int(sys.argv[2])

def parse_gga(line):
    parts = line.split(",")
    if len(parts) < 10:
        return None
    try:
        if int(parts[6]) == 0:
            return None
        raw_lat, lat_hem = parts[2], parts[3]
        raw_lon, lon_hem = parts[4], parts[5]
        alt = float(parts[9]) if parts[9] else 0.0
        lat = float(raw_lat[:2]) + float(raw_lat[2:]) / 60.0
        if lat_hem == "S":
            lat = -lat
        lon = float(raw_lon[:3]) + float(raw_lon[3:]) / 60.0
        if lon_hem == "W":
            lon = -lon
        return lat, lon, alt
    except Exception:
        return None

try:
    s = serial.Serial(port, baud, timeout=2)
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            line = s.readline().decode("ascii", errors="ignore").strip()
        except Exception:
            continue
        if line.startswith(("$GPGGA", "$GNGGA")):
            result = parse_gga(line)
            if result:
                lat, lon, alt = result
                print(f"{lat:.6f},{lon:.6f},{alt:.1f}")
                s.close()
                sys.exit(0)
    s.close()
    print("TIMEOUT")
except Exception as e:
    print(f"ERR:{e}")
PYEOF
)
        GPS_FIX=$("$VENV_PYTHON" -c "$GPS_SERIAL_SCRIPT" "$GPS_DEVICE" "$SERIAL_BAUD" 2>/dev/null)

        if [[ "$GPS_FIX" =~ ^-?[0-9] ]]; then
            IFS=',' read -r GPS_LAT GPS_LON GPS_ALT <<< "$GPS_FIX"
            echo "  Fix obtained: lat=${GPS_LAT} lon=${GPS_LON} alt=${GPS_ALT}m"
            GPS_LABEL="serial ${GPS_DEVICE} (${GPS_LAT}, ${GPS_LON}, ${GPS_ALT}m)"
        else
            echo "  ⚠  Could not get fix from ${GPS_DEVICE}: ${GPS_FIX}"
            echo "     Storing serial mode — NodeBot reads live coordinates at startup."
            GPS_LABEL="serial ${GPS_DEVICE} (live — no fix during install)"
        fi
        ;;

    future)
        GPS_MODE="future"
        GPS_LABEL="auto-detect when a GPS device is plugged in"
        echo "  NodeBot will scan for a GPS device at startup and when one appears."
        ;;
esac

# Precision (skip if disabled)
GPS_PRECISION=4
GPS_PREC_LABEL="n/a"

if [[ "$GPS_MODE" != "disabled" ]]; then
    echo ""
    echo "  ── Coordinate precision / privacy ──────────────────"
    echo "  Fewer decimal places = larger shown area, more private."
    echo ""
    echo "    1) 2 decimal places  — ~1.1 km  (neighbourhood)"
    echo "    2) 3 decimal places  — ~111 m   (street level)"
    echo "    3) 4 decimal places  — ~11 m    (building)     ← recommended"
    echo "    4) 5 decimal places  — ~1.1 m   (precise position)"
    echo ""
    GPS_PREC_SEL=$(pick "Precision" 4)
    case "$GPS_PREC_SEL" in
        1) GPS_PRECISION=2; GPS_PREC_LABEL="2 d.p. (~1.1 km)" ;;
        2) GPS_PRECISION=3; GPS_PREC_LABEL="3 d.p. (~111 m)"  ;;
        3) GPS_PRECISION=4; GPS_PREC_LABEL="4 d.p. (~11 m)"   ;;
        4) GPS_PRECISION=5; GPS_PREC_LABEL="5 d.p. (~1.1 m)"  ;;
    esac
    echo "  Precision: $GPS_PREC_LABEL"
fi

echo ""

# Write [gps] section to config.ini
if grep -q "^\[gps\]" "$CONFIG_INI"; then
    echo "  [gps] section already present — updating."
    "$VENV_PYTHON" - "$CONFIG_INI" "$GPS_MODE" "$GPS_LAT" "$GPS_LON" "$GPS_ALT" "$GPS_DEVICE" "$GPS_PRECISION" <<'PYEOF'
import re, sys

path, gps_mode, gps_lat, gps_lon, gps_alt, gps_device, gps_precision = sys.argv[1:8]
with open(path) as f:
    content = f.read()

content = re.sub(r'(?m)^#?\s*gps_mode\s*=.*$',      f'gps_mode = {gps_mode}',           content)
content = re.sub(r'(?m)^#?\s*gps_lat\s*=.*$',       f'gps_lat = {gps_lat}',             content)
content = re.sub(r'(?m)^#?\s*gps_lon\s*=.*$',       f'gps_lon = {gps_lon}',             content)
content = re.sub(r'(?m)^#?\s*gps_alt\s*=.*$',       f'gps_alt = {gps_alt}',             content)
content = re.sub(r'(?m)^#?\s*gps_device\s*=.*$',    f'gps_device = {gps_device}',       content)
content = re.sub(r'(?m)^#?\s*gps_precision\s*=.*$', f'gps_precision = {gps_precision}', content)

with open(path, 'w') as f:
    f.write(content)
print("  Updated [gps] section.")
PYEOF
else
    cat >> "$CONFIG_INI" <<CFG

[gps]
gps_mode = $GPS_MODE
gps_lat = $GPS_LAT
gps_lon = $GPS_LON
gps_alt = $GPS_ALT
gps_device = $GPS_DEVICE
gps_precision = $GPS_PRECISION
CFG
    echo "  Appended [gps] section to config.ini"
fi

echo ""

# ── Step 6: Install nodebot.service ──────────────────────────
echo "[6/6] Installing nodebot.service..."

sudo tee /etc/systemd/system/nodebot.service > /dev/null <<EOF
[Unit]
Description=NodeBot Multi-Protocol Mesh Relay System
Documentation=https://github.com/JamesM92/NodeBot
After=network.target
Wants=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR/src
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

sudo systemctl daemon-reload
sudo systemctl enable nodebot.service
echo "  Written and enabled: /etc/systemd/system/nodebot.service"

echo ""
echo "================================================"
echo "  Installation complete."
echo "================================================"
echo ""
printf "  GPS       : %s\n" "$GPS_LABEL"
if [[ "$GPS_MODE" != "disabled" ]]; then
    printf "  Precision : %s\n" "$GPS_PREC_LABEL"
fi
echo ""
echo "  Start NodeBot:"
echo "    sudo systemctl start nodebot"
echo ""
echo "  View logs:"
echo "    journalctl -u nodebot -f"
echo ""
echo "  To add NomadNet / LXMF / rNode support:"
echo "    bash installer/install_lxmf.sh"
echo ""
