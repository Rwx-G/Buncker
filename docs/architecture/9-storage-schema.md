# 9. Storage Schema

## Store offline (`/var/lib/buncker/`)

```
/var/lib/buncker/
├── oci-layout
├── index.json
├── blobs/sha256/
│   ├── a1b2c3d4e5...
│   └── ...
├── meta/sha256/
│   ├── a1b2c3d4e5...json
│   └── ...
├── manifests/{registry}/{repo}/{tag}/{platform}.json
└── logs/buncker.jsonl
```

## Config offline (`/etc/buncker/config.json`)

```json
{
  "source_id": "buncker-prod-01",
  "bind": "0.0.0.0",
  "port": 5000,
  "store_path": "/var/lib/buncker",
  "max_workers": 16,
  "tls": false,
  "crypto": { "salt": "base64...", "mnemonic_hash": "sha256:..." },
  "api": { "enabled": false },
  "private_registries": ["registry.internal", "localhost:*"],
  "gc": { "inactive_days_threshold": 90 },
  "log_level": "INFO"
}
```

When `api.enabled: true` (after `buncker api-setup`), `tls` is also set to `true`.

## API tokens (`/etc/buncker/api-tokens.json`, mode 0600)

```json
{
  "readonly": "hex-encoded-256-bit-token",
  "admin": "hex-encoded-256-bit-token"
}
```

This file only exists after `buncker api-setup`. It is never readable by non-root users.

## Cache online (`~/.buncker/`)

```
~/.buncker/
├── config.json
├── cache/blobs/sha256/
└── logs/fetch.jsonl
```

---
