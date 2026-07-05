#!/bin/bash
# ============================================================
# NodeBot USB Port Reassignment Utility
#
# Re-assigns which physical USB device is used for each
# installed NodeBot protocol (LXMF rNode, MeshCore, Meshtastic).
#
# Run this if devices have been swapped, moved to different
# ports, or if you are replacing a radio with a new one.
#
# Usage:
#   bash scripts/reassign_usb.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/.venv"
VENV_PYTHON="$VENV/bin/python3"
USER_BIN="$(python3 -m site --user-base 2>/dev/null)/bin"

echo ""
echo "================================================"
echo "  NodeBot USB Port Reassignment"
echo "================================================"
echo ""

# ── Helper: udev property lookup ─────────────────────────────
udev_prop() {
    udevadm info --name="$1" 2>/dev/null | awk -F= "/^E: ${2}=/{print \$2}"
}

# ── Step 1: Discover installed protocols ─────────────────────

declare -A PROTO_RULE
declare -A PROTO_SYMLINK
declare -A PROTO_LABEL
INSTALLED=()

[[ -f /etc/udev/rules.d/99-rnode.rules      ]] && {
    INSTALLED+=("lxmf")
    PROTO_RULE["lxmf"]="/etc/udev/rules.d/99-rnode.rules"
    PROTO_SYMLINK["lxmf"]="rnode0"
    PROTO_LABEL["lxmf"]="LXMF rNode"
}
[[ -f /etc/udev/rules.d/99-meshcore.rules   ]] && {
    INSTALLED+=("meshcore")
    PROTO_RULE["meshcore"]="/etc/udev/rules.d/99-meshcore.rules"
    PROTO_SYMLINK["meshcore"]="meshcore0"
    PROTO_LABEL["meshcore"]="MeshCore"
}
[[ -f /etc/udev/rules.d/99-meshtastic.rules ]] && {
    INSTALLED+=("meshtastic")
    PROTO_RULE["meshtastic"]="/etc/udev/rules.d/99-meshtastic.rules"
    PROTO_SYMLINK["meshtastic"]="meshtastic0"
    PROTO_LABEL["meshtastic"]="Meshtastic"
}
[[ -f /etc/udev/rules.d/99-meshtastic1.rules ]] && {
    INSTALLED+=("meshtastic1")
    PROTO_RULE["meshtastic1"]="/etc/udev/rules.d/99-meshtastic1.rules"
    PROTO_SYMLINK["meshtastic1"]="meshtastic1"
    PROTO_LABEL["meshtastic1"]="Meshtastic 1"
}

