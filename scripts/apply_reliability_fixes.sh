#!/bin/bash
# One-time script to apply radio reliability fixes.
# Run as: sudo bash scripts/apply_reliability_fixes.sh

set -e

echo "[1/3] Writing USB power + udev restart rules..."
cat > /etc/udev/rules.d/99-nodebot-power.rules << 'EOF'
# Disable USB autosuspend for NodeBot radio devices
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="0000", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", ATTR{power/control}="on"

# Restart NodeBot immediately when a radio device reconnects
ACTION=="add", SUBSYSTEM=="tty", ENV{ID_SERIAL}=="Espressif_Systems_heltec_wifi_lora_32_v4__16_MB_FLASH__2_MB_PSRAM__8CFD49B596D8", RUN+="/bin/systemctl --no-block restart nodebot"
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", RUN+="/bin/systemctl --no-block restart nodebot"
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="0000", RUN+="/bin/systemctl --no-block restart nodebot"
EOF
udevadm control --reload-rules
echo "    udev rules written and reloaded."

echo "[2/3] Patching nodebot.service (StartLimitIntervalSec=0)..."
SERVICE=/etc/systemd/system/nodebot.service
if grep -q "StartLimitIntervalSec" "$SERVICE"; then
    echo "    Already present, skipping."
else
    sed -i '/^\[Service\]/a StartLimitIntervalSec=0' "$SERVICE"
    systemctl daemon-reload
    echo "    Service patched and reloaded."
fi

echo "[3/3] Applying power/control=on to currently connected devices..."
for path in /sys/bus/usb/devices/*/; do
    vid=$(cat "$path/idVendor" 2>/dev/null)
    pid=$(cat "$path/idProduct" 2>/dev/null)
    case "$vid:$pid" in
        10c4:ea60|10c4:0000|303a:1001)
            echo "on" > "$path/power/control" 2>/dev/null && \
                echo "    $vid:$pid — power/control set to on" || \
                echo "    $vid:$pid — could not set (non-fatal)"
            ;;
    esac
done

echo ""
echo "Done. Changes take full effect on next device reconnect or reboot."
