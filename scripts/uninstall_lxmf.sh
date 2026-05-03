#!/bin/bash
# ============================================================
# NodeBot LXMF Uninstaller
#
# Reverses what install_lxmf.sh did:
#   - Stops and disables nomadnet.service and nodebot.service
#   - Removes both service files
#   - Removes the rNode udev rules (/etc/udev/rules.d/99-rnode.rules)
#   - Optionally removes the Reticulum config (~/.reticulum/)
#   - Optionally removes the LXMF identity and message store (~/.nodebot/)
#   - Optionally uninstalls NomadNet (pip3 --user)
#   - Comments out [rns] in NodeBot's config.ini
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_INI="$PROJECT_DIR/config.ini"

SERVICE_USER="$(whoami)"
NOMADNET_SERVICE="/etc/systemd/system/nomadnet.service"
NODEBOT_SERVICE="/etc/systemd/system/nodebot.service"
UDEV_RULES="/etc/udev/rules.d/99-rnode.rules"
RNS_CONFIG_DIR="$HOME/.reticulum"
NODEBOT_STORAGE="$HOME/.nodebot"
USER_BIN="$(python3 -m site --user-base)/bin"

echo ""
echo "================================================"
echo "  NodeBot LXMF Uninstaller"
echo "================================================"
echo "  NodeBot : $PROJECT_DIR"
echo "  User    : $SERVICE_USER"
echo "================================================"
echo ""

# ── Step 1: Stop and disable services ────────────────────
echo "[1/6] Stopping and disabling services..."
echo ""

for svc in nodebot nomadnet; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        sudo systemctl stop "$svc"
        echo "  Stopped $svc."
    else
        echo "  $svc is not running."
    fi
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        sudo systemctl disable "$svc"
        echo "  Disabled $svc."
    fi
done
echo ""

# ── Step 2: Remove service files ─────────────────────────
echo "[2/6] Removing service files..."
echo ""

for svc_file in "$NOMADNET_SERVICE" "$NODEBOT_SERVICE"; do
    if [ -f "$svc_file" ]; then
        sudo rm "$svc_file"
        echo "  Removed: $svc_file"
    else
        echo "  Not found (already removed): $svc_file"
    fi
done

sudo systemctl daemon-reload
echo ""

# ── Step 3: Remove udev rules ────────────────────────────
echo "[3/6] Removing rNode udev rules..."
echo ""

if [ -f "$UDEV_RULES" ]; then
    sudo rm "$UDEV_RULES"
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo "  Removed: $UDEV_RULES"
    echo "  udev rules reloaded — /dev/rnode* symlinks will no longer be created."
else
    echo "  Not found (already removed): $UDEV_RULES"
fi
echo ""

# ── Step 4: Reticulum config ─────────────────────────────
echo "[4/6] Reticulum configuration..."
echo ""

if [ -d "$RNS_CONFIG_DIR" ]; then
    echo "  Found: $RNS_CONFIG_DIR"
    echo "  This contains your Reticulum network config and interface settings."
    echo ""
    printf "  Delete Reticulum config directory? (yes/no): "
    read -r DEL_RNS || true
    if [[ "${DEL_RNS,,}" == "yes" ]]; then
        rm -rf "$RNS_CONFIG_DIR"
        echo "  Deleted: $RNS_CONFIG_DIR"
    else
        echo "  Kept: $RNS_CONFIG_DIR"
    fi
else
    echo "  Not found — nothing to remove."
fi
echo ""

# ── Step 5: LXMF identity and message store ──────────────
echo "[5/6] LXMF identity and message store..."
echo ""

if [ -d "$NODEBOT_STORAGE" ]; then
    echo "  Found: $NODEBOT_STORAGE"
    echo "  This contains NodeBot's LXMF identity (address), message queue,"
    echo "  relay state, channel logs, and DM logs."
    echo "  Deleting it is irreversible — NodeBot gets a new LXMF address on reinstall."
    echo ""
    printf "  Delete NodeBot storage directory? (yes/no): "
    read -r DEL_STORE || true
    if [[ "${DEL_STORE,,}" == "yes" ]]; then
        rm -rf "$NODEBOT_STORAGE"
        echo "  Deleted: $NODEBOT_STORAGE"
    else
        echo "  Kept: $NODEBOT_STORAGE"
    fi
else
    echo "  Not found — nothing to remove."
fi
echo ""

# ── Step 6: Uninstall NomadNet ───────────────────────────
echo "[6/6] NomadNet..."
echo ""

if [ -f "$USER_BIN/nomadnet" ]; then
    echo "  NomadNet is installed at $USER_BIN/nomadnet"
    printf "  Uninstall NomadNet (pip3 --user)? (yes/no): "
    read -r DEL_NN || true
    if [[ "${DEL_NN,,}" == "yes" ]]; then
        # Try normal uninstall; fall back for PEP 668 systems
        if ! pip3 uninstall --user -y nomadnet rns lxmf 2>/dev/null; then
            pip3 uninstall --user --break-system-packages -y nomadnet rns lxmf 2>/dev/null || true
        fi
        echo "  NomadNet, RNS, and LXMF packages removed."
    else
        echo "  Kept NomadNet."
    fi
else
    echo "  NomadNet not found at $USER_BIN — nothing to remove."
fi
echo ""

# ── Comment out [rns] in config.ini ──────────────────────
echo "Updating NodeBot config.ini..."
echo ""

if [ -f "$CONFIG_INI" ]; then
    if grep -q "^\[rns\]" "$CONFIG_INI"; then
        python3 - "$CONFIG_INI" <<'PYEOF'
import sys, re
path = sys.argv[1]
with open(path) as f:
    content = f.read()
def comment_section(text, section):
    lines = text.splitlines(keepends=True)
    out = []
    in_section = False
    for line in lines:
        if re.match(r'^\[', line):
            in_section = line.strip() == f'[{section}]'
        if in_section and not line.startswith('#') and line.strip():
            out.append('# ' + line)
        else:
            out.append(line)
    return ''.join(out)
content = comment_section(content, 'rns')
with open(path, 'w') as f:
    f.write(content)
print("  Commented out [rns] section in config.ini")
PYEOF
    else
        echo "  No active [rns] section found — nothing to change."
    fi
else
    echo "  config.ini not found — skipping."
fi
echo ""

echo "================================================"
echo "  LXMF uninstall complete."
echo "================================================"
echo ""
echo "  Removed:"
echo "    nomadnet.service, nodebot.service"
echo "    rNode udev rules (99-rnode.rules)"
echo ""
echo "  NodeBot's other transports (Meshtastic, MeshCore,"
echo "  BitChatPi) are unaffected but nodebot.service has"
echo "  been removed. To run NodeBot again, reinstall a"
echo "  transport or start it manually:"
echo "    cd $PROJECT_DIR/src && ../.venv/bin/python3 runbot.py"
echo ""
echo "  To reinstall LXMF:"
echo "    bash installer/install_lxmf.sh"
echo ""
