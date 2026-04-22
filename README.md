# NodeBot

A multi-protocol mesh network chatbot for Raspberry Pi.

NodeBot runs as a systemd daemon and responds to commands sent over mesh radio networks. It supports multiple protocols through a pluggable transport system, so the same plugin and command logic works across all of them.

Supported transports:
- **LXMF** over Reticulum (LoRa rNode hardware via NomadNet)
- **Meshtastic** (LoRa mesh radios)
- **MeshCore** (API-based mesh)

---

## Requirements

- Raspberry Pi (any model with USB)
- Python 3.13+
- Internet connection for initial package installation

For LXMF/LoRa: an rNode-compatible LoRa device (e.g. Heltec LoRa32 v3) flashed with [rNode firmware](https://unsigned.io/rnode/)

---

## Installation

### Step 1 — Clone the repository

```bash
git clone https://github.com/JamesM92/NodeBot.git
cd NodeBot
```

### Step 2 — Run the base installer

```bash
bash installer/install_nodebot.sh
```

This will:
- Install Python 3 and pip if missing
- Create a virtual environment (`.venv/`) using `uv` or `python3 -m venv`
- Install Python dependencies from `requirements.txt`
- Create the storage directory at `~/.nodebot/lxmf_storage`
- Write and enable `nodebot.service` in systemd

### Step 3 — Configure

```bash
cp config.example config.ini
nano config.ini
```

Key settings:

| Setting | Description |
|---------|-------------|
| `[bot] name` | Display name shown on the mesh network |
| `[bot] storage_path` | Where the LXMF identity and message queue are stored |
| `[admin] addresses` | LXMF addresses with permanent admin access |
| `[admin] password` | Admin login password (plain text, hashed on load) |
| `[plugins] timeout_sec` | Max seconds a plugin may run before being killed |

### Step 4 — Start

```bash
sudo systemctl start nodebot
journalctl -u nodebot -f
```

NodeBot will log its LXMF address on startup. Use this address to contact it from any LXMF client on the network.

---

## Adding transports

Each transport has its own installer. Run them after `install_nodebot.sh`. You can install any combination.

### LXMF / rNode

```bash
bash installer/install_lxmf.sh
```

Connects NodeBot to a LoRa rNode flashed with Reticulum firmware and the NomadNet mesh network.

### MeshCore

```bash
bash installer/install_meshcore.sh
```

Detects your MeshCore radio on USB, creates a stable `/dev/meshcore0` symlink, sets the radio frequency, and writes the `[meshcore]` section to `config.ini`.

### Meshtastic

```bash
bash installer/install_meshtastic.sh
```

Detects your Meshtastic radio on USB, creates a stable `/dev/meshtastic0` symlink, configures region/preset/hops/power, sets up optional environmental telemetry, and writes `[meshtastic]` and `[telemetry]` sections to `config.ini`.

> **Note:** Writing LoRa config to a Meshtastic radio causes it to reboot (~30 seconds). This is normal — NodeBot reconnects automatically. The installer handles this transparently on subsequent runs by skipping the write if nothing has changed.

> **Note:** GPS settings are shared across all transports. The installer for NodeBot (`install_nodebot.sh`) prompts for GPS configuration and writes a single `[gps]` section that all adapters read.

---

## Built-in Commands

Send any of these to NodeBot's LXMF address:

| Command | Description |
|---------|-------------|
| `help` / `?` | List available commands |
| `ping` | Check if the bot is alive |
| `time` | Current time on the bot's host |
| `uptime` | How long the bot has been running |
| `whoami` | Your LXMF address as seen by the bot |
| `echo <text>` | Echo text back |
| `version` | Bot software version |
| `stats` | Message and command statistics |

Admin commands (require login or trusted address):

| Command | Description |
|---------|-------------|
| `admin login <password>` | Authenticate as admin |
| `admin logout` | End admin session |
| `lockdown` | Toggle lockdown mode (non-admins blocked) |

### Cross-network relay

NodeBot can bridge messages between protocols (LXMF, MeshCore, Meshtastic):

```
relay <protocol:address> <message>
```

Protocol prefixes: `lxmf:`, `mc:`, `mesh:`

```
relay mesh:02ece6b8 Hello from MeshCore
relay mc:091733a4 Hello from LXMF
relay lxmf:2f2441ef Hello from Meshtastic
```

Chain through another NodeBot:
```
relay mc:nodebotA relay mesh:02ece6b8 Hello
```

Once a relay is active, reply with:
```
Respond: <message>
```

You will receive `Relay: delivered` or `Relay: delivery failed` as confirmation.

---

## Plugin System

Plugins live in `src/plugins/`. NodeBot hot-reloads them automatically — drop a `.py` file in and it becomes active within `scan_interval` seconds, no restart needed.

A minimal plugin looks like:

```python
from commands import register

@register("greet", description="Say hello")
def greet(args, sender):
    return f"Hello, {sender[:8]}!"
```

The `@register` decorator accepts:
- `aliases` — list of alternative command names
- `description` — shown in `help` output
- `admin_only` — restrict to admin users
- `cooldown` — per-user rate limit in seconds

---

## Service Management

```bash
# Status
systemctl status nodebot

# Logs (live)
journalctl -u nodebot -f

# Restart
sudo systemctl restart nodebot

# Stop
sudo systemctl stop nodebot
```

If LXMF is installed, NomadNet runs as a separate service that NodeBot depends on:

```bash
systemctl status nomadnet nodebot
journalctl -u nomadnet -f
```

---

## Troubleshooting

**`Could not exclusively lock port` / `Resource temporarily unavailable` after Meshtastic start**
The Meshtastic radio reboots after its LoRa config is written for the first time. NodeBot retries every 10 seconds and reconnects automatically once the radio is back online (~30–60 seconds). This only happens once per config change.

**Both adapters connecting to the same device**
Check `config.ini` — `[meshcore] port` and `[meshtastic] port` must point to different devices. A common mistake when editing the file is accidentally setting both to the same path.

**`/dev/meshcore0` or `/dev/meshtastic0` disappears after replug**
Your device has a generic USB serial number (common on CP2102 clones — serial reads `0001`). The udev symlink is tied to the physical USB port. Plug the device back into the same USB socket. If you need to change sockets, re-run the relevant installer.

**Meshtastic adapter keeps rebooting in a loop**
NodeBot writes LoRa config on connect and saves the applied values to `~/.nodebot/lxmf_storage/meshtastic_lora.json`. On subsequent starts it compares the saved state and skips the write if nothing changed. If the loop persists, delete that file and NodeBot will rewrite it cleanly on next start.

**MeshCore or Meshtastic not responding to commands**
Check that the correct port is set in `config.ini` and that NodeBot has permission to access it (`ls -la /dev/meshcore0 /dev/meshtastic0` — should be owned by `dialout` group; add your user with `sudo usermod -aG dialout $USER`).

---

## Project Layout

```
NodeBot/
├── config.example          # configuration template
├── config.ini              # active configuration (create from example)
├── installer/
│   ├── install_nodebot.sh  # base installer (Python env, systemd, GPS config)
│   ├── install_lxmf.sh     # LXMF + NomadNet + rNode installer
│   ├── install_meshcore.sh # MeshCore radio installer
│   ├── install_meshtastic.sh # Meshtastic radio installer
│   ├── wait_for_rns.sh     # startup helper (waits for RNS socket)
│   ├── nodebot.service     # systemd service template
│   └── nomadnet.service    # systemd service template
├── src/
│   ├── runbot.py           # systemd entrypoint
│   ├── nodebot.py          # main coordinator
│   ├── commands.py         # plugin loader and command dispatcher
│   ├── meshbridge/         # core engine (routing, state, transport layer)
│   ├── plugins/            # built-in plugins (relay, help, admin, tools, ...)
│   └── transports/         # protocol adapters (LXMF, Meshtastic, MeshCore)
├── requirements.txt
└── pyproject.toml
```
