# Meshtastic Regional Radio Settings

Sources:
- https://meshtastic.org/docs/overview/radio-settings/
- https://meshtastic.org/docs/configuration/radio/lora/
- https://raw.githubusercontent.com/meshtastic/firmware/master/src/mesh/RadioInterface.cpp (RegionInfo table, applyModemConfig)

---

## Physical Layer Facts

- Meshtastic uses **raw LoRa modulation** — no proprietary framing below the Meshtastic layer.
- Physical MTU: **~256 bytes** (varies by firmware version).
- Half-duplex required.
- Preamble: **16 symbols** (all presets).
- Sync word: **`0x2B`** (public) → SX1262 registers `0x24` / `0xB4`.
  - Meshtastic uses a public sync word; Reticulum and MeshCore use the private word `0x12` → `0x14`/`0x24`.
  - Meshtastic packets are **not interoperable** with Reticulum or MeshCore at the LoRa layer.
- Region and modem preset are set via firmware API (no manual RF config required).

---

## Modem Presets — Fixed Parameters (all regions)

Preamble: **16 symbols** (all presets).  
Sync word: **0x2B** (public) → SX1262 registers `0x24` / `0xB4`.

| Preset | SF | BW (kHz) | CR | Symbol Time (ms) | Data Rate (kbps) | Link Budget (dB) |
|--------|----|----------|----|-----------------|------------------|-----------------|
| SHORT_TURBO | 7 | 500 | 4/5 | 0.26 | 21.88 | 140 |
| SHORT_FAST | 7 | 250 | 4/5 | 0.51 | 10.94 | 143 |
| SHORT_SLOW | 8 | 250 | 4/5 | 1.02 | 6.25 | 145.5 |
| MEDIUM_FAST | 9 | 250 | 4/5 | 2.05 | 3.52 | 148 |
| MEDIUM_SLOW | 10 | 250 | 4/5 | 4.10 | 1.95 | 150.5 |
| LONG_TURBO | 11 | 500 | 4/8 | 4.10 | 1.34 | 150 |
| **LONG_FAST** | **11** | **250** | **4/5** | **8.19** | **1.07** | **153** |
| LONG_MODERATE | 11 | 125 | 4/8 | 16.38 | 0.34 | 156 |
| LONG_SLOW | 12 | 125 | 4/8 | 32.77 | 0.18 | 158.5 |
| VERY_LONG_SLOW | 12 | 62.5 | 4/8 | 65.54 | ~0.09 | ~161 |

---

## Channel Frequency Formula

```
numSlots       = round((freqEnd - freqStart) / BW_MHz)
slotIndex      = djb2_hash(presetCamelName) % numSlots        (0-based)
ch0_freq (MHz) = freqStart + BW_MHz/2 + slotIndex × BW_MHz
```

DJB2: `hash = 5381; for each char c: hash = (hash * 33 + c) & 0xFFFFFFFF`

Preset CamelCase names: `ShortTurbo`, `ShortFast`, `ShortSlow`, `MediumFast`, `MediumSlow`,
`LongTurbo`, `LongFast`, `LongSlow`, `LongModerate`, `VeryLongSlow`.

**Confirmed:** `djb2("LongFast") = 130,429,955`  
→ US (104 slots): index 19 → **906.875 MHz** ✓  
→ EU_868 (1 slot): index 0 → **869.525 MHz** ✓  
→ EU_433 (4 slots): index 3 → **433.875 MHz** ✓

---

## US (902–928 MHz)

Region: `US` · Duty Cycle: **100%** · Max TX: **30 dBm** · 26 MHz span

