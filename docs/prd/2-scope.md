# 2. Scope

## In Scope (V1 MVP)

- Static Dockerfile resolver (ARG, multi-stage, multi-arch, scratch, digest)
- Local OCI Image Layout store (blobs + metadata + manual GC)
- Permanent HTTP daemon (OCI Distribution API pull subset + Admin API)
- Encrypted USB transfer (request.json.enc / response.tar.enc)
- buncker-fetch CLI (pair, inspect, fetch, cache)
- BIP-39 mnemonic crypto + AES-256-GCM + HMAC-SHA256
- .deb packaging (buncker + buncker-fetch)
- Audit trail logs (JSON Lines)
- GitHub Actions CI (lint + test + build .deb)
- Documentation (README, CONTRIBUTING, LICENSE Apache 2.0)

## In Scope (V2 - Admin API Auth & LAN Client Operations)

- Optional Bearer token authentication on `/admin/*` endpoints via `buncker api-setup`
- Two access levels: read-only (status, logs, gc report) and admin (analyze, generate-manifest, import, gc execute)
- TLS activation (operator-provided certificate or auto-signed) mandatory when auth is enabled
- Token management commands (`api-show`, `api-reset`)
- Remote Dockerfile analysis via content upload (curl)
- Streaming import via PUT with resume support (`Content-Range`) and pre-decryption checksum (`X-Buncker-Checksum`)
- Enriched audit trail with `client_ip`, `auth_level`, `user_agent` on all API requests

## Out of Scope (V3+)

- **Cosign / supply chain signatures** - optional `--verify-signature` flag on buncker-fetch
- **Multi-registry offline sync** - shared store via NFS/CIFS (filesystem config), later active replication
- **RPM / tarball packaging** - if community demand (Fedora, RHEL)
- **pip / PyPI packaging** - Not planned (contradicts zero-pip philosophy)
- **Web interface / dashboard** - Not planned. CLI + HTTP API suffice
- **External monitoring** (Prometheus, Grafana) - Not planned. Audit trail logs cover traceability
- **Helm charts / Kubernetes manifests support** - Docker images only
- **Windows / macOS support** - Debian/Ubuntu only
- **Daemon auto-update** - The .deb can be included in response.tar.enc but installation remains manual

---
