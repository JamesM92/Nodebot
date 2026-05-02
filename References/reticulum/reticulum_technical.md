# Reticulum Network Stack — Technical Reference

Source: https://reticulum.network/manual/understanding.html

---

## Cryptographic Primitives

- **Signatures:** Ed25519
- **Key Exchange:** X25519 (ECDH)
- **Encryption:** AES-256 in CBC mode with PKCS7 padding
- **Message Authentication:** HMAC-SHA256
- **Hashing:** SHA-256, SHA-512
- **Key Derivation:** HKDF
- **Token encryption:** Fernet spec with ephemeral Curve25519 ECDH-derived keys

Implementation defaults to OpenSSL via PyCA/cryptography. Pure-Python fallback available but slower.

---

## Address Format

- 16-byte hash derived from SHA-256 truncation
- Displayed as 16 hex bytes: `<13425ec15b621c1d928589718000d814>`
- Naming: dotted aspect notation (e.g., `environmentlogger.remotesensor.temperature`)
- Single destinations append public key before hashing to ensure uniqueness

---

## Destination Types

| Type   | Encryption                        | Multi-hop | Use Case                          |
|--------|-----------------------------------|-----------|-----------------------------------|
| Single | Per-packet ephemeral ECDH         | Yes       | Private peer-to-peer              |
| Plain  | None                              | No        | Broadcast/public                  |
| Group  | Symmetric pre-shared AES-256 key  | No        | Multi-recipient private           |
| Link   | Negotiated symmetric via ECDH     | Yes       | Bidirectional sessions w/ FS      |

---

## Packet Structure

```
[HEADER 2 bytes] [ADDRESSES 16/32 bytes] [CONTEXT 1 byte] [DATA 0-465 bytes]
```

**Header Byte 1 Flags:**
- IFAC Flag (interface authentication)
- Header Type (1 or 2 address fields)
- Context Flag
- Propagation Type (broadcast/transport)
- Destination Type (single/group/plain/link)
- Packet Type (data/announce/link request/proof)

**Header Byte 2:** Hop count

### Packet Type Sizes (on-wire, excluding IFACs)

| Type           | Size     |
|----------------|----------|
| Path Request   | 51 bytes |
| Announce       | 167 bytes|
| Link Request   | 83 bytes |
| Link Proof     | 115 bytes|
| Link RTT       | 99 bytes |
| Link keepalive | 20 bytes |

---

## Transport & Routing

**Node Classification:**
- **Instance:** `enable_transport = No` — relies on transport nodes for wide connectivity
- **Transport Node:** `enable_transport = Yes` — forwards traffic, maintains routing tables

**Announce Propagation:**
- Forwarded by transport nodes with increasing hop count (max 128 hops)
- Bandwidth allocation: default 2% per interface for announce processing
- Retry: up to 1 additional transmission if no node retransmits with greater hop count
- Duplicate detection prevents re-forwarding

**Path Establishment:** No single node has complete path knowledge; each knows only the next hop.

---

## Link Establishment (3-Packet Handshake — 297 bytes total)

1. **Link Request:** Initiator generates X25519 keypair, broadcasts request with public key `LKi`
2. **Link Proof:** Destination accepts, generates X25519 keypair, performs ECDH, returns Ed25519-signed proof containing `LKr`
3. **Verification:** Forwarding nodes verify proof, activate link; initiator confirms

**Security properties:**
- Initiator anonymous throughout handshake
- Forward secrecy via ephemeral per-link keys
- Message integrity via Ed25519 signatures
- Transparent over multiple hops

**Bandwidth cost:**
- Setup: 297 bytes one-time
- Maintenance: 0.45 bits/second per active link
- 100 concurrent links ≈ 4% of a 1200 bps channel

---

## Interface Access Codes (IFAC)

Named virtual networks or passphrase-protected interfaces derive an Ed25519 signing identity. Each outbound packet gets a signature (1-64 bytes). Inbound packets are verified; mismatched signatures dropped.

---

## Design Constraints

| Parameter                      | Value                         |
|-------------------------------|-------------------------------|
| Minimum link throughput        | 5 bits/second average         |
| Physical layer MTU             | 500 bytes                     |
| Maximum packet payload         | 465 bytes                     |
| Address space                  | 128 bits (upgradeable to 256) |
| Default max announce hops      | 128                           |
| Default announce bandwidth     | 2% per interface              |
