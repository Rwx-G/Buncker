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
  "private_registries": ["registry.internal", "localhost:*"],
  "gc": { "inactive_days_threshold": 90 },
  "log_level": "INFO"
}
```

## Cache online (`~/.buncker/`)

```
~/.buncker/
├── config.json
├── cache/blobs/sha256/
└── logs/fetch.jsonl
```

---
