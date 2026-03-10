# 15. Security

## Input Validation
- Digest format: `^sha256:[a-f0-9]{64}$`
- Tag format: `^[a-zA-Z0-9._-]{1,128}$`
- Path traversal prevention on Dockerfile paths (localhost only in V2 - remote sends content, not path)
- No eval, no shell execution of build-args
- `X-Buncker-Checksum` header validated before any decryption attempt

## Authentication

### Transfer channel (mnemonic)
- BIP-39 mnemonic shared once via human channel
- Derives AES + HMAC keys for USB transfer encryption
- Unchanged in V2

### Admin API (V2 - Bearer tokens)
- Activated by `buncker api-setup` (optional)
- Two cryptographically random tokens (256-bit, `secrets.token_hex(32)`)
  - **Read-only**: `status`, `logs`, `gc/report`
  - **Admin**: all `/admin/*` endpoints
- Token comparison: constant-time (`hmac.compare_digest`)
- Tokens stored in `/etc/buncker/api-tokens.json` (mode 0600)
- Token management: `buncker api-show|api-reset readonly|admin` (local CLI only)
- Failed auth logged with `auth_level: rejected` - no information leakage about token validity

### OCI Distribution API
- Always unauthenticated (`/v2/*`) - Docker clients pull without tokens
- Online: registry credentials via env vars only, never plaintext in config

## Secrets Management
- Mnemonic: communicated once via human channel, encrypted at rest in `/etc/buncker/env` using a key derived from `/etc/machine-id` (AES-256-GCM, PBKDF2 with 100,000 iterations). Protects against disk theft and unencrypted backups. Fallback to cleartext if machine-id is unavailable (development/testing)
- Derived keys: in-memory only during execution, never written to disk
- Config stores only verification hashes and salts
- API tokens: separate from mnemonic, stored in restricted file (0600), not derived from mnemonic
- Logs NEVER contain: mnemonic, derived keys, Bearer tokens, passwords

## Data Protection
- Transfer files (USB): AES-256-GCM + HMAC-SHA256, always encrypted
- LAN: TLS mandatory when API auth is enabled. HTTP allowed only without auth (local-only usage)
- Internet (buncker-fetch): HTTPS mandatory
- Store blobs: cleartext on disk (disk encryption is OS responsibility)
- TLS: operator-provided certificate (internal/external CA) or auto-signed with explicit security warning

## Network Binding
- Default bind address: `127.0.0.1` (localhost only)
- `buncker api-setup` switches to `0.0.0.0` (all interfaces) when activating API auth + TLS
- Before api-setup, the daemon is only reachable from the local machine (direct CLI mode)
- After api-setup, LAN clients can connect via HTTPS with Bearer tokens
- Operators can override bind address manually in config.json for custom network topologies

## Package Integrity (no GPG)
- Transfer files (.json.enc, .tar.enc) are protected by AES-256-GCM + HMAC-SHA256
- The `--deb` flag in `buncker-fetch fetch` bundles a .deb inside the encrypted response
- GPG signing of .deb packages is intentionally out of scope:
  - The offline machine has no internet access and cannot refresh GPG keyrings
  - Distributing and maintaining a GPG public key on air-gapped hosts adds operational complexity with limited security gain
  - The encrypted transfer already provides authenticity: only holders of the shared mnemonic can produce valid ciphertext
  - If the mnemonic is compromised, GPG would not help - the attacker can already forge transfer files
  - For environments requiring package-level signatures, operators should use their own GPG infrastructure outside of Buncker

## Dependency Security
- Dependencies: `python3-cryptography` and `python3-yaml` (Debian/RHEL-maintained)
- Any new dependency requires explicit justification + OS package availability

---