if (( ${#INSTALLED[@]} == 0 )); then
    echo "  No NodeBot protocol udev rules found — nothing to reassign."
    echo ""
    exit 0
fi

echo "  Installed protocols:"
for p in "${INSTALLED[@]}"; do
    sym="/dev/${PROTO_SYMLINK[$p]}"
    cur=""
    [[ -L "$sym" ]] && cur=" (currently $(readlink -f "$sym" 2>/dev/null))"
    printf "    %-12s  %s%s\n" "${PROTO_LABEL[$p]}" "$sym" "$cur"
done
echo ""

# ── Step 2: Bind cp210x for non-standard product IDs ─────────
for sysdev in /sys/bus/usb/devices/*/; do
    vid=$(cat "$sysdev/idVendor" 2>/dev/null || true)
    pid=$(cat "$sysdev/idProduct" 2>/dev/null || true)
    [[ "$vid" == "10c4" && "$pid" != "ea60" && -n "$pid" ]] || continue
    if ! ls "$sysdev"*/tty* &>/dev/null 2>&1; then
        echo "  Binding cp210x driver to ${vid}:${pid} ..."
        sudo modprobe cp210x 2>/dev/null || true
        echo "${vid} ${pid}" | sudo tee /sys/bus/usb-serial/drivers/cp210x/new_id >/dev/null 2>&1 || true
        sleep 0.5
    fi
done

# ── Step 3: Enumerate connected USB serial devices ────────────
PORTS=()
PORT_LABELS=()

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
    echo "  No USB serial devices found."
    echo "  Plug in all radio devices and re-run this script."
    echo ""
    exit 1
fi

# ── Stop NodeBot and NomadNet before probing so ports are free ─
_NODEBOT_WAS_RUNNING=false
_NOMADNET_WAS_RUNNING=false

if systemctl is-active --quiet nodebot 2>/dev/null || systemctl is-active --quiet nomadnet 2>/dev/null; then
    echo "  Running services may hold serial ports, causing probe failures."
    systemctl is-active --quiet nodebot   2>/dev/null && echo "    nodebot   — running (holds MeshCore / Meshtastic ports)"
    systemctl is-active --quiet nomadnet  2>/dev/null && echo "    nomadnet  — running (holds rNode port)"
    echo ""
    printf "  Stop these services during probing? (recommended) (yes/no): "
    read -r _STOP_FOR_PROBE || true
    if [[ "${_STOP_FOR_PROBE,,}" == "yes" ]]; then
        if systemctl is-active --quiet nodebot 2>/dev/null; then
            sudo systemctl stop nodebot
            _NODEBOT_WAS_RUNNING=true
            echo "  NodeBot stopped."
        fi
        if systemctl is-active --quiet nomadnet 2>/dev/null; then
            sudo systemctl stop nomadnet
            _NOMADNET_WAS_RUNNING=true
            echo "  NomadNet stopped."
        fi
    fi
    echo ""
fi

# ── Step 4: Pre-scan — probe every device for every protocol ──
echo "  Probing ${#PORTS[@]} device(s) — this may take up to 10 seconds per device..."
echo ""

# Probe scripts embedded as here-strings
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
            print("FAIL:no handshake response")
            return
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
    # A timeout means the port opened but the device didn't complete the
    # protocol handshake — it is present but in an unresponsive state
    # (e.g. needs a replug after repeated probing). Treat as WARN, not FAIL.
    if "timed out" in str(e).lower():
        print("WARN:device present but unresponsive — try replugging")
    else:
        print(f"FAIL:{e}")
PYEOF
)

# Arrays: PROBE_RESULT[port_index][protocol] -> "OK:detail" / "FAIL:reason" / "SKIP"
declare -A PROBE_RESULT  # key = "idx:proto"

# Build a map of real device path -> currently assigned protocol so each port
# is probed for its known protocol first.  A quick OK skips all other probes.
declare -A PORT_CURRENT_PROTO
for _p in "${INSTALLED[@]}"; do
    _sym="/dev/${PROTO_SYMLINK[$_p]}"
    [[ -L "$_sym" ]] || continue
    _real=$(readlink -f "$_sym" 2>/dev/null || true)
    [[ -n "$_real" ]] && PORT_CURRENT_PROTO["$_real"]="$_p"
done

for i in "${!PORTS[@]}"; do
    port="${PORTS[$i]}"
    label="${PORT_LABELS[$i]}"
    printf "  [%d] %-16s  %s\n" $((i+1)) "$port" "$label"

    # Probe order: currently-assigned protocol first, then others.
    # This avoids sending foreign-protocol handshakes to devices that are
    # already confirmed, and reduces stress on sensitive devices (e.g. T-Beam).
    _cur="${PORT_CURRENT_PROTO[$port]:-}"
    _probe_order=()
    [[ -n "$_cur" ]] && _probe_order+=("$_cur")
    for _p in "${INSTALLED[@]}"; do
        [[ "$_p" == "$_cur" ]] && continue
        _probe_order+=("$_p")
    done

    for proto in "${_probe_order[@]}"; do
        printf "       %-12s  checking ... " "${PROTO_LABEL[$proto]}"

        # Cross-protocol skip: if this port already got OK for a different
        # protocol, there is no point running a slow probe here — it cannot
        # be two things at once, and the foreign-protocol handshake can leave
        # sensitive devices (e.g. ESP32-based Meshtastic nodes) in a bad state.
        _skip_cross=false
        for _other in "${INSTALLED[@]}"; do
            [[ "$_other" == "$proto" ]] && continue
            [[ "${PROBE_RESULT["$i:$_other"]:-SKIP}" == OK:* ]] && _skip_cross=true && break
        done

        result="SKIP"
        if $_skip_cross; then
            result="SKIP:port confirmed as another protocol"
        else
        case "$proto" in
        meshcore)
            if [[ -f "$VENV_PYTHON" ]]; then
                result=$("$VENV_PYTHON" -c "$MC_PROBE_PY" "$port" 2>/dev/null || echo "FAIL:probe error")
            else
                result="SKIP:venv not found"
            fi
            ;;
        lxmf)
            # Try rnodeconf if available, else use USB product ID heuristic
            if command -v rnodeconf &>/dev/null || [[ -x "$USER_BIN/rnodeconf" ]]; then
                RNODECONF="${USER_BIN}/rnodeconf"
                command -v rnodeconf &>/dev/null && RNODECONF="rnodeconf"
                rn_out=$(timeout 6 "$RNODECONF" --info "$port" 2>&1 || true)
                if echo "$rn_out" | grep -qi "firmware" && \
                   ! echo "$rn_out" | grep -qi "did not respond\|not respond\|no device\|permission denied"; then
                    fw=$(echo "$rn_out" | grep -i "firmware" | head -1 | sed 's/.*: *//' || true)
                    result="OK:${fw:-rNode detected}"
                else
                    result="FAIL:no rNode response"
                fi
            else
                # Heuristic: standard CP2102 (ea60) is most commonly an rNode
                prod_id=$(udev_prop "$port" "ID_MODEL_ID")
                vid=$(udev_prop "$port" "ID_VENDOR_ID")
                if [[ "$vid" == "10c4" && "$prod_id" == "ea60" ]]; then
                    result="WARN:looks like CP2102 (typical rNode) — rnodeconf not available to confirm"
                else
                    result="WARN:rnodeconf not available — cannot confirm rNode identity"
                fi
            fi
            ;;
        meshtastic|meshtastic1)
            if [[ -f "$VENV_PYTHON" ]] && "$VENV_PYTHON" -c "import meshtastic" &>/dev/null 2>&1; then
                result=$(timeout 35 "$VENV_PYTHON" -c "$MESHTASTIC_PROBE_PY" "$port" 2>/dev/null || echo "FAIL:probe error")
                # If the probe timed out, toggle DTR to reset the device's serial
                # state (ESP32-based boards reset on DTR toggle). This prevents
                # the device accumulating bad state across repeated probe runs.
                if [[ "$result" == WARN:* || "$result" == "FAIL:probe error" ]]; then
                    "$VENV_PYTHON" - "$port" <<'PYEOF' 2>/dev/null || true
