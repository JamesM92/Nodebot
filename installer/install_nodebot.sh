#!/bin/bash
# ============================================================
# NodeBot Base Installer
#
# Sets up NodeBot and its core dependencies on a fresh
# Raspberry Pi. Installs the default nodebot.service with no
# protocol-specific dependencies.
#
# To add NomadNet / LXMF / rNode support run afterwards:
#   bash installer/install_lxmf.sh
#
# Usage (from the project root or anywhere):
#   bash installer/install_nodebot.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/.venv"
VENV_PYTHON="$VENV/bin/python3"
SERVICE_USER="$(whoami)"

echo ""
echo "================================================"
echo "  NodeBot Installer"
echo "================================================"
echo "  Project : $PROJECT_DIR"
echo "  Venv    : $VENV"
echo "  User    : $SERVICE_USER"
echo "================================================"
echo ""

# ── Step 1: System packages ───────────────────────────────────
echo "[1/5] Checking system packages..."
MISSING=()
command -v python3 >/dev/null 2>&1 || MISSING+=("python3")
command -v pip3    >/dev/null 2>&1 || MISSING+=("python3-pip")

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  Installing: ${MISSING[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${MISSING[@]}"
else
    echo "  python3 and pip3 already present."
fi

# netcat is used by install_lxmf.sh's wait script — install now so it is
# available if the user runs the LXMF installer later.
if ! command -v nc >/dev/null 2>&1; then
    echo "  Installing netcat-openbsd (needed by install_lxmf.sh)..."
    sudo apt-get install -y netcat-openbsd
fi

# ── Step 2: Virtual environment ───────────────────────────────
echo "[2/5] Setting up virtual environment..."

if [ ! -d "$VENV" ]; then
    if command -v uv >/dev/null 2>&1; then
        echo "  Using uv..."
        cd "$PROJECT_DIR" && uv sync
    else
        echo "  Using python3 -m venv..."
        python3 -m venv "$VENV"
    fi
else
    echo "  Venv already exists, skipping creation."
fi

# Fix execute permissions — the venv can lose them after git clone or copy.
find "$VENV/bin" -type f ! -perm /111 -exec chmod +x {} \;
echo "  Permissions fixed."

# ── Step 3: Python dependencies ───────────────────────────────
echo "[3/5] Installing Python dependencies..."

if command -v uv >/dev/null 2>&1 && [ -f "$PROJECT_DIR/uv.lock" ]; then
    echo "  Using uv sync..."
    cd "$PROJECT_DIR" && uv sync
else
    echo "  Using pip install..."
    "$VENV/bin/pip3" install --upgrade pip --quiet
    "$VENV/bin/pip3" install -r "$PROJECT_DIR/requirements.txt"
fi

echo "  Dependencies installed."

# ── Step 4: Storage directory ─────────────────────────────────
echo "[4/5] Creating storage directory..."
mkdir -p "$HOME/.nodebot/lxmf_storage"
echo "  $HOME/.nodebot/lxmf_storage"

# ── Step 5: Install nodebot.service ──────────────────────────
echo "[5/5] Installing nodebot.service..."

sudo tee /etc/systemd/system/nodebot.service > /dev/null <<EOF
[Unit]
Description=NodeBot Multi-Protocol Mesh Relay System
Documentation=https://github.com/JamesM92/NodeBot
After=network.target
Wants=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR/src
ExecStart=$VENV_PYTHON $PROJECT_DIR/src/runbot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nodebot.service
echo "  Written and enabled: /etc/systemd/system/nodebot.service"

echo ""
echo "================================================"
echo "  Installation complete."
echo "================================================"
echo ""
echo "  Start NodeBot:"
echo "    sudo systemctl start nodebot"
echo ""
echo "  View logs:"
echo "    journalctl -u nodebot -f"
echo ""
echo "  To add NomadNet / LXMF / rNode support:"
echo "    bash installer/install_lxmf.sh"
echo ""
