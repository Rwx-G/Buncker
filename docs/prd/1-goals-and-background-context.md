# 1. Goals and Background Context

## Goals

- **Enable Docker builds in 100% air-gapped environments** without internet access, reliably and reproducibly
- **Surgically synchronize** only the missing Docker layers (delta sync), not bulk snapshots
- **Secure the USB transfer channel** with AES-256 encryption and HMAC signatures, key shared via BIP-39 mnemonic
- **Provide a local OCI registry** transparent to Docker clients (standard pull, no client-side changes beyond hosts.toml)
- **Complete traceability**: audit trail of every operation (analysis, transfer, import, pull, GC)
- **Zero exotic dependencies**: Python stdlib + a single apt package (`python3-cryptography`), .deb packaging
- **Open source on GitHub** under Apache 2.0 license, owner-maintained

## Target Persona

**Infrastructure operator / DevOps engineer** working in a secured environment (defense, industry, isolated datacenter, critical infrastructure). Responsible for maintaining Docker build environments on air-gapped networks. Proficient in Linux, Docker, and system administration. Looking for a reliable, auditable, no-magic tool - not a "cloud-native" solution ill-suited to their context.

## Background Context

Air-gapped environments (defense, industry, critical infrastructure) need to build Docker images without internet access. Existing solutions like Hauler (SUSE/Rancher) do bulk snapshot-and-ship: they export complete images with no diff intelligence, no Dockerfile resolution, and no transfer channel encryption.

Buncker solves this with a surgical approach: a static Dockerfile resolver identifies missing layers, an encrypted transfer manifest is carried via USB to a connected machine, which fetches only the needed delta and returns an encrypted OCI package. All without ever compromising the offline environment's network isolation.

## Competitive Analysis

| | **Buncker** | **Hauler (SUSE/Rancher)** | **docker save/load** | **Skopeo copy** |
|---|---|---|---|---|
| Delta sync (missing layers only) | Yes | No (bulk snapshot) | No (full image) | No (full image) |
| Dockerfile resolution | Yes (static, ARG, multi-stage) | No | No | No |
| Transfer channel encryption | AES-256-GCM + HMAC | No | No | No |
| Complete audit trail | Yes (JSON Lines) | Partial | No | No |
| OCI standard format | Yes | Yes | Docker v2 | Yes |
| Packaging | .deb (Debian/Ubuntu) | Go binary | Bundled with Docker | Go binary |
| Dependencies | Python + 1 apt pkg | Go runtime | Docker daemon | Containers/image lib |

Buncker is the only tool combining Dockerfile resolution + delta sync + encryption for air-gapped transfers.

## Success Metrics

- **V1 Success:** Full USB cycle functional (analyze → generate → fetch → import → pull) with 0 verified corruption over 100 consecutive transfers
- **Integrity:** 100% of imported blobs pass SHA256 verification - no silent corruption
- **Delta efficiency:** >50% reduction in transferred volume compared to a full `docker save` on a multi-image case with shared layers
- **Adoption:** First functional installation by an external user (not the author) within 3 months of v1.0 release

## Change Log

| Date | Version | Description | Author |
|------|---------|-------------|--------|
| 2026-03-04 | 1.0 | Initial PRD from brainstorm + architecture | Romain G. |
| 2026-03-04 | 1.1 | Added persona, competitive analysis, success metrics, out-of-scope, license | Romain G. |
| 2026-03-04 | 1.2 | Translated to English (public repo) | Romain G. |

---
