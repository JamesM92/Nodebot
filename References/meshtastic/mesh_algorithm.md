# Meshtastic — Mesh Algorithm

Source: https://meshtastic.org/docs/overview/mesh-algo/

---

## Layer 0: LoRa Radio (PHY)

Data conversion to LoRa symbols:
- Preamble: length 16 (for receiver synchronization)
- Physical Header: packet length + sync word `0x2B` (network identifier)

---

## Layer 1: Unreliable Zero-Hop Messaging

### Packet Structure (16 bytes header + up to 237 bytes payload)

| Offset | Length   | Type    | Field                                  |
|--------|----------|---------|----------------------------------------|
| 0x00   | 4 bytes  | Integer | Destination NodeID (`0xFFFFFFFF` = broadcast) |
| 0x04   | 4 bytes  | Integer | Sender NodeID                          |
| 0x08   | 4 bytes  | Integer | Packet ID (sender-unique)              |
| 0x0C   | 1 byte   | Bits    | Flags (see below)                      |
| 0x0D   | 1 byte   | Bits    | Channel hash (decryption hint)         |
| 0x0E   | 1 byte   | Bytes   | Next-hop for relaying                  |
| 0x0F   | 1 byte   | Bytes   | Relay node of current transmission     |
| 0x10   | ≤237 B   | Bytes   | Encrypted protobuf payload             |

### Packet Header Flags Byte (0x0C)

| Bits  | Field      | Notes                        |
|-------|------------|------------------------------|
| 0-2   | HopLimit   | Remaining hops               |
| 3     | WantAck    | Request acknowledgment       |
| 4     | ViaMQTT    | Arrived via MQTT gateway     |
| 5-7   | HopStart   | Original hop limit value     |

### CSMA/CA

All transmitters perform Channel Activity Detection (CAD) before transmitting. When channel becomes idle, nodes wait a random multiple of slot times from a contention window sized based on channel utilization.

---

## Layer 2: Reliable Zero-Hop Messaging

`WantAck` flag enables reliable messaging between immediate neighbors.

- Broadcast messages: original sender listens for rebroadcasts as implicit ACK (not individual ACKs which would flood channel)
- Reattempts: up to 3 times before generating a NAK

---

## Layer 3: Multi-Hop Messaging

### Managed Flooding (Broadcasts)

Every node rebroadcasts received packets up to the hop limit. Before rebroadcasting, a node listens briefly to see if another node has already rebroadcast it.

**SNR-Based Rebroadcasting:**
- Contention window size depends on Signal-to-Noise Ratio
- Lower SNR (farther nodes) → smaller contention window → transmit first
- Exception: `ROUTER` and `REPEATER` roles prioritize rebroadcasting regardless of hearing other nodes

### Next-Hop Routing (Direct Messages, v2.6+)

1. Initially uses managed flooding to reach destination
2. Tracks which nodes relayed the successful delivery
3. On confirmed delivery, identified relay nodes become designated next-hops
4. Subsequent messages restricted to matching relay nodes only
5. Fallback to managed flooding on final retry if next-hop relay not heard

---

## Regular Broadcast Intervals

| Traffic Type      | Config Key                              | Default    |
|-------------------|-----------------------------------------|------------|
| Device Telemetry  | `telemetry.device_update_interval`      | 30 minutes |
| Position          | `position.position_broadcast_secs`      | 15 minutes |
| NodeInfo          | `device.node_info_broadcast_secs`       | 3 hours    |

### Scaling Algorithm (40+ nodes)

```
ScaledInterval = Interval × (1.0 + ((NumberOfOnlineNodes - 40) × 0.075))
```

Example: 62-node mesh scales telemetry from 30 → 79.5 minutes.
