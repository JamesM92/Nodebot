# Reticulum / RNode Regional Radio Settings

Sources:
- https://reticulum.network/manual/interfaces.html (RNodeInterface config reference)
- https://reticulum.network/manual/understanding.html (physical layer specs)
- https://raw.githubusercontent.com/markqvist/LXMF/master/README.md

---

## Physical Layer Facts

- Reticulum uses **raw LoRa modulation** — no LoRaWAN, no proprietary framing.
- Physical MTU: **500 bytes** (all Reticulum packets must fit).
- Half-duplex required.
- Minimum average throughput: 5 bits/second.
- Preamble: typically 8 symbols (RNode firmware default) but configurable.

---

## Sync Word

No mandatory sync word is specified in Reticulum docs. RNode firmware typically uses the **private sync word `0x12`** → SX1262 registers `0x14` / `0x24`.

- Same private sync word as MeshCore — Reticulum and MeshCore nodes can share the same physical channel.
- Meshtastic uses a different sync word (`0x2B` → `0x24`/`0xB4`) and is **not interoperable** at the LoRa layer.

---

## Configuration Reference

```ini
[[RNodeInterface]]
  type = RNodeInterface
  interface_enabled = True

  # Connection: USB serial, TCP/WiFi, or BLE
  port = /dev/ttyUSB0
  # port = tcp://192.168.1.10
  # port = ble://RNode_Name
  # port = ble://AA:BB:CC:DD:EE:FF

  frequency = 915000000    # Hz (must be integer)
  bandwidth = 125000       # Hz: 7800, 15600, 31250, 62500, 125000, 250000, 500000
  spreadingfactor = 8      # 7-12
  codingrate = 5           # 5-8 (denominator of 4/N)
  txpower = 17             # dBm

  # Optional
  # preamble = 150         # ms (default ~8 symbols)
  # flow_control = False
  # id_callsign = CALLSIGN
  # id_interval = 600      # seconds between ID beacons (ham use)
  # airtime_limit_long = 15    # % max airtime over 60 min
  # airtime_limit_short = 15   # % max airtime over 15 sec
```

---

## Regional Presets

Machine-readable preset data: `docs/radio_settings/presets.toml`

### North America (US 915 MHz ISM)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Frequency | 915.000 MHz | Center of US ISM band. Common RNode default. |
| Bandwidth | 125 kHz | Standard; good range/speed balance |
| SF | 8 | |
| CR | 4/5 (5) | |
| TX Power | 17–20 dBm | Typical; region allows 30 dBm |
| Symbol time | 2.05 ms | |
| Preamble | 8 symbols (~16 ms) | RNode firmware default |
| Sync word | 0x12 private → 0x14/0x24 | Same as MeshCore |
| Data rate | ~3.1 kbps | |
| Duty Cycle | 100% | No limit in US ISM |

**Alternative — longer range:**

| Parameter | Value |
|-----------|-------|
| Frequency | 915.000 MHz |
| Bandwidth | 125 kHz |
| SF | 11 |
| CR | 4/5 |
| Data rate | ~0.5 kbps |
| Symbol time | 16.38 ms |

### Europe (868 MHz)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Frequency | 867.200 MHz | Example from Reticulum docs |
| Bandwidth | 125 kHz | |
| SF | 8 | |
| CR | 4/5 (5) | |
| TX Power | ≤7–14 dBm | Reticulum example uses 7 dBm. EU limit 27 dBm ERP on 869.4–869.65 MHz. |
| Duty Cycle | ≤10% | ETSI EN300220 — **must enforce** |
| Config note | EU 868 MHz band: 869.4–869.65 MHz (250 kHz span). For wider plans use 863–870 MHz. |

### Europe (433 MHz)

| Parameter | Value |
|-----------|-------|
| Frequency | 433.775 MHz |
| Bandwidth | 125 kHz |
| SF | 8 |
| CR | 4/5 |
| TX Power | ≤10 dBm EIRP |
| Duty Cycle | ≤10% |

### Australia / New Zealand (915 MHz)

| Parameter | Value |
|-----------|-------|
| Frequency | 915.000 MHz |
| Bandwidth | 125 kHz |
| SF | 8 |
| CR | 4/5 |
| TX Power | ≤30 dBm |
| Duty Cycle | 100% |

### Japan (920 MHz)

| Parameter | Value |
|-----------|-------|
| Frequency | 921.800 MHz |
| Bandwidth | 125 kHz |
| SF | 8 |
| CR | 4/5 |
| TX Power | ≤13 dBm |

---

## Cross-Protocol Comparison (US)

| Parameter | Reticulum (US) | MeshCore (US) | Meshtastic LongFast (US) |
|-----------|---------------|---------------|--------------------------|
| Frequency | 915.000 MHz | 910.525 MHz | 906.875 MHz |
| BW | 125 kHz | 62.5 kHz | 250 kHz |
| SF | 8 | 7 | 11 |
| CR | 4/5 | 4/5 | 4/5 |
| Symbol time | 2.05 ms | 2.05 ms | 8.19 ms |
| Preamble | 8 symbols | 16 symbols | 16 symbols |
| Sync word | 0x14/0x24 | 0x14/0x24 | 0x24/0xB4 |
| Encryption | X25519 + AES-128 (link) | ECDH + AES (per node) | AES256-CTR (PSK) |
| Addressing | 16-byte hash | 1-byte pubkey prefix | 4-byte node number |
| MTU | 500 bytes | 256 bytes | ~256 bytes |

---

## CAD / Scanning Timing (US)

| Parameter | Value |
|-----------|-------|
| Frequency | 915.000 MHz |
| BW | 125 kHz |
| SF | 8 |
| CR | 4/5 |
| Symbol time | 2.05 ms |
| CAD duration (4 symbols) | ~8.2 ms |
| Recommended cadTimeoutMs | 12 ms |
| Max packet RX time | ~600 ms (500B at SF8/125) |
| cadDetPeak | 23 (SF+15) |
| cadRssiGate | -110 dBm |

---

## LXMF-Specific Notes

LXMF runs on top of Reticulum — the radio parameters are purely Reticulum's. LXMF adds:
- 111-byte message overhead (16B dest + 16B src + 64B signature + msgpack overhead)
- Max useful payload per packet: ~389 bytes (500B MTU − 111B overhead)
- For larger messages, LXMF uses multi-packet delivery over a Reticulum link

No additional radio parameters are required for LXMF beyond the Reticulum RNode configuration above.
