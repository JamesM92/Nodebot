# LXMF Setup Guide

This guide covers connecting NodeBot to a LoRa mesh network using an rNode device, NomadNet, and the Reticulum Network Stack (RNS).

**Complete the [base NodeBot installation](../README.md) before following this guide.**

---

## What you need

- An rNode-compatible LoRa device (e.g. Heltec LoRa32 v3, LilyGO T-Beam) flashed with [rNode firmware](https://unsigned.io/rnode/)
- The device connected via USB to the Raspberry Pi
- NodeBot base install already done

---

## Run the LXMF installer

```bash
bash installer/install_lxmf.sh
```

The installer walks you through each step interactively.

---

## What the installer does

### 1. Legal disclaimer
You must confirm you understand that frequency settings are your legal responsibility before proceeding.

### 2. Install NomadNet globally
NomadNet is installed system-wide via `pip3 --user`, independent of the NodeBot virtual environment. It provides the RNS shared instance that owns the rNode hardware.

### 3. Detect rNodes
The installer probes every `/dev/ttyUSB*` and `/dev/ttyACM*` port using `rnodeconf --info` to identify which devices are running rNode firmware. NomadNet is paused during this scan so ports are free to probe.

### 4. Create stable udev device names
Each detected rNode gets a permanent symlink:

| Symlink | Points to |
|---------|-----------|
| `/dev/rnode0` | First detected rNode |
| `/dev/rnode1` | Second detected rNode |
| ... | ... |

**If the device has a unique USB serial number** the symlink follows the device to any USB port it is plugged into.

**If the serial is a generic factory default** (e.g. `0001`) the symlink is tied to the physical USB port instead — the device must stay in the same port to be recognised.

Rules are written to `/etc/udev/rules.d/99-rnode.rules`. When an rNode is unplugged and replugged, the symlink is recreated automatically and RNS reconnects without any manual intervention.

### 5. Region / frequency selection
You are prompted to select your region from a list of community-reported frequency settings sourced from the [Reticulum wiki](https://github.com/markqvist/Reticulum/wiki/Popular-RNode-Settings).

> **You are solely responsible for ensuring your chosen frequency, bandwidth, and power settings comply with local radio regulations.**

Common settings:

| Region | Frequency | BW | SF |
|--------|-----------|----|----|
| United States | 914.875 MHz | 125 kHz | SF8 |
| United Kingdom | 867.5 MHz | 125 kHz | SF9 |
| Australia | 925.875 MHz | 250 kHz | SF9 |
| Germany | 869.525 MHz | 125 kHz | SF8 |

Both sides of a radio link must use **identical** frequency, bandwidth, and spreading factor to communicate.

### 6. Write `~/.reticulum/config`
The RNS configuration is written with your chosen RF parameters and the stable `/dev/rnodeN` device path.

### 7. Install systemd services

| Service | Purpose |
|---------|---------|
| `nomadnet.service` | Owns the rNode and RNS shared instance. Starts first. |
| `nodebot.service` | Connects to NomadNet's RNS instance. Waits for it to be ready. |

Both services are enabled to start automatically on boot.

---

## Finding NodeBot's LXMF address

NodeBot's address is logged on every startup:

```bash
journalctl -u nodebot | grep "bot address"
```

Example output:
```
[lxmf_adapter] LXMF ready — bot address: <2f2441ef70215ac0d88dd15b12feb7e9>
```

This address is permanent — it is derived from the identity file at `~/.nodebot/lxmf_storage/identity` and does not change across restarts.

A plain-text copy of the identity is saved alongside it at `~/.nodebot/lxmf_storage/identity.txt` for reference.

---

## Connecting from another device

NodeBot announces itself on the network as **"NodeBot"** (or whatever name is set in `config.ini`). From any LXMF client (Sideband, NomadNet, another NodeBot):

1. Wait for the announce to propagate (a few seconds if in RF range)
2. Look for **"NodeBot"** in your contacts or discovered nodes
3. Send a message — try `?` or `help` to see available commands

If the announce has not been received yet, restart NodeBot to force a fresh announce:

```bash
sudo systemctl restart nodebot
```

---

## Service management

```bash
# Status of both services
systemctl status nomadnet nodebot

# Live logs
journalctl -u nomadnet -f
journalctl -u nodebot -f

# Restart both (e.g. after config change)
sudo systemctl restart nomadnet nodebot
```

---

## Multiple rNodes

If you have more than one rNode, the installer detects all of them and asks which one should be the primary for LXMF. Additional rNodes can be added to `~/.reticulum/config` as extra `[[RNodeInterface]]` sections after the initial setup.

---

## Troubleshooting

**rNode not detected during install**
- Confirm the device is plugged in: `ls /dev/ttyUSB*`
- Confirm it has rNode firmware: `~/.local/bin/rnodeconf /dev/ttyUSB0 --info`
- If another process is using the port, the installer will ask you to stop it first

**Messages sent but not received by NodeBot**
- Confirm you are sending to NodeBot's address, not NomadNet's — they are different
- Both addresses are visible in `journalctl -u nodebot` on startup
- Restart to re-announce: `sudo systemctl restart nodebot`

**RF link not working between two rNodes**
- Frequency, bandwidth, and spreading factor must be identical on both sides
- Coding rate and TX power can differ — they do not need to match
- Check: [Reticulum wiki — Popular RNode Settings](https://github.com/markqvist/Reticulum/wiki/Popular-RNode-Settings)

**Device path changed after replug**
- If the udev rule is path-based (generic serial), the device must be plugged into the same USB port
- Re-run `bash installer/install_lxmf.sh` with the device in the desired port to update the rule
