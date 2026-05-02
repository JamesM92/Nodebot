#!/bin/bash
# ============================================================
# NodeBot MeshCore Installer
#
# - Installs the meshcore Python package into the project venv
# - Probes USB ports to auto-detect the MeshCore radio
# - Creates a stable udev symlink (/dev/meshcore0) tied to the
#   device's USB serial number so it reconnects after any replug
# - Guides region/frequency selection and programs the radio
# - Writes the [meshcore] section in config.ini
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
UDEV_RULES="/etc/udev/rules.d/99-meshcore.rules"
DEFAULT_BAUD=115200

# Fixed RF parameters — same for all MeshCore regions
MC_SF=10
MC_CR=5
MC_BW=250.0   # kHz

echo ""
echo "================================================"
echo "  NodeBot MeshCore Installer"
echo "================================================"
echo "  Project : $PROJECT_DIR"
echo "  Venv    : $VENV"
echo "================================================"
echo ""

# ── Legal disclaimer ──────────────────────────────────────────
echo "  ╔═════════════════════════════════════════════════════╗"
echo "  ║               ⚠  LEGAL NOTICE  ⚠                   ║"
echo "  ║                                                     ║"
echo "  ║  Radio frequency settings are regulated by law      ║"
echo "  ║  and vary by country and region.                    ║"
echo "  ║                                                     ║"
echo "  ║  The presets below are community-recommended        ║"
echo "  ║  starting points from the MeshCore project.         ║"
echo "  ║  They are NOT official guidance and may not be      ║"
echo "  ║  legal in your jurisdiction.                        ║"
echo "  ║                                                     ║"
echo "  ║  YOU are solely responsible for ensuring your       ║"
echo "  ║  chosen frequency complies with local radio laws.   ║"
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

# ── Step 1: Install meshcore into the project venv ───────────
echo "[1/5] Installing meshcore Python package..."
"$VENV_PIP" install --upgrade meshcore
echo "      meshcore $("$VENV_PIP" show meshcore 2>/dev/null | awk '/^Version:/{print $2}') installed."

# ── Step 2: Detect MeshCore radio on USB ports ───────────────
echo ""
echo "[2/5] Detecting MeshCore radio on USB ports..."
echo ""

PROBE_SCRIPT=$(cat <<'PYEOF'
import sys, asyncio

async def probe(port, baud):
    from meshcore.meshcore import MeshCore
    from meshcore.serial_cx import SerialConnection
    from meshcore.events import EventType

    found = asyncio.Event()

    async def on_event(event):
        found.set()

    try:
        cx = SerialConnection(port, baud)
        mc = MeshCore(cx)
        await asyncio.wait_for(mc.connect(), timeout=4)
        mc.subscribe(EventType.DEVICE_INFO, on_event)
        mc.subscribe(EventType.SELF_INFO, on_event)
        await mc.commands.send_device_query()
        try:
            await asyncio.wait_for(found.wait(), timeout=4)
            print("OK")
        except asyncio.TimeoutError:
            print("TIMEOUT")
        await mc.disconnect()
    except Exception as e:
        print(f"ERR:{e}")

asyncio.run(probe(sys.argv[1], int(sys.argv[2])))
PYEOF
)

MESHCORE_PORTS=()
MESHCORE_LABELS=()

for port in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$port" ] || continue

    model=$(udev_prop "$port" "ID_MODEL")
    vendor=$(udev_prop "$port" "ID_VENDOR")
    serial=$(udev_prop "$port" "ID_SERIAL_SHORT")

    printf "  Probing %-16s [%s %s S/N:%s] ... " \
        "$port" "$vendor" "$model" "${serial:-none}"

    result=$("$VENV_PYTHON" -c "$PROBE_SCRIPT" "$port" "$DEFAULT_BAUD" 2>/dev/null)

    if [[ "$result" == "OK" ]]; then
        echo "MeshCore detected"
        MESHCORE_PORTS+=("$port")
        MESHCORE_LABELS+=("$vendor $model (S/N: ${serial:-none})")
    else
        echo "no response"
    fi
done

echo ""

