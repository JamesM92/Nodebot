# LXMF — Lightweight Extensible Message Format

Source: https://raw.githubusercontent.com/markqvist/LXMF/master/README.md

---

## Overview

LXMF is a messaging protocol built on top of Reticulum, designed for minimal bandwidth while maintaining security. Enables messaging over low-bandwidth systems like packet radio and LoRa.

---

## Message Structure

Four core components:
1. **Destination** — Reticulum destination hash (16 bytes)
2. **Source** — Reticulum source address
3. **Ed25519 Signature** — 64 bytes
4. **Payload** — timestamp, content, title, optional fields dictionary

**Total overhead: 111 bytes**

All mandatory elements must be present; content, title, and fields may be empty.

---

## Identification

Messages are identified by SHA-256 hashes derived from destination, source, and payload data. The signature validates these components plus the message ID.

---

## Transport & Encryption

LXMF uses Reticulum's encryption layer:
- **AES-128 with ECDH on Curve25519** for link-based delivery
- Provides forward secrecy
- Messages can be encoded as QR codes or URI text for analog/out-of-band transport

---

## Infrastructure

- **`lxmd` daemon** — provides router and propagation node capabilities
- Installation via pip or pipx

## Client Implementations

- Sideband
- MeshChat
- Nomad Network

---

## Integration with NodeBot

NodeBot's `lxmf_adapter` connects to a running RNS instance (same host), then registers an LXMF destination. Incoming messages are routed through `engine.handle_message()`. Outbound messages use `adapter.send_message(bytes.fromhex(addr), text)` where `addr` is the 32-hex-char destination hash.

Relay addresses take the form `lxmf:<hex_destination_hash>` (e.g., `lxmf:abc123def456...`).
