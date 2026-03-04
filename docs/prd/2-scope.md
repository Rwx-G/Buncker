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

## Out of Scope (V2+)

- **Cosign / supply chain signatures** - V2: optional `--verify-signature` flag on buncker-fetch
- **Multi-registry offline sync** - V2: shared store via NFS/CIFS (filesystem config). V3: active replication
- **RPM / tarball packaging** - V2 if community demand (Fedora, RHEL)
- **pip / PyPI packaging** - Not planned (contradicts zero-pip philosophy)
- **Web interface / dashboard** - Not planned. CLI + HTTP API suffice
- **External monitoring** (Prometheus, Grafana) - Not planned. Audit trail logs cover traceability
- **Helm charts / Kubernetes manifests support** - V1 = Docker images only
- **Windows / macOS support** - V1 = Debian/Ubuntu only
- **Daemon auto-update** - The .deb can be included in response.tar.enc but installation remains manual

---
