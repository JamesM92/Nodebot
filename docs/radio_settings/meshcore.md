# MeshCore Regional Radio Settings

Sources:
- https://docs.meshcore.io/cli_commands (CLI radio commands)
- https://github.com/ripplebiz/MeshCore/blob/main/docs/companion_protocol.md (PACKET_SELF_INFO radio fields)
- MeshCore CLI defaults and community-established regional norms

---

## Physical Layer Facts

- MeshCore uses **raw LoRa modulation** — no LoRaWAN framing.
- Physical MTU: **256 bytes**.
- Half-duplex required.
- No built-in preset system — frequency, BW, SF, and CR are configured manually via CLI or companion app.
- Preamble: **16 symbols** (community convention).

---

## Configuration Reference

```
get radio          → show current: freq,bw,sf,cr
set radio <freq>,<bw>,<sf>,<cr>   → set and save (requires reboot)
get tx / set tx <dbm>             → TX power (1-22 dBm from chip; FEM may boost)
get freq / set freq <mhz>         → frequency only
set radio.rxgain on|off           → boosted RX gain (SX1262/SX1268 only; default on v1.14.1+)
tempradio <freq>,<bw>,<sf>,<cr>,<timeout_mins>  → temporary (non-persistent)
```

Valid ranges:
- Frequency: 300–2500 MHz
- Bandwidth: 7.8–500 kHz
- SF: 5–12
- CR: 5–8 (= 4/N coding rate denominator)
- TX power: 1–22 dBm (chip level; board FEM adds gain on top)

---

## Sync Word

MeshCore uses the **private LoRa sync word** (not the Meshtastic public one):
- Sync word: `0x12` (private) → SX1262 registers `0x14` / `0x24`
- This means MeshCore and Meshtastic packets are **not interoperable** at the LoRa layer.

---

## Regional Presets

Machine-readable preset data: `docs/radio_settings/presets.toml`

MeshCore has no built-in regional preset system — users configure frequency/BW/SF/CR manually.
The following are the most widely used community configurations per region.

### North America (US 915 MHz ISM)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Frequency | 910.525 MHz | Common default. Clear of Meshtastic (906.875 MHz). |
| Bandwidth | 62.5 kHz | Good range/speed balance |
| SF | 7 | Fast; adequate for short–medium range |
| CR | 4/5 (5) | Standard |
| TX Power | 20–22 dBm chip (≈27–30 dBm with FEM) | |
| Symbol time | 2.05 ms | |
| Preamble | 16 symbols | |
| Sync word | 0x14/0x24 | Private, not Meshtastic |
| Max range | ~5–15 km open field | Depends on hardware |

### Europe (868 MHz)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Frequency | 869.525 MHz | EU_868 default band (869.4–869.65 MHz) |
| Bandwidth | 62.5 kHz | Fits in EU narrowband allocations |
| SF | 7 | |
| CR | 4/5 (5) | |
| TX Power | ≤27 dBm EIRP | EU_868 regulatory limit |
| Duty Cycle | **≤10%** | ETSI EN300220 requirement — must enforce in firmware |
| Sync word | 0x14/0x24 | Private |

**Alternative EU 433 MHz:**

| Parameter | Value |
|-----------|-------|
| Frequency | 433.525 MHz |
| Bandwidth | 62.5 kHz |
| SF | 7 |
| CR | 4/5 |
| TX Power | ≤10 dBm EIRP |
| Duty Cycle | ≤10% |

### Australia / New Zealand (ANZ 915 MHz)

| Parameter | Value |
|-----------|-------|
| Frequency | 915.525 MHz |
| Bandwidth | 62.5 kHz |
| SF | 7 |
| CR | 4/5 |
| TX Power | ≤30 dBm |
| Duty Cycle | 100% |

### Japan (920 MHz)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Frequency | 921.525 MHz | Within 920.5–923.5 MHz band |
| Bandwidth | 62.5 kHz | |
| SF | 7 | |
| CR | 4/5 | |
| TX Power | ≤13 dBm | JP regulatory limit |

---

## PACKET_SELF_INFO Radio Fields

When connected over BLE, the device reports its active radio config in `PACKET_SELF_INFO` (0x05):

```
Bytes 48-51: Radio Frequency (uint32 LE, divided by 1000.0 → MHz)
Bytes 52-55: Radio Bandwidth (uint32 LE, divided by 1000.0 → kHz)
Byte 56:     Radio Spreading Factor (uint8)
Byte 57:     Radio Coding Rate (uint8, denominator of 4/N)
```

Use these to auto-detect the device's active channel when connecting.

---

## Cross-Protocol Comparison (US)

| Parameter | MeshCore US | Reticulum (US) | Meshtastic US LongFast |
|-----------|-------------|----------------|------------------------|
| Frequency | 910.525 MHz | 915.000 MHz | 906.875 MHz |
| BW | 62.5 kHz | 125 kHz | 250 kHz |
| SF | 7 | 8 | 11 |
| CR | 4/5 | 4/5 | 4/5 |
| Symbol time | 2.05 ms | 2.05 ms | 8.19 ms |
| Preamble | 16 symbols | 8 symbols | 16 symbols |
| Sync word | 0x14/0x24 (private) | 0x14/0x24 (private) | 0x24/0xB4 (public 0x2B) |
| Data rate | ~3.5 kbps | ~3.1 kbps | ~1.07 kbps |
| Encryption | ECDH + AES (per node/channel) | X25519 + AES-128 (link) | AES256-CTR (shared PSK) |
| MTU | 256 bytes | 500 bytes | ~256 bytes |
| Topology | Flood + direct path | Routed mesh | Managed flooding |

---

## CAD / Scanning Timing (US)

For BridgeNode use (our project's MeshCore slot):

| Parameter | Value |
|-----------|-------|
| Frequency | 910.525 MHz |
| BW | 62.5 kHz |
| SF | 7 |
| CR | 4/5 |
| Symbol time | 2.05 ms |
| CAD duration (4 symbols) | ~8.2 ms |
| Recommended cadTimeoutMs | 15 ms |
| Max packet RX time | ~2500 ms (large payload at SF7/62.5) |
| cadDetPeak | 22 (SF+15, conservative to reject noise) |
| cadRssiGate | -110 dBm |
