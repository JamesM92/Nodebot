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

## Adding LXMF / rNode support

The base install runs NodeBot in standalone mode. To connect it to a LoRa rNode and the Reticulum mesh network, run the LXMF installer:

```bash
bash installer/install_lxmf.sh
```

See **[docs/lxmf-setup.md](docs/lxmf-setup.md)** for full details.

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

## Project Layout

```
NodeBot/
├── config.example          # configuration template
├── config.ini              # active configuration (create from example)
├── installer/
│   ├── install_nodebot.sh  # base installer
│   ├── install_lxmf.sh     # LXMF + NomadNet + rNode installer
│   ├── wait_for_rns.sh     # startup helper (waits for RNS socket)
│   ├── nodebot.service     # systemd service template
│   └── nomadnet.service    # systemd service template
├── docs/
│   └── lxmf-setup.md       # LXMF setup guide
├── src/
│   ├── runbot.py           # systemd entrypoint
│   ├── nodebot.py          # main coordinator
│   ├── commands.py         # plugin loader and command dispatcher
│   ├── meshbridge/         # core engine (routing, state, transport layer)
│   ├── plugins/            # built-in plugins
│   └── transports/         # protocol adapters (LXMF, Meshtastic, MeshCore)
├── requirements.txt
└── pyproject.toml
```
