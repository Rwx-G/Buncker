<p align="center">
  <h1 align="center">Buncker</h1>
  <p align="center">Bunker meets Docker - encrypted delta sync for air-gapped builds.</p>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.11-3776AB.svg?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/platform-Debian%2FUbuntu-A81D33.svg?logo=debian&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/packaging-.deb-orange.svg" alt="Packaging">
  <img src="https://img.shields.io/badge/status-v0.6.1-brightgreen.svg" alt="Status">
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

## Installation

### From .deb packages (recommended)

Download the latest `.deb` files from [GitHub Releases](https://github.com/Rwx-G/Buncker/releases):

```bash
# Offline machine
sudo dpkg -i buncker_0.6.1_all.deb

# Online machine
sudo dpkg -i buncker-fetch_0.6.1_all.deb
```

If dependencies are missing, fix them with:

```bash
sudo apt-get install -f
```

### From source (development)

```bash
git clone https://github.com/Rwx-G/Buncker.git
cd Buncker
pip install ruff pytest cryptography   # dev dependencies
make build-deb                         # build .deb packages to dist/
sudo dpkg -i dist/buncker_*_all.deb
sudo dpkg -i dist/buncker-fetch_*_all.deb
```

## Quick Start

**1. Offline machine - setup**

```bash
sudo buncker setup
```

Expected output:

```
Buncker initialized successfully.

IMPORTANT: Write down the following 12-word mnemonic.
This is the ONLY time it will be displayed.
You need it to start the daemon and for key recovery.

  abandon ability able about above absent absorb abstract absurd abuse access accident

Config: /etc/buncker/config.json
Store:  /var/lib/buncker
```

Start the daemon:

```bash
sudo systemctl enable --now buncker
```

**2. Analyze a Dockerfile and generate a transfer request**

```bash
buncker analyze ./Dockerfile --build-arg NODE_VERSION=20
```

Expected output:

```json
{
  "images": ["docker.io/library/node:20-bookworm-slim"],
  "missing_blobs": 12,
  "total_size": 48230400
}
```

```bash
buncker generate-manifest --output /media/usb/request.json.enc
```

Expected output:

```
Transfer request saved to /media/usb/request.json.enc
```

**3. Online machine - fetch**

```bash
buncker-fetch pair
```

Expected output:

```
Enter the 12-word mnemonic (space-separated):
> abandon ability able about above absent absorb abstract absurd abuse access accident
  status: success
  message: Pairing successful
```

```bash
buncker-fetch fetch /media/usb/request.json.enc --output /media/usb/
```

Expected output:

```
  status: success
  downloaded: 12
  skipped: 0
  errors: 0
  response_file: /media/usb/buncker-response.tar.enc
```

**4. Back offline - import and build**

```bash
buncker import /media/usb/buncker-response.tar.enc
```

Expected output:

```json
{
  "imported": 12,
  "skipped": 0,
  "errors": []
}
```

```bash
docker build -t myapp .     # works without internet
```

## Configuration

| Side | Config file |
|------|-------------|
| Offline daemon | `/etc/buncker/config.json` |
| Online CLI | `~/.buncker/config.json` |
| Docker clients | See [Docker Client Setup](#docker-client-setup) below |

### Docker Client Setup

Docker clients on the offline LAN need to pull images from the buncker
registry instead of Docker Hub. There are two approaches:

**Approach 1 - Explicit registry in Dockerfiles (simplest)**

Reference the buncker host directly in your `FROM` instructions:

```dockerfile
FROM buncker-host:5000/library/alpine:3.19
FROM buncker-host:5000/library/python:3.11-slim
```

Add the registry as insecure (HTTP) in `/etc/docker/daemon.json`:

```json
{
  "insecure-registries": ["buncker-host:5000"]
}
```

Restart Docker after editing: `sudo systemctl restart docker`

This is the most reliable approach. Replace `buncker-host` with the
actual hostname or IP of the buncker daemon on your offline LAN.

**Approach 2 - Registry mirror (transparent, Docker 20.10+)**

Configure Docker to use buncker as a pull-through mirror.
Dockerfiles keep standard `FROM alpine:3.19` syntax.

In `/etc/docker/daemon.json`:

```json
{
  "registry-mirrors": ["http://buncker-host:5000"],
  "insecure-registries": ["buncker-host:5000"]
}
```

Restart Docker after editing: `sudo systemctl restart docker`

With this setup, `docker pull alpine:3.19` checks buncker first.
Note: registry mirrors only work for Docker Hub (`docker.io`) images.
Images from other registries (ghcr.io, quay.io) still need explicit
references as in Approach 1.

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

## Command Reference

### `buncker` (offline daemon)

| Command | Description |
|---------|-------------|
| `buncker setup` | Initialize: generate mnemonic, create config, init store |
| `buncker serve` | Start the HTTP daemon (reads mnemonic from `BUNCKER_MNEMONIC` env or stdin) |
| `buncker analyze <Dockerfile>` | Analyze Dockerfile and identify missing blobs |
| `buncker generate-manifest` | Generate an encrypted transfer request |
| `buncker import <file.tar.enc>` | Import an encrypted transfer response |
| `buncker status` | Show registry status (blob count, store size) |
| `buncker gc --report` | List inactive blobs eligible for garbage collection |
| `buncker gc --execute` | Delete reported inactive blobs |
| `buncker rotate-keys` | Generate a new mnemonic and deprecate old keys |
| `buncker export-ca` | Print CA certificate to stdout (TLS mode only) |

**Flags:**

| Flag | Description |
|------|-------------|
| `--config <path>` | Config file path (default: `/etc/buncker/config.json`) |
| `--build-arg KEY=VALUE` | Build argument for `analyze` (repeatable) |
| `--inactive-days N` | GC inactivity threshold (default: 90) |
| `--operator <name>` | Operator name for GC audit trail |
| `--grace-period N` | Key rotation grace period in days (default: 30) |

### `buncker-fetch` (online CLI)

| Command | Description |
|---------|-------------|
| `buncker-fetch pair` | Enter 12-word mnemonic and derive encryption keys |
| `buncker-fetch inspect <file.json.enc>` | Decrypt and display transfer request summary |
| `buncker-fetch fetch <file.json.enc>` | Fetch missing blobs and build encrypted response |
| `buncker-fetch status` | Display cache statistics |
| `buncker-fetch cache clean` | Remove old cached blobs |

**Flags:**

| Flag | Description |
|------|-------------|
| `--json` | Machine-readable JSON output |
| `--config <path>` | Config file path (default: `~/.buncker/config.json`) |
| `--output <path>` | Output directory for `fetch` response |
| `--parallelism N` | Parallel downloads for `fetch` (default: 4) |
| `--older-than Nd` | Cache clean threshold (default: `30d`) |

## Maintenance

### Key rotation

Generate a new mnemonic when compromised or as periodic security practice:

```bash
sudo buncker rotate-keys --grace-period 30
# Write down the new 12-word mnemonic
# Restart daemon with new mnemonic
sudo systemctl restart buncker
# Re-pair online machine
buncker-fetch pair
```

Old keys remain valid during the grace period for in-flight transfers.

### Garbage collection

Remove blobs that have not been referenced in recent transfers:

```bash
# Preview candidates
buncker gc --report --inactive-days 90

# Execute cleanup
buncker gc --execute --operator "admin"
```

### Cache management

On the online machine, manage the local blob cache:

```bash
# Check cache usage
buncker-fetch status

# Clean blobs older than 30 days
buncker-fetch cache clean --older-than 30d
```

### Logs

Buncker writes structured JSON Lines logs:

```bash
# Daemon logs
sudo tail -f /var/log/buncker/buncker.log

# Or via journalctl
sudo journalctl -u buncker -f
```

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `dpkg: dependency problems` | Missing Python or cryptography | `sudo apt-get install -f` |
| `Cannot connect to buncker daemon` | Daemon not running | `sudo systemctl start buncker` and check `journalctl -u buncker` |
| `mnemonic does not match config` | Wrong mnemonic entered | Re-enter the correct 12 words from initial setup |
| `Mnemonic verification failed` on fetch | Mnemonic or salt mismatch | Re-run `buncker-fetch pair` with the correct mnemonic |
| `buncker setup` fails with "Config already exists" | Previous setup detected | Back up and remove `/etc/buncker/config.json` to re-initialize |
| Blobs not found after import | OCI store path mismatch | Verify `store_path` in config matches daemon working directory |
| `docker build` fails after import | Daemon not serving imported blobs | Check `buncker status` and verify daemon is running |
| High disk usage on online machine | Blob cache growing | Run `buncker-fetch cache clean --older-than 7d` |

## Development

```bash
git clone https://github.com/Rwx-G/Buncker.git
cd Buncker
pip install ruff pytest cryptography

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

[Apache 2.0](LICENSE) - Copyright 2026 Rwx-G.
