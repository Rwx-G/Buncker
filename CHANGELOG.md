# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-03-04

### Added

- HTTP server (`buncker/server.py`): `BunckerServer` with `ThreadingHTTPServer` and bounded `ThreadPoolExecutor`, graceful shutdown
- OCI Distribution API (`buncker/handler.py`): GET/HEAD for `/v2/`, manifests, blobs with 64KB streaming, correct OCI headers (`Docker-Content-Digest`, `Content-Type`, `Content-Length`)
- Admin API (`buncker/handler.py`): POST `/admin/analyze`, `/admin/generate-manifest`, `/admin/import`; GET `/admin/status`, `/admin/gc/report`, `/admin/logs`; POST `/admin/gc/execute`
- Transfer module (`buncker/transfer.py`): `generate_request()` produces encrypted+signed `.json.enc` files, `import_response()` decrypts, verifies HMAC, imports blobs with SHA-256 check, handles partial imports via `ERRORS.json`
- Config module (`buncker/config.py`): `load_config()` with sensible defaults, validation, `save_config()`
- CLI entry point (`buncker/__main__.py`): argparse with subcommands `setup`, `serve`, `analyze`, `generate-manifest`, `import`, `status`, `gc`, `rotate-keys`, `export-ca`
- systemd unit file (`packaging/buncker/debian/buncker.service`): hardened service with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`
- Version single source of truth in `buncker/__init__.__version__`

### Fixed

- Cross-platform daemon shutdown using `threading.Event` instead of `signal.pause()` (Windows support)
- tarfile extraction fallback for Python < 3.12 (filter="data" not available on 3.11)
- Chunked SHA256 verification for blob import to reduce memory pressure on large blobs

### Security

- SHA256 integrity verification when serving blobs via OCI GET endpoint
- Path traversal prevention in `/admin/analyze` rejects `..` path components
- Request body size limits: 4 GiB for import, 10 MiB for JSON endpoints

## [0.2.0] - 2026-03-04

### Added

- Blob store (`buncker/store.py`): OCI Image Layout initialization, atomic blob import with SHA-256 verification, metadata sidecars, `has_blob`, `get_blob`, `list_missing`
- Store GC (`buncker/store.py`): `gc_report()` scans inactive blobs, `gc_execute()` deletes with audit logging and report validation
- Manifest cache (`buncker/registry_client.py`): offline OCI manifest cache with tag and digest lookup, `_buncker` extension metadata
- Dockerfile parser (`buncker/resolver.py`): `parse_dockerfile()` extracts FROM references with ARG substitution, `--platform`, digest, alias, Docker Hub normalization, private registry detection
- Resolver pipeline (`buncker/resolver.py`): `resolve_dockerfile()` orchestrates parse -> manifest lookup -> `list_missing` with deduplication and warnings

## [0.1.0] - 2026-03-04

### Added

- Project scaffolding: `shared/`, `buncker/`, `buncker_fetch/`, `tests/`, `packaging/` directories
- `pyproject.toml` with ruff lint/format and pytest configuration
- GitHub Actions CI workflow (`ci.yml`) with lint and test jobs
- `Makefile` with `lint`, `test`, and `build` targets
- Placeholder test to verify CI pipeline end-to-end
- Crypto module (`shared/crypto.py`): AES-256-GCM encrypt/decrypt, PBKDF2 key derivation, HMAC-SHA256 sign/verify, BIP-39 mnemonic generation
- BIP-39 English wordlist (`shared/wordlist.py`): 2048 words embedded
- OCI module (`shared/oci.py`): parse manifests/indexes, select platform, build OCI Image Layout, verify blob digests
- Exception hierarchy (`shared/exceptions.py`): BunckerError base + ConfigError, CryptoError, StoreError, ResolverError, RegistryError, TransferError
- JSON Lines logging (`shared/logging.py`): JsonFormatter, setup_logging with file + stderr handlers, get_logger helper

## [0.0.1] - 2026-03-04

### Added

- Product Requirements Document (PRD) with 5 epics and detailed stories
- Architecture document covering all components, workflows, data models, and API spec
- 19 implementation stories (1.1-1.4, 2.1-2.5, 3.1-3.4, 4.1-4.4, 5.1-5.3) - all QA-validated
- Repository setup: README, LICENSE (Apache 2.0), CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
- GitHub templates for issues (bug report, feature request) and pull requests
- Conventional Commits convention and branching strategy documented

[Unreleased]: https://github.com/Rwx-G/Buncker/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Rwx-G/Buncker/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Rwx-G/Buncker/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Rwx-G/Buncker/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/Rwx-G/Buncker/releases/tag/v0.0.1
