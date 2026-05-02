# MeshCore — Overview

Source: https://raw.githubusercontent.com/fdlamotte/meshcore/main/README.md
Additional: https://github.com/fdlamotte/meshcore

---

## What It Is

MeshCore is a lightweight, portable C++ library enabling multi-hop packet routing for embedded projects using LoRa and other packet radios. The `meshcore` Python package (`pymc-core`) provides a client library for interacting with MeshCore firmware devices.

---

## Core Capabilities

- Multi-hop packet routing with configurable hop limits
- Decentralized wireless mesh without internet infrastructure
- Supported hardware: Heltec, RAK Wireless, and similar LoRa modules
- Low-power operation (battery/solar suitable)
- Node roles: Companion (no repeat), Repeater, Room Server

---

## Installation (Python client)

```bash
pip install pymc-core
```

---

## Architecture (Three Layers)

1. **C++ firmware library** — runs on the embedded device, handles radio I/O and routing
2. **Serial/USB protocol** — binary protocol over UART between device and host
3. **Python client library (`meshcore`)** — async Python wrapper; exposes commands and events

---

## Python Client Key Concepts

### Connection

```python
import asyncio
from meshcore import MeshCore

async def main():
    mc = MeshCore()
    await mc.connect_serial("/dev/meshcore0", baudrate=115200)
    # or
    await mc.connect_tcp("192.168.1.100", port=5000)

asyncio.run(main())
```

### Event System

Subscribe to events with `mc.subscribe(EventType.X, callback)`.

Key event types used by NodeBot:
- `EventType.CHANNEL_MSG_RECV` — decrypted channel message (only fires when device has channel key)
- `EventType.CHANNEL_MSG_RECV_V3` — queued channel message response from `get_msg()`
- `EventType.RX_LOG_DATA` / `EventType.LOG_DATA` (`0x88`) — raw RF packet log (fires for ALL packets, encrypted or not; GRP_TXT payloads decrypted by library if channel key loaded)
- `EventType.CONTACT_MSG_RECV` — direct message from a contact

### Channel Key Loading

```python
# Load channel key from device (enables library-side decryption of LOG_DATA GRP_TXT)
result = await mc.commands.get_channel(idx)  # idx 0-7
mc.set_decrypt_channel_logs(True)
```

### Contacts

```python
contacts = await mc.commands.ensure_contacts()
contact = mc.get_contact_by_key_prefix("091733a4")
name = contact.get("adv_name")
```

### Sending Messages

```python
# Channel message
await mc.commands.send_channel_message(channel_idx=0, text="hello mesh")

# Direct message
await mc.commands.send_contact_message(contact_key="091733a4...", text="hello")
```

---

## NodeBot Integration Notes

- NodeBot's `meshcore_adapter` connects via serial (udev symlink `/dev/meshcore0`)
- Channel message fallback path: subscribe `RX_LOG_DATA`, call `_query_channels()` on startup to load keys, enable `set_decrypt_channel_logs(True)`
- Dedup between `CHANNEL_MSG_RECV` and `RX_LOG_DATA` paths uses `(sender_timestamp, text[:32])` key
- Relay addresses use prefix `mc:` (e.g., `mc:091733a4`)
- Message size limit: 190 bytes (MeshCore practical limit)

---

## Flasher / Firmware

- Web flasher: https://flasher.meshcore.co.uk
- Firmware types: Companion Radio, Simple Repeater, Simple Room Server, Simple Secure Chat
- License: MIT
