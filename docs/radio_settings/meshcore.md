# MeshCore Regional Radio Settings

---

## AGC Reset and Radio Deafness (Serial Companion Mode)

### The problem

The SX126x radio chip used by MeshCore devices (Heltec V3, etc.) has an AGC (Automatic Gain Control) circuit that can lock up when the radio has not transmitted for an extended period, or after a strong out-of-band RF signal hits the receiver. Once locked, the radio appears to the firmware to be fully functional — serial commands still work, the companion app still connects — but the radio silently stops receiving RF packets. Incoming channel messages and node announcements are lost with no error logged anywhere.

This affects **serial companion mode specifically**. BLE companions are immune because the phone disconnects and reconnects frequently (every time it sleeps), and each reconnect triggers a firmware radio reinitialisation. A serial companion stays connected indefinitely, so no natural reinit ever occurs.

### Symptoms of AGC lockup

- Bot receives messages from nearby nodes (strong signal) but misses messages from nodes further away
- Channel goes silent for minutes at a time then resumes
- `rflog` shows undecrypted packets from other channels, but no traffic on Public or your named channels
- No errors logged — the firmware and serial connection both appear healthy
- Reconnecting the serial cable (or restarting the companion app) immediately fixes it

### Fix A: `agc.reset.interval` (Repeater / Room Server firmware only)

> **This setting only exists in Repeater and Room Server firmware. It is NOT present in Companion Radio firmware.** If your device runs Companion Radio firmware (i.e. it connects to NodeBot over USB as a companion), use Fix B below instead.

The MeshCore Repeater/Room Server firmware has a built-in AGC reset mechanism:

```
set agc.reset.interval 4
```

This tells the firmware to run a full SX126x calibration cycle every ~16 seconds internally, without any companion involvement or RF transmissions. **Set this once via a UART serial terminal or the MeshCore BLE app CLI (for supported hardware), and it persists across reboots.**

The `set_custom_var` companion API cannot write this setting — it is firmware CLI config and is rejected with an error on Companion Radio firmware.

### Fix B: NodeBot TX keepalive (Companion Radio firmware)

Since `agc.reset.interval` is not available on Companion Radio firmware, NodeBot sends a zero-hop advert every 8 minutes as a fallback. Any transmission forces the SX126x through a TX → RX state transition, which resets the analog frontend and clears a stuck AGC. This adds minimal RF traffic (one short zero-hop packet every 8 minutes) and works reliably as a workaround on Companion Radio firmware.

The keepalive is active by default and requires no configuration. It is implemented in `nodebot/transports/meshcore_adapter.py` (`_AGC_TX_SECS = 8 * 60`).

---

Sources:
- https://github.com/meshcore-dev/MeshCore/issues/1716 (AGC lockup investigation)
- https://github.com/meshcore-dev/MeshCore/pull/1743 (AGC fix merged in v1.13)
- https://github.com/meshcore-dev/MeshCore/issues/1950 (agc.reset.interval default discussion)
- https://docs.meshcore.io/faq/ (MeshCore FAQ — repeater deafness)
- https://github.com/ripplebiz/MeshCore/blob/main/docs/companion_protocol.md (PACKET_SELF_INFO radio fields)

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
