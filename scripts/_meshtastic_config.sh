#!/bin/bash
# ============================================================
# Shared Meshtastic radio configuration library
#
# Source this file; then call:
#   meshtastic_configure_radio  <config_section> <port> <config_ini> <venv_python>
#
# Prompts for region, preset, hop limit, TX power, and offers
# to program the radio immediately via the Meshtastic library.
# Writes the chosen settings into <config_section> of <config_ini>.
# ============================================================

# ── Helper: validated numeric pick ───────────────────────────
_mesh_pick() {
    local prompt="$1" max="$2" choice
    while true; do
        printf "  %s [1-%d]: " "$prompt" "$max" >&2
        read -r choice || true
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= max )); then
            echo "$choice"
            return
        fi
        echo "  Please enter a number between 1 and ${max}." >&2
    done
}

meshtastic_configure_radio() {
    local cfg_sec="$1"   # e.g. "meshtastic" or "meshtastic1"
    local port="$2"      # e.g. /dev/meshtastic1
    local cfg_ini="$3"   # path to config.ini
    local venv_py="$4"   # path to venv python3

    # ── Legal notice ─────────────────────────────────────────
    echo ""
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
    read -r _ACCEPT || true
    if [[ "${_ACCEPT,,}" != "yes" ]]; then
        echo "  Aborted. Review local radio regulations before proceeding."
        return 1
    fi
    echo ""

    # ── Region ───────────────────────────────────────────────
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

    local _rsel; _rsel=$(_mesh_pick "Region" 26)
    case "$_rsel" in
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

    [[ "$RADIO_REGION" == "EU_433" || "$RADIO_REGION" == "EU_868" ]] && \
        echo "  ⚠  ${RADIO_REGION}: 10% hourly duty cycle."
    [[ "$RADIO_REGION" == "UA_868" ]] && \
        echo "  ⚠  UA_868: 1% duty cycle — very restricted transmit time."

    # ── Modem preset ─────────────────────────────────────────
    echo ""
    echo "  ── Modem preset ─────────────────────────────────────"
    echo "  Presets trade off speed vs range."
    echo ""
    echo "    1) SHORT_TURBO    Fastest. Not legal everywhere (500 kHz BW)."
    echo "    2) SHORT_FAST"
    echo "    3) SHORT_SLOW"
    echo "    4) MEDIUM_FAST"
    echo "    5) MEDIUM_SLOW"
    echo "    6) LONG_FAST      Default. Good balance of speed and range."
    echo "    7) LONG_MODERATE"
    echo "    8) LONG_SLOW"
    echo "    9) VERY_LONG_SLOW Slowest / max range."
    echo ""

    local _psel; _psel=$(_mesh_pick "Modem preset" 9)
    case "$_psel" in
        1) RADIO_PRESET="SHORT_TURBO"    ;;  2) RADIO_PRESET="SHORT_FAST"     ;;
        3) RADIO_PRESET="SHORT_SLOW"     ;;  4) RADIO_PRESET="MEDIUM_FAST"    ;;
        5) RADIO_PRESET="MEDIUM_SLOW"    ;;  6) RADIO_PRESET="LONG_FAST"      ;;
        7) RADIO_PRESET="LONG_MODERATE"  ;;  8) RADIO_PRESET="LONG_SLOW"      ;;
        9) RADIO_PRESET="VERY_LONG_SLOW" ;;
    esac

    # ── Hop limit ─────────────────────────────────────────────
    echo ""
    echo "  ── Hop limit ────────────────────────────────────────"
    echo "  Maximum hops a packet may take through the mesh (0–7)."
    echo "  Default is 7."
    echo ""
    printf "  Max hops [0-7, default 7]: "
    read -r _hop_in || true
    if [[ "$_hop_in" =~ ^[0-7]$ ]]; then
        RADIO_HOPS="$_hop_in"
    else
        RADIO_HOPS=7
        [[ -n "$_hop_in" ]] && echo "  Invalid — using default of 7."
    fi

    # ── TX power ──────────────────────────────────────────────
    echo ""
    echo "  ── Transmit power ───────────────────────────────────"

    local REGION_MAX_DBM
    case "$RADIO_REGION" in
        US)      REGION_MAX_DBM="30 dBm" ;; EU_433)  REGION_MAX_DBM="12 dBm" ;;
        EU_868)  REGION_MAX_DBM="27 dBm" ;; ANZ)     REGION_MAX_DBM="30 dBm" ;;
        ANZ_433) REGION_MAX_DBM="14 dBm" ;; CN)      REGION_MAX_DBM="19 dBm" ;;
        JP)      REGION_MAX_DBM="16 dBm" ;; KR)      REGION_MAX_DBM="—"      ;;
        TW)      REGION_MAX_DBM="27 dBm" ;; RU)      REGION_MAX_DBM="20 dBm" ;;
        IN)      REGION_MAX_DBM="30 dBm" ;; NZ_865)  REGION_MAX_DBM="36 dBm" ;;
        TH)      REGION_MAX_DBM="16 dBm" ;; UA_433)  REGION_MAX_DBM="10 dBm" ;;
        UA_868)  REGION_MAX_DBM="14 dBm" ;; MY_433)  REGION_MAX_DBM="20 dBm" ;;
        MY_919)  REGION_MAX_DBM="27 dBm" ;; SG_923)  REGION_MAX_DBM="20 dBm" ;;
        KZ_433)  REGION_MAX_DBM="10 dBm" ;; KZ_863)  REGION_MAX_DBM="30 dBm" ;;
        BR_902)  REGION_MAX_DBM="30 dBm" ;; PH_433)  REGION_MAX_DBM="10 dBm" ;;
        PH_868)  REGION_MAX_DBM="14 dBm" ;; PH_915)  REGION_MAX_DBM="24 dBm" ;;
        NP_865)  REGION_MAX_DBM="—"      ;; LORA_24) REGION_MAX_DBM="10 dBm" ;;
        *)       REGION_MAX_DBM="—"      ;;
    esac

    echo "  0 = use the maximum legal power for your region (recommended)."
    if [[ "$REGION_MAX_DBM" == "—" ]]; then
        echo "  Max legal for ${RADIO_REGION}: not specified — firmware applies its own cap."
    else
        echo "  Max legal for ${RADIO_REGION}: ${REGION_MAX_DBM}."
    fi
    echo ""
    printf "  TX power in dBm [default 0 = max legal]: "
    read -r _pwr_in || true
    if [[ "$_pwr_in" =~ ^[0-9]+$ ]]; then
        RADIO_POWER="$_pwr_in"
    else
        RADIO_POWER=0
        [[ -n "$_pwr_in" ]] && echo "  Invalid — using 0 (max legal)."
    fi

    # ── Summary ───────────────────────────────────────────────
    echo ""
    echo "  ── Selected radio configuration ─────────────────────"
    printf "  Region    : %s\n" "$RADIO_REGION"
    printf "  Preset    : %s\n" "$RADIO_PRESET"
    printf "  Max hops  : %s\n" "$RADIO_HOPS"
    if (( RADIO_POWER == 0 )); then
        if [[ "$REGION_MAX_DBM" == "—" ]]; then
            printf "  TX power  : max legal (cap not specified for %s)\n" "$RADIO_REGION"
        else
            printf "  TX power  : max legal (%s for %s)\n" "$REGION_MAX_DBM" "$RADIO_REGION"
        fi
    else
        printf "  TX power  : %s dBm\n" "$RADIO_POWER"
    fi
    echo "  ─────────────────────────────────────────────────────"
    echo ""

    # ── Write config.ini ──────────────────────────────────────
    python3 - <<PYEOF
