<p align="center">
  <h1 align="center">Buncker</h1>
  <p align="center">Bunker meets Docker - encrypted delta sync for air-gapped builds.</p>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.11-3776AB.svg?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/platform-Debian%20%7C%20RHEL%20%7C%20Fedora-A81D33.svg?logo=linux&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/packaging-.deb%20%7C%20.rpm-orange.svg" alt="Packaging">
  <img src="https://img.shields.io/badge/status-v1.0.2-brightgreen.svg" alt="Status">
  <img src="https://img.shields.io/badge/coverage-%E2%89%A590%25-brightgreen.svg" alt="Coverage">
</p>

---

Buncker analyzes Dockerfiles, identifies missing layers in your offline store, and transfers only the delta via encrypted USB - no bulk snapshots, no internet fallback, no magic.

Nothing equivalent exists: [Hauler](https://github.com/hauler-dev/hauler) does bulk snapshot & ship but has no Dockerfile resolver and no delta approach.

## How It Works

Buncker supports two operating modes depending on your setup:

- **Direct mode** - the operator works directly on the buncker server via CLI
- **LAN client mode** - the operator works from any machine on the offline LAN via `curl` + Bearer tokens (no SSH needed)

Both modes use the same transfer pipeline: encrypted request out, blobs fetched online, encrypted response back.

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
| **BIP-39 mnemonic** | 16-word shared secret, no PKI to manage |
| **OCI standard** | Local registry compatible with `docker pull`, no client changes |
| **Full audit trail** | Every operation logged in structured JSON Lines |
| **Compose support** | Analyze `docker-compose.yml` to resolve all service images at once |
| **Manifest staleness** | Configurable TTL warns when cached manifests are outdated, `--refresh-stale` re-fetches them |
| **OS-packaged deps only** | Python stdlib + `python3-cryptography` + `python3-yaml` (apt/dnf) |

## Components

| Component | Role | Packaging |
|-----------|------|-----------|
| **`buncker`** | Offline HTTP daemon - OCI registry + admin API | `.deb` / `.rpm` (systemd) |
| **`buncker-fetch`** | Online CLI - fetch blobs from public registries | `.deb` / `.rpm` |

## Requirements

- Debian 12+ / Ubuntu 22.04+ or RHEL 9+ / Fedora 38+
- Python >= 3.11
- `python3-cryptography` and `python3-yaml` (installed via apt/dnf, not pip)

> **Windows / macOS**: Buncker requires Linux. On Windows, use
> [WSL2](https://learn.microsoft.com/en-us/windows/wsl/) with a Debian or
> Ubuntu distribution. There is no native Windows or macOS build.

## Installation

### From .deb packages (Debian/Ubuntu)

Download the latest `.deb` files from [GitHub Releases](https://github.com/Rwx-G/Buncker/releases):

```bash
# Offline machine
sudo dpkg -i buncker_1.0.1_all.deb

# Online machine
sudo dpkg -i buncker-fetch_1.0.1_all.deb
```

If dependencies are missing, fix them with:

```bash
sudo apt-get install -f
```

### From .rpm packages (RHEL/Fedora)

Download the latest `.rpm` files from [GitHub Releases](https://github.com/Rwx-G/Buncker/releases):

```bash
# Offline machine
sudo dnf install buncker-1.0.1-1.noarch.rpm

# Online machine
sudo dnf install buncker-fetch-1.0.1-1.noarch.rpm
```

### From source (development)

```bash
git clone https://github.com/Rwx-G/Buncker.git
cd Buncker
pip install ruff pytest cryptography pyyaml  # dev dependencies
make build-deb                         # build .deb packages to dist/
sudo dpkg -i dist/buncker_*_all.deb
sudo dpkg -i dist/buncker-fetch_*_all.deb
```

## Quick Start

### Initial setup (buncker server - offline machine)

This step is the same for both operating modes.

```bash
sudo buncker setup
```

Expected output:

```
[1/4] Generating cryptographic keys...  done
[2/4] Initializing store...             done
[3/4] Saving configuration...           done
[4/4] Enabling and starting daemon...   done

============================================================

  IMPORTANT - Write down your 16-word recovery mnemonic.
  This is the ONLY time it will be displayed.

  pride evoke tumble stool coach enact lazy ribbon
  silent split orphan peace flavor broom render desk

  Config:  /etc/buncker/config.json
  Store:   /var/lib/buncker
  Daemon:  active on 127.0.0.1:5000

============================================================
```

Setup automatically enables and starts the daemon via systemd. The mnemonic
is also saved to `/etc/buncker/env` (mode 0600) so the service can restart
without manual re-entry.

---

### Mode 1: Direct (CLI on buncker server)

The operator works directly on the buncker server. Transfers go through USB.

**1. Prepare a transfer request from a Dockerfile**

```bash
buncker prepare ./Dockerfile --build-arg NODE_VERSION=20 --output /media/usb/
# Combines analyze + generate-manifest in one step
```

This analyzes the Dockerfile, identifies missing layers, and writes the
encrypted transfer request to the USB drive in a single command.
You can also run `buncker analyze` and `buncker generate-manifest` separately
if you need to inspect the analysis before generating the request.

**2. Online machine - pair and fetch**

```bash
buncker-fetch pair
# Enter the 16-word mnemonic when prompted

buncker-fetch fetch /media/usb/request.json.enc --output /media/usb/
```

**3. Back offline - import and build**

```bash
buncker import /media/usb/buncker-response.tar.enc
docker build -t myapp .     # works without internet
```

---

### Mode 2: LAN client (curl + Bearer tokens)

The operator works from any machine on the isolated LAN - no SSH to the
buncker server needed. All admin operations go through HTTP with Bearer
token authentication.

**1. Enable API auth on the buncker server**

```bash
sudo buncker api-setup
# Generates admin + readonly tokens, activates TLS
# Displays cert fingerprint and copies CA to /etc/buncker/ca.pem

# Distribute CA certificate to LAN clients
scp /etc/buncker/ca.pem user@client:~/buncker-ca.pem

# Show the admin token (if needed later)
buncker api-show admin
```

**2. From LAN client - analyze and generate transfer request**

```bash
# Analyze a Dockerfile (send content, not a file path)
curl -X POST -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"dockerfile_content": "FROM alpine:3.19\nRUN apk add curl"}' \
  https://buncker:5000/admin/analyze --cacert buncker-ca.pem

# Download the encrypted transfer request
curl -X POST -H "Authorization: Bearer <admin-token>" \
  -o request.json.enc \
  https://buncker:5000/admin/generate-manifest --cacert buncker-ca.pem
```

**3. Online machine - pair and fetch** (same as direct mode)

```bash
buncker-fetch pair
# Enter the 16-word mnemonic when prompted

buncker-fetch fetch request.json.enc --output ./
```

**4. From LAN client - upload response with checksum verification**

```bash
CHECKSUM=$(sha256sum buncker-response.tar.enc | cut -d' ' -f1)
curl -T buncker-response.tar.enc \
  -H "Authorization: Bearer <admin-token>" \
  -H "X-Buncker-Checksum: sha256:$CHECKSUM" \
  https://buncker:5000/admin/import --cacert buncker-ca.pem
```

**5. Build** (from any Docker host on the LAN)

```bash
docker build -t myapp .     # pulls from buncker registry, no internet
```

**Check status** (read-only token is sufficient):

```bash
curl -H "Authorization: Bearer <readonly-token>" \
  https://buncker:5000/admin/status --cacert buncker-ca.pem
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
| `bind` | string | `"127.0.0.1"` | Listen address (localhost only; `api-setup` switches to `0.0.0.0` for LAN access) |
| `port` | int | `5000` | Listen port |
| `store_path` | string | `"/var/lib/buncker"` | OCI blob store directory |
| `max_workers` | int | `16` | Thread pool size for HTTP server |
| `tls` | bool | `false` | Enable HTTPS (self-signed CA) |
| `crypto.salt` | string | - | Base64-encoded PBKDF2 salt (set by `buncker setup`) |
| `crypto.mnemonic_hash` | string | - | SHA256 hash of mnemonic for verification |
| `private_registries` | list | `[]` | Private registry patterns to skip |
| `gc.inactive_days_threshold` | int | `90` | GC inactivity threshold in days |
| `log_level` | string | `"INFO"` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `transfer_path` | string | `""` | Default directory for transfer files (empty = cwd) |
| `manifest_ttl` | int | `30` | Days before cached manifests are considered stale |
| `oci.restrict` | bool | `false` | Require Bearer token on `/v2/*` OCI endpoints |

### Online CLI (`~/.buncker/config.json`)

| Field | Type | Description |
|-------|------|-------------|
| `salt` | string | Base64-encoded PBKDF2 salt (set by `buncker-fetch pair`) |
| `derived_key_check` | string | Encrypted marker for mnemonic verification |
| `transfer_path` | string | Default directory for transfer files (empty = cwd) |

## Command Reference

### `buncker` (offline daemon)

| Command | Description |
|---------|-------------|
| `buncker setup` | Initialize: generate keys, create config, init store, start daemon |
| `buncker serve` | Start the HTTP daemon (reads mnemonic from `BUNCKER_MNEMONIC` env or stdin) |
| `buncker prepare <Dockerfile>` | Analyze + generate transfer request in one step |
| `buncker analyze <Dockerfile>` | Analyze Dockerfile and identify missing blobs |
| `buncker analyze --compose <file>` | Analyze docker-compose.yml and resolve all service images |
| `buncker generate-manifest` | Generate an encrypted transfer request |
| `buncker import [file.tar.enc]` | Import an encrypted transfer response (auto-scans `transfer_path` if omitted, `--cleanup` deletes file after success) |
| `buncker status` | Show registry status (blob count, store size) |
| `buncker verify` | Re-hash all blobs and detect silent corruption (bit-rot) |
| `buncker gc --report` | List inactive blobs eligible for garbage collection |
| `buncker gc --execute` | Delete reported inactive blobs (requires `--yes` or interactive confirmation) |
| `buncker rotate-keys` | Generate a new mnemonic and deprecate old keys |
| `buncker export-ca` | Print CA certificate to stdout (TLS mode only) |
| `buncker api-setup` | Generate API tokens and activate TLS for LAN access |
| `buncker api-show readonly\|admin` | Display an API token |
| `buncker api-reset readonly\|admin` | Regenerate an API token |

**Flags:**

| Flag | Description |
|------|-------------|
| `--config <path>` | Config file path (default: `/etc/buncker/config.json`) |
| `--cleanup` | Delete `.tar.enc` file after successful `import` |
| `--build-arg KEY=VALUE` | Build argument for `analyze` and `prepare` (repeatable) |
| `--output <path>` | Output directory for `generate-manifest` and `prepare` |
| `--inactive-days N` | GC inactivity threshold (default: 90) |
| `--compose <file>` | Docker Compose file for `analyze` (resolves all services) |
| `--restrict-oci` | Require Bearer token on `/v2/*` OCI endpoints (`serve`) |
| `--refresh-stale` | Include stale manifests for re-download (`generate-manifest`) |
| `--operator <name>` | Operator name for GC audit trail |
| `--grace-period N` | Key rotation grace period in days (default: 30) |
| `--cert <path>` | TLS certificate for `api-setup` |
| `--key <path>` | TLS private key for `api-setup` |

### `buncker-fetch` (online CLI)

| Command | Description |
|---------|-------------|
| `buncker-fetch pair` | Enter 16-word mnemonic and derive encryption keys |
| `buncker-fetch inspect <file.json.enc>` | Decrypt and display transfer request summary |
| `buncker-fetch fetch [file.json.enc]` | Fetch missing blobs and build encrypted response (auto-scans `transfer_path` if omitted) |
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
| `--deb <path>` | Include a `.deb` update file in the `fetch` response |

## Maintenance

### Key rotation

Generate a new mnemonic when compromised or as periodic security practice:

```bash
sudo buncker rotate-keys --grace-period 30
# Write down the new 16-word mnemonic
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

## Security Hardening

### Mnemonic storage (`/etc/buncker/env`)

`buncker setup` encrypts the mnemonic with a PBKDF2-derived key from
`/etc/machine-id` and stores the ciphertext as `BUNCKER_MNEMONIC_ENC=<base64>`
in `/etc/buncker/env` (mode 0600, owned by root). The daemon decrypts it
automatically on startup. This prevents direct exposure if the file is
read by an attacker without access to the machine-id.

On sensitive deployments, consider additional protections:

- **LUKS encryption** - place the buncker data directory on a LUKS
  encrypted partition for full at-rest protection
- **TPM-backed encryption** - use `systemd-creds` or `clevis` to seal
  `/etc/buncker/env` to the machine's TPM, so it can only be decrypted
  on that specific host
- **Manual entry** - remove `/etc/buncker/env` and enter the mnemonic via
  stdin on each daemon start (set `BUNCKER_MNEMONIC` env or pipe it).
  This provides the strongest protection but requires manual intervention
  on every restart

### Backup and recovery

Buncker state consists of the blob store and configuration files. To back up:

```bash
# Back up store and config
rsync -a /var/lib/buncker/ /backup/buncker-store/
rsync -a /etc/buncker/ /backup/buncker-config/
```

To restore, copy the files back and restart the daemon.

**Mnemonic recovery**: if the mnemonic is lost and `/etc/buncker/env` is
unavailable, the only option is `buncker rotate-keys` to generate a new
mnemonic. There is no way to extract the original mnemonic from the config.
After rotation, re-pair the online machine with `buncker-fetch pair`.

### OCI endpoints (`/v2/*`)

By default, the OCI Distribution API endpoints (`/v2/`, `/v2/<name>/manifests/`,
`/v2/<name>/blobs/`) are unauthenticated, even when API auth is enabled.
Docker clients can pull images without Bearer token configuration.

**For high-security environments**, use `--restrict-oci` to require
authentication on OCI endpoints:

```bash
buncker serve --restrict-oci
```

This requires `api-setup` to be configured first (tokens + TLS). The
auto-generated certificate covers `localhost`, `127.0.0.1`, and `buncker`
as SANs. For a custom hostname, provide your own certificate via
`buncker api-setup --cert <path> --key <path>`.

Docker clients authenticate via `hosts.toml`:

```toml
# /etc/docker/certs.d/buncker:5000/hosts.toml
server = "https://buncker:5000"

[host."https://buncker:5000"]
  capabilities = ["pull"]
  ca = "/path/to/ca.pem"
  [host."https://buncker:5000".header]
    Authorization = ["Bearer <readonly-token>"]
```

When restricted, unauthenticated requests receive a 401 with a standard
`WWW-Authenticate: Bearer` challenge per the OCI Distribution Spec.

**Default mode implications:**

- Any machine on the offline LAN can pull images from buncker
- Image content is not confidential in most air-gapped scenarios (the
  threat model protects integrity and provenance, not secrecy)
- For additional access control without `--restrict-oci`, use network-level
  controls (firewall rules, VLAN segmentation) to limit which hosts can
  reach port 5000

### Admin API protection

When API auth is enabled (`buncker api-setup`):

- TLS is mandatory (the daemon refuses to start without it)
- Admin endpoints require the admin token (analyze, import, GC execute)
- Read-only endpoints accept either token (status, logs, health, GC report)
- Token values are never logged (only `auth_level` appears in audit trail)
- All admin API calls are logged with `client_ip`, `auth_level`, and
  `user_agent` for forensic review
- Per-IP rate limiting on admin endpoints: 60 requests/minute sliding window
  (returns 429 with `Retry-After` header when exceeded)

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `dpkg: dependency problems` | Missing Python or cryptography | `sudo apt-get install -f` |
| `Cannot connect to buncker daemon` | Daemon not running | `sudo systemctl start buncker` and check `journalctl -u buncker` |
| `mnemonic does not match config` | Wrong mnemonic entered | Re-enter the correct 16 words from initial setup |
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
make build-rpm  # build .rpm packages to dist/
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

## Roadmap

| Feature | Description | Status |
|---------|-------------|--------|
| Docker Compose support | `buncker analyze --compose docker-compose.yml` to extract `image:` and `build.dockerfile` from all services | Done (v1.0.0) |
| RPM packaging | `.rpm` packages for RHEL/Fedora enterprise environments | Done (v1.0.0) |
| Log rotation | `logrotate.d/buncker` config shipped in .deb/.rpm for `/var/log/buncker/` | Done (v1.0.0) |
| OCI auth restriction | `--restrict-oci` flag to require read-only token on `/v2/*` endpoints (high-security environments) | Done (v1.0.0) |
| Manifest cache TTL | Configurable TTL (default 30d) on offline manifest cache with staleness warning and `--refresh-stale` flag | Done (v1.0.0) |

## License

[Apache 2.0](LICENSE) - Copyright 2026 Rwx-G.
