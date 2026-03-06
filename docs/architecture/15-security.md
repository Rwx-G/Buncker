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
- Mnemonic: communicated once via human channel, never stored in cleartext
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

## Dependency Security
- Single dependency: `python3-cryptography` (Debian-maintained)
- Any new dependency requires explicit justification + Debian package availability

---
