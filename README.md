<p align="center">
  <h1 align="center">Buncker</h1>
  <p align="center">Bunker meets Docker - encrypted delta sync for air-gapped builds.</p>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.11-3776AB.svg?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/platform-Debian%2FUbuntu-A81D33.svg?logo=debian&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/packaging-.deb-orange.svg" alt="Packaging">
  <img src="https://img.shields.io/badge/status-v0.5.0-brightgreen.svg" alt="Status">
</p>

---

Buncker analyzes Dockerfiles, identifies missing layers in your offline store, and transfers only the delta via encrypted USB - no bulk snapshots, no internet fallback, no magic.

Nothing equivalent exists: [Hauler](https://github.com/hauler-dev/hauler) does bulk snapshot & ship but has no Dockerfile resolver and no delta approach.

## How It Works

```
 OFFLINE (isolated LAN)             USB             ONLINE (connected machine)

┌───────────────────────┐    ┌────────────────┐    ┌──────────────────────────┐
│  buncker daemon       │    │  request.enc   │    │  buncker-fetch CLI       │
│                       │───>│  (AES-256-GCM) │───>│                          │
│  1. Analyze Dockerfile│    └────────────────┘    │  3. Decrypt + verify     │
│  2. Diff missing blobs│                          │  4. Fetch delta blobs    │
│                       │    ┌────────────────┐    │  5. Build response       │
│  7. Import + verify   │<───│  response.enc  │<───│                          │
│  8. Serve via OCI API │    │  (AES-256-GCM) │    └──────────────────────────┘
└───────────────────────┘    └────────────────┘
```

## Key Features

| Feature | Description |
|---------|-------------|
| **Delta sync** | Only missing layers are transferred, not entire images |
| **Dockerfile resolver** | Static analysis of `FROM`, `ARG`, multi-stage, multi-arch |
| **Encrypted transfers** | AES-256-GCM + HMAC-SHA256 on all USB files |
| **BIP-39 mnemonic** | 12-word shared secret, no PKI to manage |
| **OCI standard** | Local registry compatible with `docker pull`, no client changes |
| **Full audit trail** | Every operation logged in structured JSON Lines |
| **Zero exotic deps** | Python stdlib + `python3-cryptography` (apt) |

## Components

| Component | Role | Packaging |
|-----------|------|-----------|
| **`buncker`** | Offline HTTP daemon - OCI registry + admin API | `.deb` (systemd) |
| **`buncker-fetch`** | Online CLI - fetch blobs from public registries | `.deb` |

## Requirements

- Debian 12+ / Ubuntu 22.04+
- Python >= 3.11
- `python3-cryptography` (installed via apt, not pip)

## Quick Start

**1. Offline machine - setup**

```bash
sudo dpkg -i buncker.deb
sudo buncker setup          # generates 12 mnemonic words - write them down
sudo systemctl enable --now buncker
```

**2. Analyze & generate request**

```bash
buncker analyze ./Dockerfile --build-arg NODE_VERSION=20
buncker generate-manifest --output /media/usb/request.json.enc
```

**3. Online machine - fetch**

```bash
sudo dpkg -i buncker-fetch.deb
buncker-fetch pair           # enter the 12 mnemonic words
buncker-fetch fetch /media/usb/request.json.enc --output /media/usb/response.tar.enc
```

**4. Back offline - import & build**

```bash
buncker import /media/usb/response.tar.enc
docker build -t myapp .     # works without internet
```

## Configuration

| Side | Config file |
|------|-------------|
| Offline daemon | `/etc/buncker/config.json` |
| Online CLI | `~/.buncker/config.json` |
| Docker clients | `/etc/docker/certs.d/docker.io/hosts.toml` |

## Configuration Reference

### Offline daemon (`/etc/buncker/config.json`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source_id` | string | `""` | Unique identifier for this buncker instance |
| `bind` | string | `"0.0.0.0"` | Listen address |
| `port` | int | `5000` | Listen port |
| `store_path` | string | `"/var/lib/buncker"` | OCI blob store directory |
| `max_workers` | int | `16` | Thread pool size for HTTP server |
| `tls` | bool | `false` | Enable HTTPS (self-signed CA) |
| `crypto.salt` | string | - | Base64-encoded PBKDF2 salt (set by `buncker setup`) |
| `crypto.mnemonic_hash` | string | - | SHA256 hash of mnemonic for verification |
| `private_registries` | list | `[]` | Private registry patterns to skip |
| `gc.inactive_days_threshold` | int | `90` | GC inactivity threshold in days |
| `log_level` | string | `"INFO"` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |

### Online CLI (`~/.buncker/config.json`)

| Field | Type | Description |
|-------|------|-------------|
| `salt` | string | Base64-encoded PBKDF2 salt (set by `buncker-fetch pair`) |
| `derived_key_check` | string | Encrypted marker for mnemonic verification |

## Development

### Setup

```bash
git clone https://github.com/Rwx-G/Buncker.git
cd Buncker
pip install ruff pytest    # dev dependencies only
```

### Commands

```bash
make lint       # ruff check + format verification
make test       # run pytest suite
make build-deb  # build .deb packages to dist/
make clean      # remove build artifacts
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full development setup and guidelines.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Components, workflows, API spec, data models, tech stack |
| [PRD](docs/prd.md) | Requirements, epics, stories, success metrics |
| [Contributing](CONTRIBUTING.md) | Dev setup, commit convention, branching, testing |
| [Changelog](CHANGELOG.md) | Release history (Keep a Changelog + SemVer) |
| [Security](SECURITY.md) | Vulnerability reporting policy |

## License

[Apache 2.0](LICENSE) - Copyright 2026 Romain G.
