#!/bin/bash
# ============================================================
# Shared MeshCore radio configuration library
#
# Source this file; then call:
#   meshcore_configure_radio <cfg_sec> <port> <cfg_ini> <venv_py> <project_dir>
#
# Prompts for region/frequency/BW/SF/CR, hop limit, and channels.
# Offers to program the radio immediately via the MeshCore library.
# Writes the config.ini section.
#
# After the call these global vars are set (for summary display):
#   MESHCORE_REGION_LABEL   MESHCORE_FREQ_MHZ   MESHCORE_FORWARD_LABEL
# ============================================================

_mc_pick() {
    local prompt="$1" max="$2" choice
    while true; do
        printf "  %s [1-%d]: " "$prompt" "$max" >&2
        read -r choice || true
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= max )); then
            echo "$choice"; return
        fi
        echo "  Please enter a number between 1 and ${max}." >&2
    done
}

meshcore_configure_radio() {
    local cfg_sec="$1"       # e.g. "meshcore" or "meshcore1"
    local port="$2"          # e.g. /dev/meshcore0
    local cfg_ini="$3"       # path to config.ini
    local venv_py="$4"       # path to venv python3
    local project_dir="$5"   # project root (for docs/radio_settings/presets.toml)

    # ── Load presets ─────────────────────────────────────────
    local _presets_tmp
    _presets_tmp="$(mktemp)"
    "$venv_py" - "$project_dir/docs/radio_settings/presets.toml" "$_presets_tmp" <<'PYEOF'
import sys, tomllib
toml_path, out_path = sys.argv[1], sys.argv[2]
with open(toml_path, "rb") as fh:
    data = tomllib.load(fh)
presets = data["meshcore"]["presets"]
regions = " ".join(f'"{p["region"]}"' for p in presets)
freqs   = " ".join(str(p["freq_mhz"]) for p in presets)
bws     = " ".join(str(p["bw_khz"])   for p in presets)
sfs     = " ".join(str(p["sf"])       for p in presets)
crs     = " ".join(str(p["cr"])       for p in presets)
lines = [
    f"_MCC_COUNT={len(presets)}",
    f"_MCC_REGIONS=({regions})",
    f"_MCC_FREQS=({freqs})",
    f"_MCC_BWS=({bws})",
    f"_MCC_SFS=({sfs})",
    f"_MCC_CRS=({crs})",
]
with open(out_path, "w") as fh:
    fh.write("\n".join(lines) + "\n")
PYEOF
    # shellcheck source=/dev/null
    source "$_presets_tmp"
    rm -f "$_presets_tmp"

    # ── Legal notice ─────────────────────────────────────────
    echo ""
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
    echo "  ╚═════════════════════════════════════════════════════╝"
    echo ""
    printf "  I understand and accept responsibility (yes/no): "
    read -r _MCC_ACCEPT || true
    if [[ "${_MCC_ACCEPT,,}" != "yes" ]]; then
        echo "  Aborted. Review local radio regulations before proceeding."
        return 1
    fi
    echo ""

    # ── Region / frequency selection ─────────────────────────
    echo "  Select your region:"
    local _i
    for (( _i=0; _i<_MCC_COUNT; _i++ )); do
        printf "    %d) %-36s  %s MHz  BW=%s kHz  SF=%s  CR=4/%s\n" \
            $((_i+1)) \
            "${_MCC_REGIONS[$_i]}" \
            "${_MCC_FREQS[$_i]}" \
            "${_MCC_BWS[$_i]}" \
            "${_MCC_SFS[$_i]}" \
            "${_MCC_CRS[$_i]}"
    done
    printf "    %d) Manual entry\n" $(( _MCC_COUNT + 1 ))
    echo ""

    local _MCC_SEL
    _MCC_SEL=$(_mc_pick "Region" $(( _MCC_COUNT + 1 )))

    local MC_FREQ MC_BW MC_SF MC_CR
    if (( _MCC_SEL <= _MCC_COUNT )); then
        local _idx=$(( _MCC_SEL - 1 ))
        MESHCORE_REGION_LABEL="${_MCC_REGIONS[$_idx]}"
        MC_FREQ="${_MCC_FREQS[$_idx]}"
        MC_BW="${_MCC_BWS[$_idx]}"
        MC_SF="${_MCC_SFS[$_idx]}"
        MC_CR="${_MCC_CRS[$_idx]}"
    else
        printf "  Frequency in MHz (e.g. 910.525): "; read -r MC_FREQ || true
        printf "  Bandwidth in kHz (e.g. 62.5):    "; read -r MC_BW   || true
        printf "  Spreading factor (e.g. 7):       "; read -r MC_SF   || true
        printf "  Coding rate denominator (e.g. 5): "; read -r MC_CR  || true
        MESHCORE_REGION_LABEL="Custom"
    fi
    MESHCORE_FREQ_MHZ="$MC_FREQ"

    # ── Hop limit ─────────────────────────────────────────────
    echo ""
    echo "  ── Forwarding / repeater configuration ─────────────"
    echo "  The hop limit controls how many times a packet may be"
    echo "  relayed before it is dropped (max 64). Enter 0 to"
    echo "  disable forwarding entirely."
    echo ""
    printf "  Max hops [0-64, default 64]: "
    read -r _MCC_HOP || true
    local MC_REPEAT
    if [[ "$_MCC_HOP" =~ ^[0-9]+$ ]] && (( _MCC_HOP >= 0 && _MCC_HOP <= 64 )); then
        MC_REPEAT="$_MCC_HOP"
    else
        MC_REPEAT=64
        [[ -n "$_MCC_HOP" ]] && echo "  Invalid entry — using default of 64."
    fi

    if (( MC_REPEAT == 0 )); then
        MESHCORE_FORWARD_LABEL="disabled"
    else
        MESHCORE_FORWARD_LABEL="enabled (max ${MC_REPEAT} hops)"
    fi

    # ── Channels ──────────────────────────────────────────────
    echo ""
    echo "  ── Channels ─────────────────────────────────────────"
    echo "  Space or comma-separated list of channels to join."
    echo "  '#test' is the global MeshCore test channel."
    echo ""
    printf "  Channels [default #test]: "
    read -r _MCC_CHAN || true
    local MC_CHANNELS="${_MCC_CHAN:-#test}"

    # ── Summary ───────────────────────────────────────────────
    echo ""
    echo "  ── Selected radio configuration ─────────────────────"
    printf "  Region    : %s\n"       "$MESHCORE_REGION_LABEL"
    printf "  Frequency : %s MHz\n"   "$MC_FREQ"
    printf "  Bandwidth : %.0f kHz\n" "$MC_BW"
    printf "  SF        : %s\n"       "$MC_SF"
    printf "  CR        : 4/%s\n"     "$MC_CR"
    printf "  Forwarding: %s\n"       "$MESHCORE_FORWARD_LABEL"
    printf "  Channels  : %s\n"       "$MC_CHANNELS"
    echo "  ─────────────────────────────────────────────────────"
    echo ""

    # ── Write config.ini ─────────────────────────────────────
    # port may be a serial path (/dev/…) or a BLE address (xx:xx:xx or hex string)
    "$venv_py" - <<PYEOF
import configparser
cfg = configparser.ConfigParser()
cfg.read('${cfg_ini}')
sec = '${cfg_sec}'
if sec not in cfg:
    cfg.add_section(sec)
port = '${port}'
if port.startswith('/dev/'):
    cfg.set(sec, 'port',     port)
    cfg.set(sec, 'baudrate', '115200')
else:
    # BLE transport — write ble_address, remove stale port/baudrate
    cfg.set(sec, 'ble_address', port)
    cfg.remove_option(sec, 'port')
    cfg.remove_option(sec, 'baudrate')
cfg.set(sec, 'channels', '${MC_CHANNELS}')
with open('${cfg_ini}', 'w') as f:
    cfg.write(f)
print('  Written [${cfg_sec}] to config.ini')
PYEOF

    # ── Offer to program radio now (serial only) ──────────────
    local _DO_MC_PROG="no"
    if [[ "${port}" == /dev/* ]]; then
        printf "  Program these settings onto the radio now? (yes/no): "
        read -r _DO_MC_PROG || true
    else
        echo "  (Radio settings will be applied by NodeBot on first BLE connect.)"
    fi

    if [[ "${_DO_MC_PROG,,}" == "yes" ]]; then
        if systemctl is-active --quiet nodebot 2>/dev/null; then
            echo ""
            echo "  NodeBot is running and holds the serial port."
            printf "  Stop NodeBot to free the port? (yes/no): "
            read -r _MCC_STOP || true
            if [[ "${_MCC_STOP,,}" == "yes" ]]; then
                sudo systemctl stop nodebot
                echo "  NodeBot stopped."
                _MCC_RESTARTED_NODEBOT=true
            else
                echo "  Skipping programming — port busy."
                echo "  Settings saved; will be applied on NodeBot startup."
                _DO_MC_PROG="no"
            fi
        fi
    fi

    if [[ "${_DO_MC_PROG,,}" == "yes" ]]; then
        echo "  Programming radio..."
        local _MCC_SET_SCRIPT
        _MCC_SET_SCRIPT=$(cat <<'PYEOF'
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
        local _mc_result
        _mc_result=$("$venv_py" -c "$_MCC_SET_SCRIPT" \
            "$port" "115200" \
            "$MC_FREQ" "$MC_BW" "$MC_SF" "$MC_CR" "$MC_REPEAT" 2>/dev/null)

        if [[ "$_mc_result" == "OK" ]]; then
            echo "  Radio programmed successfully."
        else
            echo "  ⚠  Programming returned: $_mc_result"
            echo "     Settings saved to config.ini; will be applied on NodeBot startup."
        fi

        if [[ "${_MCC_RESTARTED_NODEBOT:-false}" == "true" ]]; then
            if systemctl is-enabled --quiet nodebot 2>/dev/null; then
                echo ""
                echo "  Restarting NodeBot..."
                sudo systemctl start nodebot
            fi
        fi
    else
        echo "  Settings will be applied by NodeBot on first startup."
        echo "  You can also program them with the MeshCore companion app."
    fi
}