import sys, time, serial
try:
    s = serial.Serial(sys.argv[1], 115200, timeout=0.2)
    s.dtr = False; time.sleep(0.1); s.dtr = True
    s.close()
except Exception:
    pass
PYEOF
                fi
            else
                result="WARN:meshtastic library not available — cannot confirm"
            fi
            ;;
        esac
        fi  # end cross-protocol skip

        PROBE_RESULT["$i:$proto"]="$result"

        # Print status
        if [[ "$result" == OK:* ]];   then
            detail="${result#OK:}"
            echo "OK${detail:+  ($detail)}"
        elif [[ "$result" == WARN:* ]]; then
            detail="${result#WARN:}"
            echo "WARN${detail:+  ($detail)}"
        elif [[ "$result" == FAIL:* ]]; then
            detail="${result#FAIL:}"
            echo "no  ${detail:+($detail)}"
        else
            echo "skipped"
        fi
    done
    echo ""
done

# ── Step 5: Suggest assignments based on probe results ────────
# Find the best (or only OK/WARN) port for each protocol
declare -A SUGGESTED_IDX

for proto in "${INSTALLED[@]}"; do
    best_idx=""
    best_rank=99
    for i in "${!PORTS[@]}"; do
        r="${PROBE_RESULT["$i:$proto"]:-SKIP}"
        rank=99
        [[ "$r" == OK:*   ]] && rank=0
        [[ "$r" == WARN:* ]] && rank=1
        if (( rank < best_rank )); then
            best_rank=$rank
            best_idx=$i
        fi
    done
    # Only promote a WARN suggestion if there is no current assignment to fall
    # back to — avoids suggesting the wrong device when every port looks the
    # same (e.g. all three CP210x devices WARN for Meshtastic).
    if [[ -n "$best_idx" && "$best_rank" -eq 0 ]]; then
        SUGGESTED_IDX["$proto"]=$best_idx
    elif [[ -n "$best_idx" && "$best_rank" -eq 1 ]]; then
        # Check if the current symlink resolves to one of the discovered ports
        sym="/dev/${PROTO_SYMLINK[$proto]}"
        current_port=""
        [[ -L "$sym" ]] && current_port=$(readlink -f "$sym" 2>/dev/null || true)
        has_current=false
        for i in "${!PORTS[@]}"; do
            real=$(readlink -f "${PORTS[$i]}" 2>/dev/null || echo "${PORTS[$i]}")
            [[ "$real" == "$current_port" ]] && has_current=true && break
        done
        # Only use the WARN suggestion if there is no existing assignment to fall back to
        $has_current || SUGGESTED_IDX["$proto"]=$best_idx
    fi
done

# ── Step 6: User assignment ───────────────────────────────────
echo "  ── Device Assignment ───────────────────────────────────"
echo "  Enter the device number for each protocol."
echo "  Suggestions are based on the probe results above."
echo "  Press Enter to accept the suggestion."
echo ""