| Preset | SF | BW (kHz) | CR | Ch.0 Freq (MHz) | Max TX (dBm) | Duty (%) | Symbol Time (ms) | Data Rate (kbps) | Notes |
|--------|----|----------|----|-----------------|-------------|----------|-----------------|-----------------|-------|
| SHORT_TURBO | 7 | 500 | 4/5 | formula (52 slots) | 30 | 100 | 0.26 | 21.88 | |
| SHORT_FAST | 7 | 250 | 4/5 | formula (104 slots) | 30 | 100 | 0.51 | 10.94 | |
| SHORT_SLOW | 8 | 250 | 4/5 | formula (104 slots) | 30 | 100 | 1.02 | 6.25 | |
| MEDIUM_FAST | 9 | 250 | 4/5 | formula (104 slots) | 30 | 100 | 2.05 | 3.52 | |
| MEDIUM_SLOW | 10 | 250 | 4/5 | formula (104 slots) | 30 | 100 | 4.10 | 1.95 | |
| LONG_TURBO | 11 | 500 | 4/8 | formula (52 slots) | 30 | 100 | 4.10 | 1.34 | |
| **LONG_FAST** | **11** | **250** | **4/5** | **906.875** | **30** | **100** | **8.19** | **1.07** | **Default** |
| LONG_MODERATE | 11 | 125 | 4/8 | formula (208 slots) | 30 | 100 | 16.38 | 0.34 | |
| LONG_SLOW | 12 | 125 | 4/8 | formula (208 slots) | 30 | 100 | 32.77 | 0.18 | |
| VERY_LONG_SLOW | 12 | 62.5 | 4/8 | formula (416 slots) | 30 | 100 | 65.54 | ~0.09 | |

---

## Cross-Protocol Comparison (US)

| Parameter | Meshtastic US LongFast | MeshCore US | Reticulum (US) |
|-----------|------------------------|-------------|----------------|
| Frequency | 906.875 MHz | 910.525 MHz | 915.000 MHz |
| BW | 250 kHz | 62.5 kHz | 125 kHz |
| SF | 11 | 7 | 8 |
| CR | 4/5 | 4/5 | 4/5 |
| Symbol time | 8.19 ms | 2.05 ms | 2.05 ms |
| Preamble | 16 symbols | 16 symbols | 8 symbols |
| Sync word | 0x24/0xB4 (public 0x2B) | 0x14/0x24 (private) | 0x14/0x24 (private) |
| Data rate | ~1.07 kbps | ~3.5 kbps | ~3.1 kbps |
| Encryption | AES256-CTR (shared PSK) | ECDH + AES (per node) | X25519 + AES-128 (link) |
| MTU | ~256 bytes | 256 bytes | 500 bytes |
| Addressing | 4-byte node number | 1-byte pubkey prefix | 16-byte hash |

---

## Europe — EU_868 (869.4–869.65 MHz)

Region: `EU_868` · Duty Cycle: **10% max (ETSI EN300220 — must enforce)** · Max TX: **27 dBm** · 250 kHz span

With BW=250 kHz there is exactly 1 slot → all 250 kHz presets share the single frequency 869.525 MHz regardless of channel hash.

| Preset | SF | BW (kHz) | CR | Ch.0 Freq (MHz) | Max TX (dBm) | Duty (%) | Symbol Time (ms) | Data Rate (kbps) | Notes |
|--------|----|----------|----|-----------------|-------------|----------|-----------------|-----------------|-------|
| SHORT_TURBO | 7 | 500 | 4/5 | — | — | — | 0.26 | 21.88 | **Not allowed** — BW > band span |
| SHORT_FAST | 7 | 250 | 4/5 | **869.525** | 27 | 10 | 0.51 | 10.94 | 1 slot only |
| SHORT_SLOW | 8 | 250 | 4/5 | **869.525** | 27 | 10 | 1.02 | 6.25 | 1 slot only |
| MEDIUM_FAST | 9 | 250 | 4/5 | **869.525** | 27 | 10 | 2.05 | 3.52 | 1 slot only |
| MEDIUM_SLOW | 10 | 250 | 4/5 | **869.525** | 27 | 10 | 4.10 | 1.95 | 1 slot only |
| LONG_TURBO | 11 | 500 | 4/8 | — | — | — | 4.10 | 1.34 | **Not allowed** — BW > band span |
| **LONG_FAST** | **11** | **250** | **4/5** | **869.525** | **27** | **10** | **8.19** | **1.07** | **Default — 1 slot only** |
| LONG_MODERATE | 11 | 125 | 4/8 | — | — | — | 16.38 | 0.34 | **Not allowed** — firmware-restricted |
| LONG_SLOW | 12 | 125 | 4/8 | formula (2 slots) | 27 | 10 | 32.77 | 0.18 | 869.4625 or 869.5875 MHz |
| VERY_LONG_SLOW | 12 | 62.5 | 4/8 | formula (4 slots) | 27 | 10 | 65.54 | ~0.09 | 869.43–869.62 MHz |