if (( ${#MESHCORE_PORTS[@]} == 0 )); then
    echo "  No MeshCore radio detected."
    echo "  Make sure the device is plugged in and running MeshCore firmware."
    echo ""
    printf "  Continue with manual port entry? (yes/no): "
    read -r CONT || true
    if [[ "${CONT,,}" != "yes" ]]; then exit 1; fi
    while true; do
        printf "  Enter port (e.g. /dev/ttyUSB0): "
        read -r MANUAL_PORT || true
        [[ "$MANUAL_PORT" == /dev/* ]] && break
        echo "  Port must start with /dev/ (e.g. /dev/ttyUSB0)"
    done
    MESHCORE_PORTS=("$MANUAL_PORT")
    MESHCORE_LABELS=("manual entry")
fi

CHOSEN_IDX=0

if (( ${#MESHCORE_PORTS[@]} > 1 )); then
    echo "  Multiple MeshCore devices found:"
    for i in "${!MESHCORE_PORTS[@]}"; do
        printf "    %d) %s  (%s)\n" $((i+1)) "${MESHCORE_PORTS[$i]}" "${MESHCORE_LABELS[$i]}"
    done
    echo ""
    SEL=$(pick "Primary MeshCore radio" "${#MESHCORE_PORTS[@]}")
    CHOSEN_IDX=$((SEL-1))
fi

CHOSEN_PORT="${MESHCORE_PORTS[$CHOSEN_IDX]}"

# ── Step 3: Create udev rule for stable /dev/meshcore0 ───────
echo "[3/5] Creating udev symlink for stable device naming..."
echo ""
echo "  This creates /dev/meshcore0 tied to the device's USB identity."
echo "  When unplugged and replugged (any port), the symlink is recreated"
echo "  and NodeBot reconnects automatically."
echo ""

id_serial=$(udev_prop "$CHOSEN_PORT" "ID_SERIAL")
id_serial_short=$(udev_prop "$CHOSEN_PORT" "ID_SERIAL_SHORT")
id_path=$(udev_prop "$CHOSEN_PORT" "ID_PATH")
id_vendor=$(udev_prop "$CHOSEN_PORT" "ID_VENDOR_ID")
id_model_id=$(udev_prop "$CHOSEN_PORT" "ID_MODEL_ID")

echo "  Device : $CHOSEN_PORT — ${MESHCORE_LABELS[$CHOSEN_IDX]}"

generic_serials=("0001" "0000" "1234567890" "ABCDEF" "")
is_generic=false
for g in "${generic_serials[@]}"; do
    if [[ "$id_serial_short" == "$g" ]]; then is_generic=true; break; fi
done

CP210X_PROG=$(cat <<'PYEOF'
import sys, os

def write_serial(port, new_serial):
    try:
        import usb.core, usb.util
    except ImportError:
        return "ERR:pyusb not available"
    tty_name = os.path.basename(port)
    path = os.path.realpath(f"/sys/class/tty/{tty_name}/device")
    busnum = devnum = None
    while path and path != "/":
        try:
            busnum = int(open(f"{path}/busnum").read())
            devnum = int(open(f"{path}/devnum").read())
            break
        except (FileNotFoundError, ValueError):
            path = os.path.dirname(path)
    if busnum is None:
        return "ERR:USB device not found in sysfs"
    dev = usb.core.find(bus=busnum, address=devnum)
    if dev is None:
        return "ERR:USB device not found"
    detached = []
    try:
        cfg = dev.get_active_configuration()
        for iface in range(cfg.bNumInterfaces):
            try:
                if dev.is_kernel_driver_active(iface):
                    dev.detach_kernel_driver(iface)
                    detached.append(iface)
            except Exception:
                pass
        enc = new_serial.encode('utf-16-le')
        desc = bytes([len(enc) + 2, 0x03]) + enc
        dev.ctrl_transfer(0x40, 0xFF, 0x3702, 0, desc)
        return f"OK:{new_serial}"
    except Exception as e:
        return f"ERR:{e}"
    finally:
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass
        for i in detached:
            try:
                dev.attach_kernel_driver(i)
            except Exception:
                pass

print(write_serial(sys.argv[1], sys.argv[2]))
PYEOF
)

if [[ -n "$id_serial" ]] && ! $is_generic; then
    # Already has a unique serial number
    RULE="SUBSYSTEM==\"tty\", ENV{ID_SERIAL}==\"${id_serial}\", SYMLINK+=\"meshcore0\""
    echo "  Unique serial detected — symlink follows device across ports."

elif [[ "$id_vendor" == "1a86" ]]; then
    # WCH CH343P / CH340 family — unique by vendor+product ID
    RULE="SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"1a86\", ATTRS{idProduct}==\"${id_model_id}\", SYMLINK+=\"meshcore0\""
    echo "  WCH CH343P / CH340 detected."
    echo "  This chip has a unique USB vendor/product ID."
    echo "  Symlink is permanently port-independent — works across any USB socket."

elif [[ "$id_vendor" == "10c4" ]]; then
    # Silicon Labs CP210x with generic serial — attempt to program a unique one
    echo "  CP210x with generic serial '${id_serial_short}' detected."
    echo "  Attempting to write a unique serial number to the chip..."
    "$VENV_PIP" install --quiet pyusb 2>/dev/null || true

    UNIQUE_SERIAL="NB-$("$VENV_PYTHON" -c 'import secrets; print(secrets.token_hex(2).upper())')"
    PROG_RESULT=$(timeout 15 sudo -n "$VENV_PYTHON" -c "$CP210X_PROG" "$CHOSEN_PORT" "$UNIQUE_SERIAL" 2>/dev/null || echo "ERR:timeout or requires sudo")

    if [[ "$PROG_RESULT" == OK:* ]]; then
        WRITTEN_SERIAL="${PROG_RESULT#OK:}"
        echo "  Serial programmed: ${WRITTEN_SERIAL}"
        echo "  Please unplug and replug the device now, then press Enter."
        read -r _
        RULE="SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"10c4\", ATTRS{idProduct}==\"${id_model_id}\", ATTRS{serial}==\"${WRITTEN_SERIAL}\", SYMLINK+=\"meshcore0\""
        echo "  Symlink is now tied to the programmed serial — port-independent."
    else
        echo "  Could not program serial (${PROG_RESULT#ERR:}) — chip may be a locked clone."
        echo "  Falling back to physical USB port binding."
        RULE="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"${id_path}\", SYMLINK+=\"meshcore0\""
        echo "  Keep this device in the same USB port, or re-run this installer if you move it."
    fi

else
    # Unknown chip type — fall back to port-based rule
    RULE="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"${id_path}\", SYMLINK+=\"meshcore0\""
    echo "  Unknown USB chip (vendor ${id_vendor}) — symlink tied to physical USB port."
fi

echo "  Rule: $RULE"

sudo tee "$UDEV_RULES" > /dev/null <<UDEV
# MeshCore stable device naming — written by NodeBot MeshCore installer
# Creates /dev/meshcore0 tied to device identity.
# Device reconnects automatically when replugged.

# Device: $CHOSEN_PORT — ${MESHCORE_LABELS[$CHOSEN_IDX]}
$RULE
UDEV

sudo udevadm control --reload-rules
sudo udevadm trigger
sleep 1

ACTIVE_PORT="$CHOSEN_PORT"
if [ -e "/dev/meshcore0" ]; then
    echo "  Symlink active: /dev/meshcore0 -> $(readlink -f /dev/meshcore0)"
    ACTIVE_PORT="/dev/meshcore0"
else
    echo "  Note: /dev/meshcore0 will appear once the device is plugged in."
fi
echo ""

# ── Step 4: Region / frequency selection and radio programming ─
echo "[4/5] Radio frequency configuration"
echo ""
echo "  All MeshCore regions share the same modulation settings:"
printf "  SF=%-3s  CR=%-3s  BW=%.0f kHz\n" "$MC_SF" "$MC_CR" "$MC_BW"
echo ""
echo "  Only the frequency differs by region."
echo "  (Source: MeshCore FAQ — https://github.com/meshcore-dev/MeshCore/wiki/FAQ)"
echo ""
echo "  Select your region:"
echo "    1) Australia / New Zealand  — 915.800 MHz"
echo "    2) USA                      — 910.525 MHz"
echo "    3) UK / EU                  — 867.500 MHz"
echo "    4) UK / EU (proposed)       — 869.525 MHz (community discussion, not finalised)"
echo "    5) Manual entry             — enter custom frequency"
echo ""

REGION=$(pick "Region" 5)
REGION_LABEL=""

case "$REGION" in
    1) MC_FREQ=915.8;   REGION_LABEL="Australia / New Zealand" ;;
    2) MC_FREQ=910.525; REGION_LABEL="USA" ;;
    3) MC_FREQ=867.5;   REGION_LABEL="UK / EU" ;;
    4) MC_FREQ=869.525; REGION_LABEL="UK / EU (proposed 869.525 MHz)" ;;
    5)
        printf "  Frequency in MHz (e.g. 915.8): "
        read -r MC_FREQ || true
        REGION_LABEL="Custom"
        ;;
esac

echo ""
echo "  ── Forwarding / repeater configuration ─────────────"
echo "  This radio will forward messages it hears onto the mesh."
echo "  The hop limit controls how many times a packet may be"
echo "  relayed before it is dropped (max allowed: 64)."
echo "  Enter 0 to disable forwarding entirely."
echo ""
printf "  Max hops [0-64, default 64]: "
read -r HOP_INPUT || true
if [[ "$HOP_INPUT" =~ ^[0-9]+$ ]] && (( HOP_INPUT >= 0 && HOP_INPUT <= 64 )); then
    MC_REPEAT="$HOP_INPUT"
else
    MC_REPEAT=64
    if [[ -n "$HOP_INPUT" ]]; then
        echo "  Invalid entry — using default of 64."
    fi
fi
echo ""

if (( MC_REPEAT == 0 )); then
    FORWARD_LABEL="disabled"
else
    FORWARD_LABEL="enabled (max ${MC_REPEAT} hops)"
fi

echo "  ── Selected radio configuration ────────────────────"
printf "  Region    : %s\n"          "$REGION_LABEL"
printf "  Frequency : %s MHz\n"      "$MC_FREQ"
printf "  Bandwidth : %.0f kHz\n"    "$MC_BW"
printf "  SF        : %s\n"          "$MC_SF"
printf "  CR        : %s\n"          "$MC_CR"
printf "  Forwarding: %s\n"          "$FORWARD_LABEL"
echo "  ────────────────────────────────────────────────────"
echo ""
printf "  Program these settings onto the radio now? (yes/no): "
read -r DO_PROGRAM || true

if [[ "${DO_PROGRAM,,}" == "yes" ]]; then
    echo "  Programming radio..."

    SET_RADIO_SCRIPT=$(cat <<PYEOF
import sys, asyncio

async def set_radio(port, baud, freq, bw, sf, cr, repeat):
    from meshcore.meshcore import MeshCore
    from meshcore.serial_cx import SerialConnection
    from meshcore.events import EventType

    try:
        cx = SerialConnection(port, baud)
        mc = MeshCore(cx)
        await asyncio.wait_for(mc.connect(), timeout=5)
        evt = await mc.commands.set_radio(
            float(freq), float(bw), int(sf), int(cr),
            repeat=int(repeat)
        )
        if evt and evt.type == EventType.ERROR:
            print(f"ERR:{evt.payload}")
        else:
            print("OK")
        await mc.disconnect()
    except Exception as e:
        print(f"ERR:{e}")

asyncio.run(set_radio(
    sys.argv[1], int(sys.argv[2]),
    sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7]
))
PYEOF
)

    result=$("$VENV_PYTHON" -c "$SET_RADIO_SCRIPT" \
        "$ACTIVE_PORT" "$DEFAULT_BAUD" \
        "$MC_FREQ" "$MC_BW" "$MC_SF" "$MC_CR" "$MC_REPEAT" 2>/dev/null)

    if [[ "$result" == "OK" ]]; then
        echo "  Radio programmed successfully."
    else
        echo "  ⚠  Programming returned: $result"
        echo "     Settings may still have been applied — check device logs to confirm."
    fi
else
    echo "  Skipping radio programming."
    echo "  You can set these manually with rnodeconf or the MeshCore companion app."
fi
echo ""

# ── Step 5: Write [meshcore] section to config.ini ───────────
echo "[5/5] Updating config.ini..."

if [ ! -f "$CONFIG_INI" ]; then
    echo "  config.ini not found. Run install_nodebot.sh first."
    exit 1
fi

if grep -q "^\[meshcore\]" "$CONFIG_INI"; then
    echo "  [meshcore] section already present — updating."
    "$VENV_PYTHON" - "$CONFIG_INI" "$DEFAULT_BAUD" <<'PYEOF'
import re, sys

path, baud = sys.argv[1], sys.argv[2]
with open(path) as f:
    content = f.read()

content = re.sub(r'(?m)^#?\s*port\s*=.*$',     'port = /dev/meshcore0', content)
content = re.sub(r'(?m)^#?\s*baudrate\s*=.*$', f'baudrate = {baud}',    content)

with open(path, 'w') as f:
    f.write(content)
print("  Updated: port = /dev/meshcore0  baudrate =", baud)
PYEOF
else
    cat >> "$CONFIG_INI" <<CFG

[meshcore]
port = /dev/meshcore0
baudrate = $DEFAULT_BAUD
CFG
    echo "  Appended [meshcore] section to config.ini"
fi

echo ""
echo "================================================"
echo "  MeshCore installation complete."
echo "================================================"
echo ""
printf "  Region    : %s\n"     "$REGION_LABEL"
printf "  Frequency : %s MHz\n" "$MC_FREQ"
printf "  Forwarding: %s\n"     "$FORWARD_LABEL"
printf "  Symlink   : /dev/meshcore0\n"
printf "  Config    : %s\n"     "$CONFIG_INI"
echo ""
echo "  Restart NodeBot to activate the MeshCore adapter:"
echo "    sudo systemctl restart nodebot"
echo ""
echo "  Live logs:"
echo "    journalctl -u nodebot -f"
echo ""
