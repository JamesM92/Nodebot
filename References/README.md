# NodeBot — Protocol References

Copies of documentation and whitepapers for each transport protocol supported by NodeBot.
Fetched 2026-04-25. Re-fetch periodically as protocols evolve.

---

## Reticulum / LXMF

| File | Contents |
|------|----------|
| [reticulum/reticulum_technical.md](reticulum/reticulum_technical.md) | Cryptographic primitives (Ed25519, X25519, AES-256-CBC, HMAC-SHA256, HKDF), address format (16-byte SHA-256 hash), destination types, packet structure, transport/routing mechanics, link establishment handshake (3-packet, 297 bytes), IFAC, design constraints |
| [reticulum/lxmf_spec.md](reticulum/lxmf_spec.md) | LXMF message structure (111-byte overhead), Ed25519 64-byte signature, SHA-256 message ID, AES-128/Curve25519 transport encryption, lxmd daemon, NodeBot integration notes |

**Key facts for NodeBot:**
- LXMF addresses are 32-hex-char destination hashes (16 bytes)
- Relay prefix: `lxmf:<hex_hash>`
- NodeBot connects to a running RNS instance (service/client model)
- Max packet payload: 465 bytes; no NodeBot-side splitting needed for typical messages

---

## Meshtastic

| File | Contents |
|------|----------|
| [meshtastic/meshtastic_overview.md](meshtastic/meshtastic_overview.md) | What it is, how relay works, channel system (up to 8), mesh structure |
| [meshtastic/mesh_algorithm.md](meshtastic/mesh_algorithm.md) | Layer 0-3, 16-byte packet header format, CSMA/CA, managed flooding, SNR-based rebroadcast, next-hop routing (v2.6+), broadcast interval scaling |
| [meshtastic/encryption.md](meshtastic/encryption.md) | AES-256-CTR for channels, PKC (DM, v2.5+), Admin session security, security limitations (no PFS, no auth, no integrity on channels) |
| [meshtastic/radio_settings.md](meshtastic/radio_settings.md) | Frequency bands by region, modem preset table (SF/BW/CR/link budget), config.ini parameters |

**Key facts for NodeBot:**
- Relay prefix: `mesh:<node_id>`
- Message size limit: 237 bytes encrypted payload
- Long Fast preset: 1.07 kbps, SF11, 4/5 CR, 250 kHz BW, 153 dB link budget
- Channel keys: AES-256-CTR; default key `"AQ=="` = no real security

---

## MeshCore

| File | Contents |
|------|----------|
| [meshcore/meshcore_overview.md](meshcore/meshcore_overview.md) | C++ firmware library overview, Python client (pymc-core), three-layer architecture, connection API, event types (CHANNEL_MSG_RECV, RX_LOG_DATA, CONTACT_MSG_RECV), channel key loading, contact lookup, NodeBot integration notes |

**Key facts for NodeBot:**
- Relay prefix: `mc:<pubkey_prefix>` (e.g., `mc:091733a4`)
- Message size limit: 190 bytes (practical MeshCore limit)
- `CHANNEL_MSG_RECV` only fires when device has channel key; use `RX_LOG_DATA` as fallback
- Call `get_channel(idx)` on startup + `set_decrypt_channel_logs(True)` to enable library decryption of GRP_TXT in LOG_DATA
- Dedup key between both paths: `(sender_timestamp, text[:32])`

---

