# Meshtastic — Overview

Source: https://meshtastic.org/docs/overview/

---

## What It Is

Meshtastic is a mesh networking system built on LoRa radio technology. Enables decentralized communication through multiple connected radio nodes that relay messages across distances — no internet required.

## How It Works

1. User sends a message via companion app (Bluetooth, Wi-Fi/Ethernet, or serial)
2. Radio broadcasts the message
3. Receiving radios check for duplicate packets and rebroadcast unfamiliar ones
4. Each hop reduces the hop limit by one until it reaches zero
5. Up to 3 retransmission attempts if no confirmation received

## Message Storage

Devices maintain approximately 30 packets in memory. When capacity is reached, older text-only messages are replaced with new ones.

## Mesh Structure

Two levels:
- **Radio mesh:** Defined by matching LoRa parameters (spreading factor, center frequency, bandwidth)
- **Logical channels:** Layered above radio mesh; up to 8 simultaneous channels per node

## Channel System

- Up to 8 channels per node, each with distinct name and encryption key
- Only nodes with matching channel config can read specific messages
- All mesh nodes receive and retransmit regardless of channel settings (relay without decrypt)

## Use Cases

- Off-grid communication
- Emergency/disaster scenarios where infrastructure is unavailable
- Protest/event communication
- Remote area coverage
- IoT sensor networks