---

## Europe — EU_433 (433.0–434.0 MHz)

Region: `EU_433` · Duty Cycle: **10% max** · Max TX: **10 dBm** · 1 MHz span

| Preset | SF | BW (kHz) | CR | Ch.0 Freq (MHz) | Max TX (dBm) | Duty (%) | Symbol Time (ms) | Data Rate (kbps) | Notes |
|--------|----|----------|----|-----------------|-------------|----------|-----------------|-----------------|-------|
| SHORT_TURBO | 7 | 500 | 4/5 | — | — | — | 0.26 | 21.88 | **Not allowed** — BW > band span |
| SHORT_FAST | 7 | 250 | 4/5 | formula (4 slots) | 10 | 10 | 0.51 | 10.94 | |
| SHORT_SLOW | 8 | 250 | 4/5 | formula (4 slots) | 10 | 10 | 1.02 | 6.25 | |
| MEDIUM_FAST | 9 | 250 | 4/5 | formula (4 slots) | 10 | 10 | 2.05 | 3.52 | |
| MEDIUM_SLOW | 10 | 250 | 4/5 | formula (4 slots) | 10 | 10 | 4.10 | 1.95 | |
| LONG_TURBO | 11 | 500 | 4/8 | — | — | — | 4.10 | 1.34 | **Not allowed** — BW > band span |
| **LONG_FAST** | **11** | **250** | **4/5** | **433.875** | **10** | **10** | **8.19** | **1.07** | **Default** |
| LONG_MODERATE | 11 | 125 | 4/8 | formula (8 slots) | 10 | 10 | 16.38 | 0.34 | |
| LONG_SLOW | 12 | 125 | 4/8 | formula (8 slots) | 10 | 10 | 32.77 | 0.18 | |
| VERY_LONG_SLOW | 12 | 62.5 | 4/8 | formula (16 slots) | 10 | 10 | 65.54 | ~0.09 | |

---

## Australia / New Zealand — ANZ (915–928 MHz)

Region: `ANZ` · Duty Cycle: **100%** · Max TX: **30 dBm** · 13 MHz span

| Preset | SF | BW (kHz) | CR | Ch.0 Freq (MHz) | Max TX (dBm) | Duty (%) | Symbol Time (ms) | Data Rate (kbps) | Notes |
|--------|----|----------|----|-----------------|-------------|----------|-----------------|-----------------|-------|
| SHORT_TURBO | 7 | 500 | 4/5 | formula (26 slots) | 30 | 100 | 0.26 | 21.88 | |
| SHORT_FAST | 7 | 250 | 4/5 | formula (52 slots) | 30 | 100 | 0.51 | 10.94 | |
| SHORT_SLOW | 8 | 250 | 4/5 | formula (52 slots) | 30 | 100 | 1.02 | 6.25 | |
| MEDIUM_FAST | 9 | 250 | 4/5 | formula (52 slots) | 30 | 100 | 2.05 | 3.52 | |
| MEDIUM_SLOW | 10 | 250 | 4/5 | formula (52 slots) | 30 | 100 | 4.10 | 1.95 | |
| LONG_TURBO | 11 | 500 | 4/8 | formula (26 slots) | 30 | 100 | 4.10 | 1.34 | |
| **LONG_FAST** | **11** | **250** | **4/5** | **919.875** | **30** | **100** | **8.19** | **1.07** | **Default** |
| LONG_MODERATE | 11 | 125 | 4/8 | formula (104 slots) | 30 | 100 | 16.38 | 0.34 | |
| LONG_SLOW | 12 | 125 | 4/8 | formula (104 slots) | 30 | 100 | 32.77 | 0.18 | |
| VERY_LONG_SLOW | 12 | 62.5 | 4/8 | formula (208 slots) | 30 | 100 | 65.54 | ~0.09 | |

