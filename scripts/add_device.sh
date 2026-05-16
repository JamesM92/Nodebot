#!/bin/bash
# ============================================================
# NodeBot Add Device Utility
#
# Assigns a newly connected USB radio to a NodeBot protocol
# slot (e.g. meshtastic1, meshcore1, rnode1).
#
# Use this when adding a radio that has no existing rule —
# including a second device for a protocol already in use.
#
# Usage:
#   bash scripts/add_device.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# shellcheck source=_meshtastic_config.sh
source "$SCRIPT_DIR/_meshtastic_config.sh"
# shellcheck source=_meshcore_config.sh
source "$SCRIPT_DIR/_meshcore_config.sh"
# shellcheck source=_lxmf_config.sh
source "$SCRIPT_DIR/_lxmf_config.sh"
VENV="$PROJECT_DIR/.venv"
VENV_PYTHON="$VENV/bin/python3"
USER_BIN="$(python3 -m site --user-base 2>/dev/null)/bin"
UDEV_DIR="/etc/udev/rules.d"

echo ""
echo "================================================"
echo "  NodeBot Add Device"
echo "================================================"
echo ""

# ── Helper: udev property lookup ─────────────────────────────
udev_prop() {
    udevadm info --name="$1" 2>/dev/null | awk -F= "/^E: ${2}=/{print \$2}"
}

# ── Discover all existing protocol slots ─────────────────────
# Map symlink name → rule file, e.g. meshtastic0 → 99-meshtastic.rules
declare -A SLOT_RULE      # symlink → rule file
declare -A SLOT_LABEL     # symlink → display label
declare -A SLOT_PROTO     # symlink → proto key

_register_slot() {
    local file="$1" symlink="$2" label="$3" proto="$4"
    [[ -f "$file" ]] || return
    SLOT_RULE["$symlink"]="$file"
    SLOT_LABEL["$symlink"]="$label"
    SLOT_PROTO["$symlink"]="$proto"
}

# Scan for all installed rule files (base + numbered extras)
for f in "$UDEV_DIR"/99-rnode*.rules "$UDEV_DIR"/99-meshcore*.rules "$UDEV_DIR"/99-meshtastic*.rules; do
    [[ -f "$f" ]] || continue
    base=$(basename "$f" .rules)
    case "$base" in
        99-rnode)          _register_slot "$f" "rnode0"        "LXMF rNode (slot 0)" "lxmf"       ;;
        99-rnode[0-9]*)    n="${base#99-rnode}";    _register_slot "$f" "rnode${n}"       "LXMF rNode (slot ${n})"       "lxmf"       ;;
        99-meshcore)       _register_slot "$f" "meshcore0"     "MeshCore (slot 0)"   "meshcore"   ;;
        99-meshcore[0-9]*) n="${base#99-meshcore}"; _register_slot "$f" "meshcore${n}"    "MeshCore (slot ${n})"         "meshcore"   ;;
        99-meshtastic)     _register_slot "$f" "meshtastic0"   "Meshtastic (slot 0)" "meshtastic" ;;
        99-meshtastic[0-9]*) n="${base#99-meshtastic}"; _register_slot "$f" "meshtastic${n}" "Meshtastic (slot ${n})"   "meshtastic" ;;
    esac
done

