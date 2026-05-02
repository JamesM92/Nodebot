# Meshtastic — Encryption

Source: https://meshtastic.org/docs/overview/encryption/

---

## Channel Encryption

**Algorithm:** AES-256-CTR

- Unique key per channel
- Packet headers remain unencrypted (nodes relay without decrypting)
- Default primary channel key: `"AQ=="` (well-known, provides no real security out of box)
- Users must change the key or create new channels for genuine privacy

All periodic broadcasts (position, telemetry, traceroutes) use the primary channel.

---

## Direct Messages (v2.5.0+)

**Algorithm:** Public Key Cryptography (PKC)

- Encrypted using recipient's public key
- Signed with sender's private key (identity verification + integrity)
- Only intended recipient can decrypt
- Significant upgrade from pre-2.5.0 (which used channel-based encryption for DMs)

---

## Admin Messages (v2.5.0+)

- Advanced encryption methods
- Unique Session IDs per administrative session
- Replay attack prevention

---

## Security Limitations

| Limitation                    | Detail                                                                                        |
|-------------------------------|-----------------------------------------------------------------------------------------------|
| No Perfect Forward Secrecy    | Vulnerable to "Harvest now, Decrypt later" if channel keys are compromised                    |
| No Integrity Verification     | Channel messages cannot be verified for tampering without the key                             |
| No Authentication             | Node IDs derive from hardware MAC addresses — trivial impersonation with channel key access   |
| Quantum Resistance            | AES256 is QR-resistant; PKC key exchange for DMs is not                                       |

---

## NodeBot Integration Notes

- Channel messages arrive with `channel_hash` field identifying which channel key was used
- DMs arrive encrypted to NodeBot's public key if peer uses v2.5+ PKC
- The `meshtastic_adapter` uses the `meshtastic` Python library which handles decryption automatically