declare -A NEW_PORT
declare -A NEW_IDX

for proto in "${INSTALLED[@]}"; do
    label="${PROTO_LABEL[$proto]}"
    sym="/dev/${PROTO_SYMLINK[$proto]}"
    current_port=""
    [[ -L "$sym" ]] && current_port=$(readlink -f "$sym" 2>/dev/null || true)

    # Determine suggestion: prefer probe-based, fall back to current assignment
    sug_idx="${SUGGESTED_IDX[$proto]:-}"
    if [[ -z "$sug_idx" ]]; then
        for i in "${!PORTS[@]}"; do
            real=$(readlink -f "${PORTS[$i]}" 2>/dev/null || echo "${PORTS[$i]}")
            [[ "$real" == "$current_port" ]] && sug_idx=$i && break
        done
    fi

    echo "  ── ${label} ─────────────────────────────────"
    for i in "${!PORTS[@]}"; do
        probe_r="${PROBE_RESULT["$i:$proto"]:-SKIP}"
        if   [[ "$probe_r" == OK:*   ]]; then status="  ✓"
        elif [[ "$probe_r" == WARN:* ]]; then status="  ?"
        elif [[ "$probe_r" == FAIL:* ]]; then status="  ✗"
        else                                  status="   "
        fi
        sug=""
        [[ "$i" == "$sug_idx" ]] && sug=" ← suggested"
        printf "    %d)%s  %-16s  %s%s\n" \
            $((i+1)) "$status" "${PORTS[$i]}" "${PORT_LABELS[$i]}" "$sug"
    done
    echo ""

    default_display=$((${sug_idx:-0} + 1))
    while true; do
        printf "  Select device for %s [default %d]: " "$label" "$default_display"
        read -r choice || true
        [[ -z "$choice" ]] && choice="$default_display"

        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#PORTS[@]} )); then
            idx=$((choice - 1))
            NEW_PORT["$proto"]="${PORTS[$idx]}"
            NEW_IDX["$proto"]=$idx
            break
        fi
        echo "  Please enter a number between 1 and ${#PORTS[@]}."
    done
    echo ""
done

# ── Step 7: Conflict check ────────────────────────────────────
any_conflict=false
for a in "${INSTALLED[@]}"; do
    for b in "${INSTALLED[@]}"; do
        [[ "$a" == "$b" ]] && continue
        [[ "${NEW_PORT[$a]}" == "${NEW_PORT[$b]}" ]] || continue
        echo "  ⚠  CONFLICT: ${PROTO_LABEL[$a]} and ${PROTO_LABEL[$b]} both assigned to ${NEW_PORT[$a]}"
        any_conflict=true
    done
done

if $any_conflict; then
    echo ""
    echo "  Two protocols cannot share the same device. Re-run and choose different devices."
    exit 1
fi

# ── Step 8: Verification probes on chosen assignments ─────────
echo "  ── Verifying chosen assignments ────────────────────────"
echo ""

all_verified=true
declare -A VERIFY_STATUS

for proto in "${INSTALLED[@]}"; do
    idx="${NEW_IDX[$proto]}"
    port="${NEW_PORT[$proto]}"
    label="${PROTO_LABEL[$proto]}"
    cached="${PROBE_RESULT["$idx:$proto"]:-SKIP}"

    printf "  %-12s  %s  " "$label" "$port"

    # Re-use cached probe result (probe already ran on this port for this protocol)
    status="FAIL"
    detail=""
    if   [[ "$cached" == OK:*   ]]; then status="OK";   detail="${cached#OK:}"
    elif [[ "$cached" == WARN:* ]]; then status="WARN"; detail="${cached#WARN:}"
    elif [[ "$cached" == SKIP:* ]]; then status="WARN"; detail="could not probe — check manually"
    else                                 status="FAIL"; detail="${cached#FAIL:}"; all_verified=false
    fi

    VERIFY_STATUS["$proto"]="$status"

    if   [[ "$status" == "OK"   ]]; then echo "✓  verified${detail:+  ($detail)}"
    elif [[ "$status" == "WARN" ]]; then echo "?  unconfirmed${detail:+  ($detail)}"
    else                                 echo "✗  WRONG DEVICE?  ${detail}"
    fi
done
echo ""