import configparser
cfg = configparser.ConfigParser()
cfg.read('${cfg_ini}')
sec = '${cfg_sec}'
if sec not in cfg:
    cfg.add_section(sec)
cfg.set(sec, 'port',         '${port}')
cfg.set(sec, 'baudrate',     '115200')
cfg.set(sec, 'region',       '${RADIO_REGION}')
cfg.set(sec, 'modem_preset', '${RADIO_PRESET}')
cfg.set(sec, 'hop_limit',    '${RADIO_HOPS}')
cfg.set(sec, 'tx_power',     '${RADIO_POWER}')
with open('${cfg_ini}', 'w') as f:
    cfg.write(f)
print('  Written [${cfg_sec}] to config.ini')
PYEOF

    # ── Offer to program radio now ────────────────────────────
    printf "  Program these settings onto the radio now? (yes/no): "
    read -r _DO_PROG || true

    if [[ "${_DO_PROG,,}" == "yes" ]]; then
        if systemctl is-active --quiet nodebot 2>/dev/null; then
            echo ""
            echo "  NodeBot is running and holds the serial port."
            printf "  Stop NodeBot to free the port? (yes/no): "
            read -r _STOP_BOT || true
            if [[ "${_STOP_BOT,,}" == "yes" ]]; then
                sudo systemctl stop nodebot
                echo "  NodeBot stopped."
                _MESH_RESTARTED_NODEBOT=true
            else
                echo "  Skipping programming — port busy."
                echo "  Settings saved; NodeBot will apply them on next startup."
                _DO_PROG="no"
            fi
        fi
    fi

    if [[ "${_DO_PROG,,}" == "yes" ]]; then
        echo ""
        echo "  Note: writing LoRa config causes the radio to reboot (~30 seconds)."
        echo "  Programming radio..."

        local _PROG_SCRIPT
        _PROG_SCRIPT=$(cat <<'PYEOF'
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
threading.Thread(target=configure, daemon=True).start()
done.wait(timeout=30)
print(result["v"])
PYEOF
)
        local _prog_result
        _prog_result=$("$venv_py" -c "$_PROG_SCRIPT" \
            "$port" "$RADIO_REGION" "$RADIO_PRESET" "$RADIO_HOPS" "$RADIO_POWER" 2>/dev/null)

        if [[ "$_prog_result" == "OK" ]]; then
            echo "  Radio programmed successfully."
            echo "  The radio will reboot to apply settings (~30 seconds)."
        else
            echo "  ⚠  Programming returned: $_prog_result"
            echo "     Settings saved to config.ini; will be applied on NodeBot startup."
        fi

        if [[ "${_MESH_RESTARTED_NODEBOT:-false}" == "true" ]]; then
            if systemctl is-enabled --quiet nodebot 2>/dev/null; then
                echo ""
                echo "  Restarting NodeBot..."
                sudo systemctl start nodebot
            fi
        fi
    else
        echo "  Settings will be applied by NodeBot on first startup."
    fi
}
