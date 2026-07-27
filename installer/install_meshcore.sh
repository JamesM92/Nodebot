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
#
# IMPORTANT — CUSTOM FIRMWARE REQUIRED:
#   The MeshCore adapter requires a custom Companion Radio firmware build.
#   Stock upstream firmware does not include AGC reset support for Companion
#   Radio, causing the SX126x to go deaf after RF interference.
#
#   Flash firmware from:
#     github.com/JamesM92/MeshCore  branch: fix/agc-reset-blocked-by-sticky-irq
#
#   Build with PlatformIO:
#     cd /path/to/MeshCore
#     git checkout fix/agc-reset-blocked-by-sticky-irq
#     pio run -e <your_device_env> -t upload
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

# shellcheck source=../scripts/_meshcore_config.sh
source "$PROJECT_DIR/scripts/_meshcore_config.sh"

echo ""
echo "================================================"
echo "  NodeBot MeshCore Installer"
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

# ── Step 1: Install meshcore into the project venv ───────────
echo "[1/4] Installing meshcore Python package..."
"$VENV_PIP" install --upgrade meshcore
echo "      meshcore $("$VENV_PIP" show meshcore 2>/dev/null | awk '/^Version:/{print $2}') installed."

# ── Step 2: Detect MeshCore radio on USB ports ───────────────
echo ""
echo "[2/4] Detecting MeshCore radio on USB ports..."
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
echo "[3/4] Creating udev symlink for stable device naming..."
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
DRIVER_BIND_RULE=""  # populated only for non-standard product IDs

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

elif [[ "$id_vendor" == "10c4" && "$id_model_id" != "ea60" ]]; then
    # CP210x with a non-standard product ID (e.g. 0000) — the product ID itself
    # uniquely distinguishes this device from standard CP2102 (ea60) devices such
    # as rNodes. Use vendor+product to match it port-independently.
    # Also inject the new_id so the cp210x driver binds on plug (non-standard PID
    # is not in the driver's built-in table).
    echo "  CP210x with non-standard product ID ${id_model_id} detected."
    echo "  Using vendor+product ID for port-independent matching."
    DRIVER_BIND_RULE="ACTION==\"add\", SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"10c4\", ATTRS{idProduct}==\"${id_model_id}\", RUN+=\"/bin/sh -c 'echo 10c4 ${id_model_id} > /sys/bus/usb-serial/drivers/cp210x/new_id'\""
    RULE="SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"10c4\", ATTRS{idProduct}==\"${id_model_id}\", SYMLINK+=\"meshcore0\""
    echo "  Symlink is permanently port-independent — works in any USB socket."

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

# ── Conflict check — ensure this device isn't already claimed by another rule ──
# Extract the match value from the new rule so we can search for it in other rule files.
# We only need to flag when two symlink rules share the same hardware identity string,
# which would cause both symlinks to point at the same physical device.
_rule_match_value=""
if [[ "$RULE" == *"ID_PATH=="* ]]; then
    _rule_match_value=$(echo "$RULE" | grep -oP '(?<=ID_PATH==")[^"]+')
elif [[ "$RULE" == *"ID_SERIAL=="* ]]; then
    _rule_match_value=$(echo "$RULE" | grep -oP '(?<=ID_SERIAL==")[^"]+')
elif [[ "$RULE" == *'ATTRS{serial}=='* ]]; then
    _rule_match_value=$(echo "$RULE" | grep -oP '(?<=ATTRS\{serial\}==")[^"]+')
fi

if [[ -n "$_rule_match_value" ]]; then
    _conflict_files=()
    for _rf in /etc/udev/rules.d/*.rules; do
        [[ "$_rf" == "$UDEV_RULES" ]] && continue
        grep -q "$_rule_match_value" "$_rf" 2>/dev/null && _conflict_files+=("$_rf")
    done
    if (( ${#_conflict_files[@]} > 0 )); then
        echo ""
        echo "  ╔══════════════════════════════════════════════════════╗"
        echo "  ║                  ⚠  PORT CONFLICT  ⚠                 ║"
        echo "  ╚══════════════════════════════════════════════════════╝"
        echo ""
        echo "  The selected device shares its USB identity with an existing rule:"
        for _rf in "${_conflict_files[@]}"; do
            echo "    $_rf"
        done
        echo ""
        echo "  Both /dev/meshcore0 and the existing symlink would point to the"
        echo "  SAME physical device, meaning NodeBot would talk to the wrong radio."
        echo ""
        echo "  Fix: plug the MeshCore device into a DIFFERENT USB port than the"
        echo "  rNode / other radio, then re-run this installer."
        echo ""
        echo "  Aborting."
        exit 1
    fi
fi

(
  echo "# MeshCore stable device naming — written by NodeBot MeshCore installer"
  echo "# Creates /dev/meshcore0 tied to device identity."
  echo "# Device reconnects automatically when replugged."
  if [[ -z "$DRIVER_BIND_RULE" ]]; then
    echo "# NOTE: Keep this device in its designated USB port."
    echo "#       If you move it, re-run install_meshcore.sh to update this rule."
  fi
  echo ""
  echo "# Device: $CHOSEN_PORT — ${MESHCORE_LABELS[$CHOSEN_IDX]}"
  [[ -n "$DRIVER_BIND_RULE" ]] && echo "$DRIVER_BIND_RULE"
  echo "$RULE"
) | sudo tee "$UDEV_RULES" > /dev/null

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

# Final sanity check — warn if meshcore0 and rnode0 resolve to the same device
if [ -e "/dev/meshcore0" ] && [ -e "/dev/rnode0" ]; then
    _mc_real=$(readlink -f /dev/meshcore0 2>/dev/null)
    _rn_real=$(readlink -f /dev/rnode0 2>/dev/null)
    if [[ "$_mc_real" == "$_rn_real" && -n "$_mc_real" ]]; then
        echo ""
        echo "  ⚠  WARNING: /dev/meshcore0 and /dev/rnode0 both resolve to $_mc_real"
        echo "  The devices appear to be on the same USB port — NodeBot will malfunction."
        echo "  Move one device to a different port and re-run this installer."
    fi
fi
echo ""

# ── Steps 4+5: Region / frequency configuration and config.ini update ─
echo "[4/4] Radio frequency configuration"
meshcore_configure_radio "meshcore" "$ACTIVE_PORT" "$CONFIG_INI" "$VENV_PYTHON" "$PROJECT_DIR"
echo ""

echo "================================================"
echo "  MeshCore installation complete."
echo "================================================"
echo ""
printf "  Region    : %s\n"     "${MESHCORE_REGION_LABEL:-configured}"
printf "  Frequency : %s MHz\n" "${MESHCORE_FREQ_MHZ:-configured}"
printf "  Forwarding: %s\n"     "${MESHCORE_FORWARD_LABEL:-configured}"
printf "  Symlink   : /dev/meshcore0\n"
printf "  Config    : %s\n"     "$CONFIG_INI"
echo ""
echo "  Restart NodeBot to activate the MeshCore adapter:"
echo "    sudo systemctl restart nodebot"
echo ""
echo "  Live logs:"
echo "    journalctl -u nodebot -f"
echo ""