# ── Step 9: Summary and apply decision ───────────────────────
echo "  ── Summary ─────────────────────────────────────────────"
for proto in "${INSTALLED[@]}"; do
    idx="${NEW_IDX[$proto]}"
    vstatus="${VERIFY_STATUS[$proto]}"
    flag="✓"
    [[ "$vstatus" == "WARN" ]] && flag="?"
    [[ "$vstatus" == "FAIL" ]] && flag="✗"
    printf "    %s  %-12s  %s  (%s)\n" \
        "$flag" "${PROTO_LABEL[$proto]}" "${NEW_PORT[$proto]}" "${PORT_LABELS[$idx]}"
done
echo "  ────────────────────────────────────────────────────────"
echo ""

if ! $all_verified; then
    echo "  ⚠  One or more devices did not pass verification."
    echo "  Applying the wrong device will prevent that protocol from working."
    echo ""
    printf "  Proceed anyway? (yes/no): "
    read -r FORCE || true
    if [[ "${FORCE,,}" != "yes" ]]; then
        echo "  Aborted — no changes made."
        exit 1
    fi
else
    printf "  Apply assignments? (yes/no): "
    read -r CONFIRM || true
    if [[ "${CONFIRM,,}" != "yes" ]]; then
        echo "  Aborted — no changes made."
        exit 0
    fi
fi
echo ""

# ── Step 10: Generate and write udev rules ────────────────────
make_rule() {
    local port="$1" symlink="$2" label="$3"
    local id_serial id_serial_short id_path id_vendor id_model_id
    id_serial=$(udev_prop "$port" "ID_SERIAL")
    id_serial_short=$(udev_prop "$port" "ID_SERIAL_SHORT")
    id_path=$(udev_prop "$port" "ID_PATH")
    id_vendor=$(udev_prop "$port" "ID_VENDOR_ID")
    id_model_id=$(udev_prop "$port" "ID_MODEL_ID")

    local is_generic=false
    local g; for g in "0001" "0000" "1234567890" "ABCDEF" ""; do
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

    echo "# NodeBot USB device rule — updated by reassign_usb.sh on $(date '+%Y-%m-%d %H:%M')"
    echo "# Protocol: $label"
    echo "# Device:   $port — $(udev_prop "$port" "ID_VENDOR") $(udev_prop "$port" "ID_MODEL") (S/N: ${id_serial_short:-none})"
    [[ -n "$bind_rule" ]] && echo "$bind_rule"
    echo "$rule"
}

stop_service=false
if systemctl is-active --quiet nodebot 2>/dev/null; then
    echo "  Stopping nodebot service..."
    sudo systemctl stop nodebot
    stop_service=true
elif $_NODEBOT_WAS_RUNNING; then
    # Already stopped for probing — still need to restart after
    stop_service=true
fi

stop_nomadnet=false
if systemctl is-active --quiet nomadnet 2>/dev/null; then
    echo "  Stopping nomadnet service..."
    sudo systemctl stop nomadnet
    stop_nomadnet=true
elif $_NOMADNET_WAS_RUNNING; then
    stop_nomadnet=true
fi

for proto in "${INSTALLED[@]}"; do
    port="${NEW_PORT[$proto]}"
    sym="${PROTO_SYMLINK[$proto]}"
    rule_file="${PROTO_RULE[$proto]}"
    label="${PROTO_LABEL[$proto]}"
    printf "  Writing %-12s → %s ... " "$label" "$port"
    make_rule "$port" "$sym" "$label" | sudo tee "$rule_file" > /dev/null
    echo "done"
done

echo ""
echo "  Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger
sleep 1

# ── Step 11: Final symlink check ──────────────────────────────
echo ""
echo "  ── Final check ─────────────────────────────────────────"
all_ok=true
for proto in "${INSTALLED[@]}"; do
    sym="/dev/${PROTO_SYMLINK[$proto]}"
    if [[ -L "$sym" ]]; then
        real=$(readlink -f "$sym")
        printf "    ✓  %-12s  %s → %s\n" "${PROTO_LABEL[$proto]}" "$sym" "$real"
    else
        printf "    ✗  %-12s  %s  (symlink missing — try replugging the device)\n" \
            "${PROTO_LABEL[$proto]}" "$sym"
        all_ok=false
    fi
done
echo "  ────────────────────────────────────────────────────────"

if $stop_service; then
    echo ""
    echo "  Restarting nodebot service..."
    sudo systemctl start nodebot
fi

if $stop_nomadnet; then
    echo ""
    echo "  Restarting nomadnet service..."
    sudo systemctl start nomadnet
fi

echo ""
$all_ok && echo "  Done — all devices assigned and verified." \
        || echo "  Done — some symlinks missing, check device connections."
echo ""
