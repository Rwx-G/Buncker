# 12. Error Handling Strategy

## Exception Hierarchy

```python
class BunckerError(Exception): ...
class ConfigError(BunckerError): ...
class CryptoError(BunckerError): ...
class StoreError(BunckerError): ...
class ResolverError(BunckerError): ...
class RegistryError(BunckerError): ...
class TransferError(BunckerError): ...
```

## Key Principles

- **Atomic writes:** temp file + SHA256 verify + rename. Never corrupt the store.
- **Actionable errors:** Every error message includes what failed, context, and what to do.
- **Retry policy (online):** 3 attempts, exponential backoff (1s, 3s, 9s). Connect 30s, read 120s.
- **Partial import:** Valid blobs are kept. Failed blobs are reported. Operator can retry.
- **Idempotent:** Importing the same blob twice = noop (same digest = same file).

## Logging

- **Format:** JSON Lines, append-only
- **Levels:** DEBUG, INFO, WARNING, ERROR
- **Events:** `dockerfile_analyzed`, `transfer_manifest_generated`, `transfer_imported`, `blob_pulled`, `blob_missing`, `gc_candidate`, `gc_executed`, `key_rotation`, `api_auth_rejected`, `api_token_reset`, `api_setup_completed`
- **V2 fields on API requests:** `client_ip`, `auth_level` (`admin`, `readonly`, `local`, `rejected`), `user_agent`
- **Never log:** mnemonic, derived keys, Bearer tokens, passwords

---
