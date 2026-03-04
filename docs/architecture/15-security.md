# 15. Security

## Input Validation
- Digest format: `^sha256:[a-f0-9]{64}$`
- Tag format: `^[a-zA-Z0-9._-]{1,128}$`
- Path traversal prevention on Dockerfile paths
- No eval, no shell execution of build-args

## Authentication
- V1: None on offline daemon (isolated LAN is the security perimeter)
- Online: registry credentials via env vars only, never plaintext in config

## Secrets Management
- Mnemonic: communicated once via human channel, never stored in cleartext
- Derived keys: in-memory only during execution, never written to disk
- Config stores only verification hashes and salts

## Data Protection
- Transfer files (USB): AES-256-GCM + HMAC-SHA256, always encrypted
- LAN: TLS optional, configurable
- Internet (buncker-fetch): HTTPS mandatory
- Store blobs: cleartext on disk (disk encryption is OS responsibility)

## Dependency Security
- Single dependency: `python3-cryptography` (Debian-maintained)
- Any new dependency requires explicit justification + Debian package availability

---
