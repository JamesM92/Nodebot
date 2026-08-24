#!/bin/bash
# ============================================================
# NodeBot rnsh Installer
#
# Installs and enables the rnsh (shell over Reticulum) listener.
# After running this script you can connect to the Pi remotely
# via any Reticulum interface — LoRa radio or internet TCP hub —
# with no port forwarding or public IP required.
#
# Two access modes are configured:
#
#   1) Direct rnsh shell
#      rnsh <destination_hash>
#      Gives a bash shell on the Pi from your laptop.
#      Install rnsh on laptop: pip3 install rnsh
#
#   2) VS Code Remote-SSH over Reticulum (recommended)
#      rnsh acts as an SSH ProxyCommand tunnelling traffic
#      to the Pi's local sshd. Full VS Code IDE + Claude Code.
#      See the SSH config snippet printed at the end.
#
# Prerequisites:
#   - install_nodebot.sh already run
#   - install_lxmf.sh already run (NomadNet/RNS instance running)
#   - SSH server installed on this Pi (openssh-server)
#
# Run as: bash installer/install_rnsh.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_FILE="$SCRIPT_DIR/rnsh.service"
SERVICE_NAME="rnsh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗${NC}  $*" >&2; exit 1; }
info() { echo -e "${CYAN}→${NC} $*"; }

echo ""
echo "================================================"
echo "  NodeBot rnsh Installer"
echo "================================================"
echo ""

# ── Step 1: Check rnsh is installed ──────────────────────────────────────────
echo "[1/4] Checking rnsh installation..."
if ! command -v rnsh &>/dev/null; then
    info "Installing rnsh..."
    pip3 install --user --break-system-packages rnsh
fi
RNSH_BIN="$(command -v rnsh)"
ok "rnsh found at $RNSH_BIN"

# ── Step 2: Check openssh-server ─────────────────────────────────────────────
echo ""
echo "[2/4] Checking SSH server..."
if ! systemctl is-active --quiet ssh 2>/dev/null && ! systemctl is-active --quiet sshd 2>/dev/null; then
    warn "openssh-server does not appear to be running."
    echo "  Install with: sudo apt install openssh-server"
    echo "  rnsh will still work as a direct shell, but VS Code Remote-SSH"
    echo "  over Reticulum requires sshd."
    echo ""
else
    ok "SSH server is running"
fi

# ── Step 3: Install systemd service ──────────────────────────────────────────
echo ""
echo "[3/4] Installing rnsh systemd service..."

# Patch ExecStart path in case rnsh is not in the expected location
PATCHED_SERVICE="$(mktemp)"
sed "s|/home/penguin/.local/bin/rnsh|$RNSH_BIN|g" "$SERVICE_FILE" > "$PATCHED_SERVICE"

sudo cp "$PATCHED_SERVICE" "/etc/systemd/system/${SERVICE_NAME}.service"
rm -f "$PATCHED_SERVICE"

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "rnsh service running"
else
    warn "Service may still be starting. Check: journalctl -u rnsh -n 20"
fi

# ── Step 4: Print destination hash ───────────────────────────────────────────
echo ""
echo "[4/4] Reading rnsh destination hash..."

HASH_OUTPUT=$(rnsh -l -s nodebot -p 2>&1)
DEST_HASH=$(echo "$HASH_OUTPUT" | grep -oP '(?<=Listening on : <)[^>]+' || true)
IDENTITY_HASH=$(echo "$HASH_OUTPUT" | grep -oP '(?<=Identity     : <)[^>]+' || true)

echo ""
echo "================================================"
echo -e "  ${GREEN}rnsh installation complete${NC}"
echo "================================================"
echo ""
printf "  Identity hash   : %s\n" "${IDENTITY_HASH:-run 'rnsh -l -s nodebot -p' to check}"
printf "  Destination hash: %s\n" "${DEST_HASH:-run 'rnsh -l -s nodebot -p' to check}"
echo ""
echo "  ── Add to ~/.ssh/config on your LAPTOP: ─────────────────────────────"
echo ""
echo "  Host pi-reticulum"
echo "      HostName pi-reticulum"
echo "      User penguin"
echo "      IdentityFile ~/.ssh/id_ed25519"
echo "      ProxyCommand rnsh -i ~/.reticulum/identity ${DEST_HASH} -- nc 127.0.0.1 22"
echo ""
echo "  ── Then connect with: ───────────────────────────────────────────────"
echo ""
echo "  ssh pi-reticulum                  # standard SSH shell"
echo "  code --remote ssh-remote+pi-reticulum /home/penguin/github.com/JamesM92/NodeBot"
echo "  # VS Code Remote-SSH: 'Connect to Host' → pi-reticulum"
echo ""
echo "  ── Direct rnsh shell (no SSH needed): ──────────────────────────────"
echo ""
printf "  rnsh %s\n" "${DEST_HASH}"
echo ""
echo "  ── Logs: ────────────────────────────────────────────────────────────"
echo "  journalctl -u rnsh -f"
echo ""
