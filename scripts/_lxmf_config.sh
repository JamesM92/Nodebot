#!/bin/bash
# ============================================================
# Shared lxmf/rNode radio configuration library
#
# Source this file; then call:
#   lxmf_configure_rnode <dev_path> <rns_config> <venv_py> <project_dir>
#
# Prompts for region / frequency / TX power, then appends a new
# [[RNodeInterfaceN]] block to the Reticulum config file.
# RNS programs the radio at runtime — no rnodeconf call here.
# ============================================================

_lxmf_pick() {
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

lxmf_configure_rnode() {
    local dev_path="$1"      # e.g. /dev/rnode1
    local rns_config="$2"    # ~/.reticulum/config
    local venv_py="$3"       # path to venv python3
    local project_dir="$4"   # project root (for docs/radio_settings/presets.toml)

    # ── Load presets ─────────────────────────────────────────
    local _presets_tmp
    _presets_tmp="$(mktemp)"
    "$venv_py" - "$project_dir/docs/radio_settings/presets.toml" "$_presets_tmp" <<'PYEOF'
import sys, tomllib
toml_path, out_path = sys.argv[1], sys.argv[2]
with open(toml_path, "rb") as fh:
    data = tomllib.load(fh)
presets = data["lxmf"]["presets"]
region_names = [p["region"] for p in presets] + ["Manual entry (custom values)"]
lines = []
quoted_names = " ".join(f'"{n}"' for n in region_names)
lines.append(f"_LXF_REGION_NAMES=({quoted_names})")
for idx, preset in enumerate(presets, start=1):
    entries = preset["nodes"]
    quoted_entries = " ".join(
        f'"{e["freq_hz"]}|{e["bw_hz"]}|{e["sf"]}|{e["description"]}"'
        for e in entries
    )
    lines.append(f"_LXF_SETTINGS_{idx}=({quoted_entries})")
with open(out_path, "w", encoding="utf-8") as fh:
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
    echo "  ╚═════════════════════════════════════════════════════╝"
    echo ""
    printf "  I understand and accept responsibility (yes/no): "
    read -r _LXF_ACCEPT || true
    if [[ "${_LXF_ACCEPT,,}" != "yes" ]]; then
        echo "  Aborted. Review local radio regulations before proceeding."
        return 1
    fi
    echo ""

    # ── Region / frequency selection ─────────────────────────
    echo "  ── rNode frequency ──────────────────────────────────"
    echo ""
    echo "  Select your region:"
    local _i
    for _i in "${!_LXF_REGION_NAMES[@]}"; do
        printf "    %2d) %s\n" $((_i+1)) "${_LXF_REGION_NAMES[$_i]}"
    done
    echo ""

    local _LXF_REGION_IDX
    _LXF_REGION_IDX=$(_lxmf_pick "Region" "${#_LXF_REGION_NAMES[@]}")

    local LXF_FREQ LXF_BW LXF_SF LXF_LOCATION
    if (( _LXF_REGION_IDX == ${#_LXF_REGION_NAMES[@]} )); then
        printf "  Frequency (Hz, e.g. 915000000): "; read -r LXF_FREQ || true
        printf "  Bandwidth (Hz, e.g. 125000):    "; read -r LXF_BW   || true
        printf "  Spreading factor (e.g. 8):      "; read -r LXF_SF   || true
        LXF_LOCATION="Custom"
    else
        local _arr_name="_LXF_SETTINGS_${_LXF_REGION_IDX}[@]"
        local _region_settings=("${!_arr_name}")
        local _count="${#_region_settings[@]}"

        if (( _count == 1 )); then
            IFS='|' read -r LXF_FREQ LXF_BW LXF_SF LXF_LOCATION <<< "${_region_settings[0]}"
        else
            local _region_label="${_LXF_REGION_NAMES[$((_LXF_REGION_IDX-1))]}"
            echo ""
            echo "  Available settings for ${_region_label}:"
            for _i in "${!_region_settings[@]}"; do
                IFS='|' read -r _f _b _s _d <<< "${_region_settings[$_i]}"
                printf "    %d) freq=%-12s bw=%-8s sf=%-3s  %s\n" \
                    $((_i+1)) "$_f" "$_b" "$_s" "$_d"
            done
            echo ""
            local _SETTING_IDX
            _SETTING_IDX=$(_lxmf_pick "Setting" "$_count")
            IFS='|' read -r LXF_FREQ LXF_BW LXF_SF LXF_LOCATION \
                <<< "${_region_settings[$((_SETTING_IDX-1))]}"
        fi
    fi

    # ── TX power ──────────────────────────────────────────────
    echo ""
    printf "  TX power in dBm [default 17, max 22 for most rNodes]: "
    read -r _LXF_PWR || true
    local LXF_TXPOWER="${_LXF_PWR:-17}"
    [[ "$LXF_TXPOWER" =~ ^[0-9]+$ ]] || { echo "  Invalid — using 17."; LXF_TXPOWER=17; }

    # ── Summary ───────────────────────────────────────────────
    echo ""
    echo "  ── Selected rNode configuration ─────────────────────"
    printf "  Device      : %s\n" "$dev_path"
    printf "  Frequency   : %s Hz\n" "$LXF_FREQ"
    printf "  Bandwidth   : %s Hz\n" "$LXF_BW"
    printf "  Spreading   : SF%s\n"  "$LXF_SF"
    printf "  TX Power    : %s dBm\n" "$LXF_TXPOWER"
    printf "  Reference   : %s\n" "$LXF_LOCATION"
    echo "  ─────────────────────────────────────────────────────"
    echo ""

    # ── Determine interface name ──────────────────────────────
    # Count existing RNodeInterface entries in the reticulum config.
    local _iface_name="RNodeInterface"
    if [[ -f "$rns_config" ]]; then
        local _n_existing
        _n_existing=$(grep -c '^\s*\[\[RNodeInterface' "$rns_config" 2>/dev/null || true)
        if (( _n_existing > 0 )); then
            _iface_name="RNodeInterface${_n_existing}"
        fi
    fi

    # ── Write / append interface to reticulum config ──────────
    printf "  Add this interface to %s? (yes/no): " "$rns_config"
    read -r _LXF_WRITE || true
    if [[ "${_LXF_WRITE,,}" != "yes" ]]; then
        echo "  Skipping reticulum config update."
        echo "  Add the following block manually to $rns_config under [interfaces]:"
        echo ""
        echo "    [[$_iface_name]]"
        echo "      type = RNodeInterface"
        echo "      interface_enabled = True"
        echo "      outgoing = True"
        echo "      port = $dev_path"
        echo "      frequency = $LXF_FREQ"
        echo "      bandwidth = $LXF_BW"
        echo "      spreadingfactor = $LXF_SF"
        echo "      txpower = $LXF_TXPOWER"
        echo "      codingrate = 5"
        return 0
    fi

    if [[ ! -f "$rns_config" ]]; then
        # Create a minimal reticulum config if none exists
        mkdir -p "$(dirname "$rns_config")"
        cat > "$rns_config" <<RNSEOF
# Reticulum configuration — written by NodeBot add_device
[reticulum]
  enable_transport = False
  share_instance = Yes
  shared_instance_port = 37428
  instance_control_port = 37429
  panic_on_interface_error = No

[logging]
  loglevel = 4

[interfaces]
RNSEOF
        echo "  Created new reticulum config: $rns_config"
    fi

    cat >> "$rns_config" <<RNODEEOF

  [[$_iface_name]]
    type = RNodeInterface
    interface_enabled = True
    outgoing = True
    port = $dev_path
    frequency = $LXF_FREQ
    bandwidth = $LXF_BW
    spreadingfactor = $LXF_SF
    txpower = $LXF_TXPOWER
    codingrate = 5
RNODEEOF

    echo "  Appended [[$_iface_name]] to $rns_config"
    echo "  Restart the nomadnet / RNS service to activate this interface."
}
