# Meshtastic — Radio Settings

Source: https://meshtastic.org/docs/overview/radio-settings/

---

## Frequency Bands by Region

| Region    | Band            | Max Power  | Notes                                          |
|-----------|-----------------|------------|------------------------------------------------|
| EU 433    | 433–434 MHz     | +10 dBm ERP| LongFast, 4 slots; factory default slot 4 = 433.875 MHz |
| EU 868    | 869.40–869.65 MHz| +27 dBm ERP| LongFast, 1 slot; factory default = 869.525 MHz; most popular EU band |
| NA 915    | 902–928 MHz     | +30 dBm ERP| ISM band; LongFast = 104 slots; factory slot 0 → hashed to slot 20 = 906.875 MHz |

---

## Modem Presets

| Preset Name        | Data-Rate    | SF | Coding Rate | Bandwidth | Link Budget |
|--------------------|-------------|-----|-------------|-----------|-------------|
| Short Turbo        | 21.88 kbps  |  7 | 4/5         | 500 kHz   | 140 dB      |
| Short Fast         | 10.94 kbps  |  7 | 4/5         | 250 kHz   | 143 dB      |
| Short Slow         |  6.25 kbps  |  8 | 4/5         | 250 kHz   | 145.5 dB    |
| Medium Fast        |  3.52 kbps  |  9 | 4/5         | 250 kHz   | 148 dB      |
| Medium Slow        |  1.95 kbps  | 10 | 4/5         | 250 kHz   | 150.5 dB    |
| Long Turbo         |  1.34 kbps  | 11 | 4/8         | 500 kHz   | 150 dB      |
| **Long Fast**      |  **1.07 kbps**| **11** | **4/5** | **250 kHz** | **153 dB** |
| Long Moderate      |  0.34 kbps  | 11 | 4/8         | 125 kHz   | 156 dB      |
| Long Slow (depr.)  |  0.18 kbps  | 12 | 4/8         | 125 kHz   | 158.5 dB    |

**Long Fast** is the default and recommended preset (good balance of speed and range).

Link budget assumes 22 dBm TX power and 0 dB antenna gain.

---

## Key Technical Notes

- **SF5-SF6:** Supported only on 2nd generation chips; 1st gen limited to SF7-SF12
- **Encryption:** AES128 or AES256 pre-shared keys; can be disabled for ham radio operation
- **Short Turbo (500 kHz BW):** Not legal in all regions

---

## NodeBot config.ini Parameters

```ini
[meshtastic]
port = /dev/meshtastic0
baudrate = 115200
region = EU_868          # or US, AU_915, etc.
modem_preset = LONG_FAST
hop_limit = 7
tx_power = 0             # 0 = use device default
```