---

## Japan — JP (920.5–923.5 MHz)

Region: `JP` · Duty Cycle: **100%** · Max TX: **13 dBm** · 3 MHz span

| Preset | SF | BW (kHz) | CR | Ch.0 Freq (MHz) | Max TX (dBm) | Duty (%) | Symbol Time (ms) | Data Rate (kbps) | Notes |
|--------|----|----------|----|-----------------|-------------|----------|-----------------|-----------------|-------|
| SHORT_TURBO | 7 | 500 | 4/5 | formula (6 slots) | 13 | 100 | 0.26 | 21.88 | |
| SHORT_FAST | 7 | 250 | 4/5 | formula (12 slots) | 13 | 100 | 0.51 | 10.94 | |
| SHORT_SLOW | 8 | 250 | 4/5 | formula (12 slots) | 13 | 100 | 1.02 | 6.25 | |
| MEDIUM_FAST | 9 | 250 | 4/5 | formula (12 slots) | 13 | 100 | 2.05 | 3.52 | |
| MEDIUM_SLOW | 10 | 250 | 4/5 | formula (12 slots) | 13 | 100 | 4.10 | 1.95 | |
| LONG_TURBO | 11 | 500 | 4/8 | formula (6 slots) | 13 | 100 | 4.10 | 1.34 | |
| **LONG_FAST** | **11** | **250** | **4/5** | **923.375** | **13** | **100** | **8.19** | **1.07** | **Default** |
| LONG_MODERATE | 11 | 125 | 4/8 | formula (24 slots) | 13 | 100 | 16.38 | 0.34 | |
| LONG_SLOW | 12 | 125 | 4/8 | formula (24 slots) | 13 | 100 | 32.77 | 0.18 | |
| VERY_LONG_SLOW | 12 | 62.5 | 4/8 | formula (48 slots) | 13 | 100 | 65.54 | ~0.09 | |

---

## All Other Regions — LONG_FAST Summary

All values for LONG_FAST: SF=11, BW=250 kHz, CR=4/5, symbol time 8.19 ms, 1.07 kbps.  
Ch.0 freq = `freqStart + 0.125 + (djb2("LongFast") % numSlots) × 0.25`