if (( ${#SLOT_RULE[@]} == 0 )); then
    echo "  No NodeBot udev rules found. Run the installer first."
    echo ""
    exit 1
fi

echo "  Currently assigned slots:"
for sym in $(echo "${!SLOT_RULE[@]}" | tr ' ' '\n' | sort); do
    cur=""
    [[ -L "/dev/$sym" ]] && cur=" → $(readlink -f "/dev/$sym" 2>/dev/null)"
    printf "    %-14s  %s%s\n" "/dev/$sym" "${SLOT_LABEL[$sym]}" "$cur"
done
echo ""

# ── Collect all currently-assigned real device paths ─────────
declare -A ASSIGNED_REAL   # real path → symlink
for sym in "${!SLOT_RULE[@]}"; do
    [[ -L "/dev/$sym" ]] || continue
    real=$(readlink -f "/dev/$sym" 2>/dev/null || true)
    [[ -n "$real" ]] && ASSIGNED_REAL["$real"]="$sym"
done

# ── Enumerate connected USB serial devices ────────────────────
PORTS=()
PORT_LABELS=()

# Bind cp210x for non-standard product IDs
for sysdev in /sys/bus/usb/devices/*/; do
    vid=$(cat "$sysdev/idVendor" 2>/dev/null || true)
    pid=$(cat "$sysdev/idProduct" 2>/dev/null || true)
    [[ "$vid" == "10c4" && "$pid" != "ea60" && -n "$pid" ]] || continue
    if ! ls "$sysdev"*/tty* &>/dev/null 2>&1; then
        sudo modprobe cp210x 2>/dev/null || true
        echo "${vid} ${pid}" | sudo tee /sys/bus/usb-serial/drivers/cp210x/new_id >/dev/null 2>&1 || true
        sleep 0.5
    fi
done

for port in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$port" ] || continue
    vendor=$(udev_prop "$port" "ID_VENDOR")
    model=$(udev_prop "$port" "ID_MODEL")
    serial=$(udev_prop "$port" "ID_SERIAL_SHORT")
    prod_id=$(udev_prop "$port" "ID_MODEL_ID")
    PORTS+=("$port")
    PORT_LABELS+=("${vendor:-?} ${model:-?}  S/N:${serial:-none}  pid:${prod_id:-?}")
done

if (( ${#PORTS[@]} == 0 )); then
    echo "  No USB serial devices found. Plug in the new device and re-run."
    echo ""
    exit 1
fi

# ── Identify unassigned devices ───────────────────────────────
UNASSIGNED_IDX=()
for i in "${!PORTS[@]}"; do
    real=$(readlink -f "${PORTS[$i]}" 2>/dev/null || echo "${PORTS[$i]}")
    if [[ -z "${ASSIGNED_REAL[$real]:-}" ]]; then
        UNASSIGNED_IDX+=("$i")
    fi
done

if (( ${#UNASSIGNED_IDX[@]} == 0 )); then
    echo "  All connected devices are already assigned to a slot."
    echo "  To reassign existing devices, use: bash scripts/reassign_usb.sh"
    echo ""
    exit 0
fi

echo "  Unassigned device(s) found:"
for i in "${UNASSIGNED_IDX[@]}"; do
    printf "    /dev/%s  —  %s\n" "$(basename "${PORTS[$i]}")" "${PORT_LABELS[$i]}"
done
echo ""

# ── Stop services for clean probing ──────────────────────────
_NODEBOT_WAS_RUNNING=false
_NOMADNET_WAS_RUNNING=false

if systemctl is-active --quiet nodebot 2>/dev/null || \
   systemctl is-active --quiet nomadnet 2>/dev/null; then
    echo "  Running services hold serial ports. Stop them for probing?"
    printf "  Stop services during probing? (recommended) (yes/no): "
    read -r _STOP || true
    if [[ "${_STOP,,}" == "yes" ]]; then
        if systemctl is-active --quiet nodebot 2>/dev/null; then
            sudo systemctl stop nodebot; _NODEBOT_WAS_RUNNING=true; echo "  NodeBot stopped."
        fi
        if systemctl is-active --quiet nomadnet 2>/dev/null; then
            sudo systemctl stop nomadnet; _NOMADNET_WAS_RUNNING=true; echo "  NomadNet stopped."
        fi
    fi
    echo ""
fi

# ── Embedded probe scripts ────────────────────────────────────
MC_PROBE_PY=$(cat <<'PYEOF'
import sys, asyncio
async def probe(port):
    from meshcore.meshcore import MeshCore
    from meshcore.serial_cx import SerialConnection
    from meshcore.events import EventType
    found = asyncio.Event()
    result = {"name": "", "pubkey": ""}
    async def on_self_info(event):
        result["name"]   = event.payload.get("name", "")
        result["pubkey"] = event.payload.get("public_key", "")[:8]
        found.set()
    try:
        cx = SerialConnection(port, 115200)
        mc = MeshCore(cx)
        mc.subscribe(EventType.SELF_INFO, on_self_info)
        conn = await asyncio.wait_for(mc.connect(), timeout=5)
        if conn is None:
            print("FAIL:no handshake response"); return
        try:
            await asyncio.wait_for(found.wait(), timeout=3)
            print(f"OK:{result['name']}:{result['pubkey']}")
        except asyncio.TimeoutError:
            print("FAIL:no SELF_INFO")
        await mc.disconnect()
    except Exception as e:
        print(f"FAIL:{e}")
asyncio.run(probe(sys.argv[1]))
PYEOF
)

MESHTASTIC_PROBE_PY=$(cat <<'PYEOF'
import sys
try:
    import meshtastic.serial_interface as mi
    iface = mi.SerialInterface(devPath=sys.argv[1])
    node = iface.myInfo
    name = getattr(node, "long_name", "") or ""
    iface.close()
    print(f"OK:{name}")
except Exception as e:
    if "timed out" in str(e).lower():
        print("WARN:device present but unresponsive — try replugging")
    else:
        print(f"FAIL:{e}")
PYEOF
)

# ── Probe unassigned devices ──────────────────────────────────
echo "  Probing ${#UNASSIGNED_IDX[@]} unassigned device(s)..."
echo ""

declare -A PROBE   # idx:proto → result

for i in "${UNASSIGNED_IDX[@]}"; do
    port="${PORTS[$i]}"
    printf "  [%d] %-16s  %s\n" $((i+1)) "$port" "${PORT_LABELS[$i]}"

    for proto in lxmf meshcore meshtastic; do
        printf "       %-12s  checking ... " "$proto"
        result="SKIP"
        case "$proto" in
        meshcore)
            if [[ -f "$VENV_PYTHON" ]]; then
                result=$("$VENV_PYTHON" -c "$MC_PROBE_PY" "$port" 2>/dev/null || echo "FAIL:probe error")
            else
                result="SKIP:venv not found"
            fi
            ;;
        lxmf)
            if command -v rnodeconf &>/dev/null || [[ -x "$USER_BIN/rnodeconf" ]]; then
                RNODECONF="rnodeconf"; [[ -x "$USER_BIN/rnodeconf" ]] && RNODECONF="$USER_BIN/rnodeconf"
                rn_out=$(timeout 6 "$RNODECONF" --info "$port" 2>&1 || true)
                if echo "$rn_out" | grep -qi "firmware" && \
                   ! echo "$rn_out" | grep -qi "did not respond\|not respond"; then
                    fw=$(echo "$rn_out" | grep -i "firmware" | head -1 | sed 's/.*: *//' || true)
                    result="OK:${fw:-rNode detected}"
                else
                    result="FAIL:no rNode response"
                fi
            else
                prod_id=$(udev_prop "$port" "ID_MODEL_ID"); vid=$(udev_prop "$port" "ID_VENDOR_ID")
                [[ "$vid" == "10c4" && "$prod_id" == "ea60" ]] \
                    && result="WARN:looks like CP2102 (typical rNode) — rnodeconf unavailable" \
                    || result="WARN:rnodeconf not available — cannot confirm rNode identity"
            fi
            ;;
        meshtastic)
            if [[ -f "$VENV_PYTHON" ]] && "$VENV_PYTHON" -c "import meshtastic" &>/dev/null 2>&1; then
                result=$(timeout 35 "$VENV_PYTHON" -c "$MESHTASTIC_PROBE_PY" "$port" 2>/dev/null || echo "FAIL:probe error")
                if [[ "$result" == WARN:* || "$result" == "FAIL:probe error" ]]; then
                    "$VENV_PYTHON" - "$port" <<'PYEOF' 2>/dev/null || true
import sys, time, serial
try:
    s = serial.Serial(sys.argv[1], 115200, timeout=0.2)
    s.dtr = False; time.sleep(0.1); s.dtr = True; s.close()
except Exception:
    pass
PYEOF
                fi
            else
                result="WARN:meshtastic library not available"
            fi
            ;;
        esac
        PROBE["$i:$proto"]="$result"

        if   [[ "$result" == OK:*   ]]; then echo "OK  (${result#OK:})"
        elif [[ "$result" == WARN:* ]]; then echo "WARN  (${result#WARN:})"
        elif [[ "$result" == FAIL:* ]]; then echo "no"
        else                                 echo "skipped"
        fi
    done
    echo ""
done

# ── Select device ─────────────────────────────────────────────
if (( ${#UNASSIGNED_IDX[@]} == 1 )); then
    SEL_I="${UNASSIGNED_IDX[0]}"
    echo "  Using the only unassigned device: ${PORTS[$SEL_I]}"
    echo ""
else
    echo "  ── Select device to add ────────────────────────────────"
    for idx in "${UNASSIGNED_IDX[@]}"; do
        printf "    %d)  %-16s  %s\n" $((idx+1)) "${PORTS[$idx]}" "${PORT_LABELS[$idx]}"
    done
    echo ""
    while true; do
        printf "  Device number: "
        read -r choice || true
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#PORTS[@]} )); then
            SEL_I=$((choice-1))
            # Must be in unassigned list
            _ok=false
            for ui in "${UNASSIGNED_IDX[@]}"; do [[ "$ui" == "$SEL_I" ]] && _ok=true && break; done
            $_ok && break
        fi
        echo "  Please enter one of the listed numbers."
    done
    echo ""
fi

SEL_PORT="${PORTS[$SEL_I]}"

# ── Select protocol ───────────────────────────────────────────
echo "  ── Select protocol ─────────────────────────────────────"
PROTO_OPTS=("meshtastic" "meshcore" "lxmf")
PROTO_NAMES=("Meshtastic" "MeshCore" "LXMF rNode")
for pi in "${!PROTO_OPTS[@]}"; do
    p="${PROTO_OPTS[$pi]}"
    probe_r="${PROBE["$SEL_I:$p"]:-SKIP}"
    if   [[ "$probe_r" == OK:*   ]]; then flag="✓"
    elif [[ "$probe_r" == WARN:* ]]; then flag="?"
    elif [[ "$probe_r" == FAIL:* ]]; then flag="✗"
    else                                  flag=" "
    fi
    printf "    %d)  %s  %s\n" $((pi+1)) "$flag" "${PROTO_NAMES[$pi]}"
done
echo ""

# Default: first protocol with OK probe
DEF_PI=0
for pi in "${!PROTO_OPTS[@]}"; do
    [[ "${PROBE["$SEL_I:${PROTO_OPTS[$pi]}"]:-SKIP}" == OK:* ]] && DEF_PI=$pi && break
done

while true; do
    printf "  Protocol [default %d]: " $((DEF_PI+1))
    read -r pchoice || true
    [[ -z "$pchoice" ]] && pchoice=$((DEF_PI+1))
    if [[ "$pchoice" =~ ^[1-3]$ ]]; then
        SEL_PROTO="${PROTO_OPTS[$((pchoice-1))]}"
        SEL_PROTO_LABEL="${PROTO_NAMES[$((pchoice-1))]}"
        break
    fi
    echo "  Please enter 1, 2, or 3."
done
echo ""

# ── Determine next available slot ────────────────────────────
case "$SEL_PROTO" in
    lxmf)        _base_sym="rnode";       _base_rule="99-rnode"       ;;
    meshcore)    _base_sym="meshcore";    _base_rule="99-meshcore"    ;;
    meshtastic)  _base_sym="meshtastic";  _base_rule="99-meshtastic"  ;;
esac

_slot=0
while [[ -f "$UDEV_DIR/${_base_rule}${_slot:+$_slot}.rules" ]]; do
    # Slot 0 uses bare filename (99-meshtastic.rules), slot 1+ appends number
    _slot=$(( _slot == 0 ? 1 : _slot + 1 ))
done

if (( _slot == 0 )); then
    NEW_SYM="${_base_sym}0"
    NEW_RULE="$UDEV_DIR/${_base_rule}.rules"
else
    NEW_SYM="${_base_sym}${_slot}"
    NEW_RULE="$UDEV_DIR/${_base_rule}${_slot}.rules"
fi
NEW_LABEL="$SEL_PROTO_LABEL (slot ${_slot})"

echo "  Will create:  /dev/$NEW_SYM  →  $SEL_PORT"
echo "  Rule file:    $NEW_RULE"
echo ""

# ── Confirm ───────────────────────────────────────────────────
printf "  Apply? (yes/no): "
read -r CONFIRM || true
if [[ "${CONFIRM,,}" != "yes" ]]; then
    echo "  Aborted — no changes made."

    if $_NODEBOT_WAS_RUNNING;  then sudo systemctl start nodebot  2>/dev/null || true; fi
    if $_NOMADNET_WAS_RUNNING; then sudo systemctl start nomadnet 2>/dev/null || true; fi
    exit 0
fi
echo ""

# ── Write udev rule ───────────────────────────────────────────
make_rule() {
    local port="$1" symlink="$2" label="$3"
    local id_serial id_serial_short id_path id_vendor id_model_id
    id_serial=$(udev_prop "$port" "ID_SERIAL")
    id_serial_short=$(udev_prop "$port" "ID_SERIAL_SHORT")
    id_path=$(udev_prop "$port" "ID_PATH")
    id_vendor=$(udev_prop "$port" "ID_VENDOR_ID")
    id_model_id=$(udev_prop "$port" "ID_MODEL_ID")

    local is_generic=false
    for g in "0001" "0000" "1234567890" "ABCDEF" ""; do
        [[ "$id_serial_short" == "$g" ]] && is_generic=true && break
    done

    local rule="" bind_rule=""

    if [[ -n "$id_serial" ]] && ! $is_generic; then
        rule="SUBSYSTEM==\"tty\", ENV{ID_SERIAL}==\"${id_serial}\", GROUP=\"dialout\", MODE=\"0660\", SYMLINK+=\"${symlink}\""
    elif [[ "$id_vendor" == "10c4" && "$id_model_id" != "ea60" && -n "$id_model_id" ]]; then
        bind_rule="ACTION==\"add\", SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"10c4\", ATTRS{idProduct}==\"${id_model_id}\", RUN+=\"/bin/sh -c 'echo 10c4 ${id_model_id} > /sys/bus/usb-serial/drivers/cp210x/new_id'\""
        rule="SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"10c4\", ATTRS{idProduct}==\"${id_model_id}\", GROUP=\"dialout\", MODE=\"0660\", SYMLINK+=\"${symlink}\""
    elif [[ "$id_vendor" == "1a86" ]]; then
        rule="SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"1a86\", ATTRS{idProduct}==\"${id_model_id}\", GROUP=\"dialout\", MODE=\"0660\", SYMLINK+=\"${symlink}\""
    else
        rule="SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"${id_path}\", GROUP=\"dialout\", MODE=\"0660\", SYMLINK+=\"${symlink}\""
    fi

    echo "# NodeBot USB device rule — created by add_device.sh on $(date '+%Y-%m-%d %H:%M')"
    echo "# Protocol: $label"
    echo "# Device:   $port — $(udev_prop "$port" "ID_VENDOR") $(udev_prop "$port" "ID_MODEL") (S/N: ${id_serial_short:-none})"
    [[ -n "$bind_rule" ]] && echo "$bind_rule"
    echo "$rule"
}

make_rule "$SEL_PORT" "$NEW_SYM" "$NEW_LABEL" | sudo tee "$NEW_RULE" > /dev/null
echo "  Written: $NEW_RULE"

sudo udevadm control --reload-rules
sudo udevadm trigger
sleep 1

# ── Final check ───────────────────────────────────────────────
echo ""
if [[ -L "/dev/$NEW_SYM" ]]; then
    real=$(readlink -f "/dev/$NEW_SYM")
    echo "  ✓  /dev/$NEW_SYM → $real"
else
    echo "  ✗  /dev/$NEW_SYM did not appear — try replugging the device."
fi

# ── Update config.ini ────────────────────────────────────────
CONFIG_INI="$PROJECT_DIR/config.ini"

# Determine the config section name for this slot.
# Slot 0 is the base section (already exists from installer); slot 1+ gets a numbered section.
case "$SEL_PROTO" in
    lxmf)       _cfg_sec_base="lxmf"       ;;
    meshcore)   _cfg_sec_base="meshcore"   ;;
    meshtastic) _cfg_sec_base="meshtastic" ;;
esac

if (( _slot == 0 )); then
    _cfg_sec="$_cfg_sec_base"
else
    _cfg_sec="${_cfg_sec_base}${_slot}"
fi

if [[ -f "$CONFIG_INI" ]]; then
    # Check if section already present
    if python3 -c "
import configparser, sys
c = configparser.ConfigParser()
c.read('$CONFIG_INI')
sys.exit(0 if '$_cfg_sec' in c else 1)
" 2>/dev/null; then
        echo "  config.ini already has [$_cfg_sec] — no changes made."
    else
        echo ""
        echo "  ── config.ini update ───────────────────────────────────"

        case "$SEL_PROTO" in
        meshtastic)
            # Full radio configuration via shared library (region, preset, hop limit, TX power, program radio)
            meshtastic_configure_radio "$_cfg_sec" "/dev/$NEW_SYM" "$CONFIG_INI" "$VENV_PYTHON"
            ;;
        meshcore)
            meshcore_configure_radio "$_cfg_sec" "/dev/$NEW_SYM" "$CONFIG_INI" "$VENV_PYTHON" "$PROJECT_DIR"
            ;;
        lxmf)
            lxmf_configure_rnode "/dev/$NEW_SYM" "$HOME/.reticulum/config" "$VENV_PYTHON" "$PROJECT_DIR"
            ;;
        esac
    fi
else
    echo "  config.ini not found at $CONFIG_INI — skipping config update."
fi

# ── Restart services ──────────────────────────────────────────
if $_NODEBOT_WAS_RUNNING;  then echo ""; echo "  Restarting nodebot...";  sudo systemctl start nodebot;  fi
if $_NOMADNET_WAS_RUNNING; then echo ""; echo "  Restarting nomadnet..."; sudo systemctl start nomadnet; fi

echo ""
echo "  Done. /dev/$NEW_SYM is assigned and config.ini updated."
echo "  Restart NodeBot to activate: sudo systemctl restart nodebot"
echo ""