| Region | Freq Range (MHz) | numSlots | Slot Index | Ch.0 Freq (MHz) | Max TX (dBm) | Duty (%) |
|--------|-----------------|----------|-----------|----------------|-------------|----------|
| CN | 470.0–510.0 | 160 | 35 | **478.875** | 19 | 100 |
| RU | 868.7–869.2 | 2 | 1 | **869.075** | 20 | 100 |
| KR | 920.0–923.0 | 12 | 11 | **922.875** | 23 | 100 |
| TW | 920.0–925.0 | 20 | 15 | **923.875** | 27 | 100 |
| IN | 865.0–867.0 | 8 | 3 | **865.875** | 30 | 100 |
| NZ_865 | 864.0–868.0 | 16 | 3 | **864.875** | 36 | 100 |
| TH | 920.0–925.0 | 20 | 15 | **923.875** | 27 | **10** |
| ANZ_433 | 433.05–434.79 | 6 | formula | formula | 14 | 100 |
| UA_433 | 433.0–434.7 | 6 | formula | formula | 10 | **10** |
| UA_868 | 868.0–868.6 | 2 | 1 | **868.825** | 14 | **1** |
| MY_433 | 433.0–435.0 | 8 | formula | formula | 20 | 100 |
| MY_919 | 919.0–924.0 | 20 | 15 | **922.875** | 27 | 100 |
| SG_923 | 917.0–925.0 | 32 | formula | formula | 20 | 100 |
| PH_433 | 433.0–434.7 | 6 | formula | formula | 10 | 100 |
| PH_868 | 868.0–869.4 | 5 | formula | formula | 14 | 100 |
| PH_915 | 915.0–918.0 | 12 | 11 | **917.875** | 24 | 100 |
| KZ_433 | 433.075–434.775 | 6 | formula | formula | 10 | 100 |
| KZ_863 | 863.0–868.0 | 20 | 15 | **866.875** | 30 | 100 |
| NP_865 | 865.0–868.0 | 12 | 11 | **867.375** | 30 | 100 |
| BR_902 | 902.0–907.5 | 22 | formula | formula | 30 | 100 |
| LORA_24 | 2400.0–2483.5 | 334 | formula | formula | 10 | 100 |

> MY_919 uses LongFast slot 15 in 919–924 MHz: freq = 919.0 + 0.125 + 15×0.25 = 922.875 MHz.  
> PH_915 slot 11 in 915–918 MHz: freq = 915.0 + 0.125 + 11×0.25 = 917.875 MHz.  
> KZ_863 slot 15 in 863–868 MHz: freq = 863.0 + 0.125 + 15×0.25 = 866.875 MHz.  
> NP_865 slot 11 in 865–868 MHz (12 slots): freq = 865.0 + 0.125 + 11×0.25 = 867.375 MHz.  
> UA_868 slot 1 in 868.0–868.6 MHz (2 slots): freq = 868.0 + 0.125 + 1×0.25 = 868.375 MHz.

---

## Setup Checklist: US LONG_FAST

| Parameter | Value |
|-----------|-------|
| Region | US |
| Preset | LONG_FAST |
| Frequency | 906.875 MHz (channel 0) |
| Bandwidth | 250 kHz |
| SF | 11 |
| CR | 4/5 |
| Preamble | 16 symbols |
| Sync Word | 0x2B → registers 0x24 / 0xB4 |
| TX Power | ≤30 dBm |
| Duty Cycle | 100% |
| Default PSK | `{0xd4,0xf1,0xbb,0x3a,0x20,0x29,0x07,0x59,0xf0,0xbc,0xff,0xab,0xcf,0x4e,0x69,0xbf}` |
| Channel Hash | 0xBC |

## Setup Checklist: EU_868 LONG_FAST

| Parameter | Value |
|-----------|-------|
| Region | EU_868 |
| Preset | LONG_FAST |
| Frequency | 869.525 MHz (only slot) |
| Bandwidth | 250 kHz |
| SF | 11 |
| CR | 4/5 |
| Preamble | 16 symbols |
| Sync Word | 0x2B → registers 0x24 / 0xB4 |
| TX Power | ≤27 dBm |
| Duty Cycle | **10% max — must enforce in firmware** |

---

## CAD / Scanning Timing (US)

For BridgeNode use (our project's Meshtastic slot):

| Parameter | Value |
|-----------|-------|
| Frequency | 906.875 MHz |
| BW | 250 kHz |
| SF | 11 |
| CR | 4/5 |
| Symbol time | 8.19 ms |
| CAD duration (4 symbols) | ~32.8 ms |
| Recommended cadTimeoutMs | 45 ms |
| Max packet RX time | ~2600 ms (~256B at SF11/250) |
| cadDetPeak | 26 (SF+15) |
| cadRssiGate | -110 dBm |
