# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-03-05

### Added

- Multi-container integration test environment (`tests/integration/`): 3 services (online, buncker-offline, client), 2 networks (bridge + internal), shared transfer volume simulating USB key
- Step-by-step README walkthrough for the full air-gapped cycle (setup, pair, analyze, fetch, import, docker build)
- 16-word mnemonic with embedded salt (12 secret + 4 salt words) for air-gapped pairing without separate salt exchange
- OCI manifests included in transfer response - online side fetches manifests alongside blobs so offline can cache them on import
- Docker client setup guide in README: explicit registry references and registry mirror approaches

### Fixed

- Thread pool initialization order in server: `_pool` created before `super().__init__()` to prevent `AttributeError` when port bind fails
- .deb package no longer ships skeleton `config.json` that blocked `buncker setup`

### Changed

- **BREAKING**: mnemonic is now 16 words instead of 12 (existing setups must re-run `buncker setup`)

## [0.6.1] - 2026-03-04

### Added

- Docker-based .deb installation tests (`tests/deb-install/`): Dockerfile, verify.sh, run.sh (69 checks)
- Live end-to-end test: fetches real `alpine:3.19` blobs from Docker Hub, imports into store
- CI job `test-deb-install` verifies .deb packages in a Debian 12 container before release
- README: Installation section (deb packages and from source)
- README: Command Reference tables for `buncker` and `buncker-fetch`
- README: Maintenance section (key rotation, GC, cache management, logs)
- README: Troubleshooting table with common problems and solutions

### Fixed

- `python3-cryptography` dependency lowered from `>= 41.0` to `>= 38.0` (Debian 12 ships 38.0.4)
- CRLF line endings in Makefile deb build: all text files now stripped of `\r` before packaging
- Registry client: `Authorization` header forwarded on cross-host redirects caused HTTP 400 from CDN (AWS S3)
- Helper script `build-in-docker.sh` for cross-platform .deb builds

### Changed

- README: Quick Start enriched with expected output after each command
- README: status badge updated to v0.6.1
- CI: release job now depends on both `build-deb` and `test-deb-install`
- Version bumped to 0.6.1 across pyproject.toml, buncker, buncker_fetch, packaging

## [0.6.0] - 2026-03-04

### Fixed

- `handler.py` used hardcoded version "0.3.0" instead of `buncker.__version__`
- Removed obsolete `test_placeholder.py` scaffolding artifact

### Added

- QA review and gate files for all 20 stories (Epics 1-5 now fully reviewed)
- Dev Agent Records filled for Epic 4 stories (4.1-4.4)

### Changed

- Version bumped to 0.6.0 across pyproject.toml, buncker/__init__.py, buncker_fetch/__init__.py

## [0.5.0] - 2026-03-04

### Added

- .deb packaging for `buncker` and `buncker-fetch` with Debian control files, dependencies, and file mappings
- Entry point scripts (`/usr/bin/buncker`, `/usr/bin/buncker-fetch`) with PYTHONPATH setup
- `postinst` script for buncker: creates system user/group, data directories, sets permissions
- Default config file `/etc/buncker/config.json` as conffile (preserved on upgrade)
- `make build-deb` target: builds both .deb packages to `dist/` using `dpkg-deb`
- `make clean` target: removes `dist/` build artifacts
- CI `build-deb` job: builds .deb on Ubuntu, verifies with dpkg-deb, uploads as artifacts
- Packaging tests (`tests/test_packaging.py`): validates .deb metadata, file contents, dependencies
- End-to-end test suite (`tests/e2e/`): full USB cycle, key rotation, corrupt blob handling, HMAC tamper detection
- `@pytest.mark.e2e` marker for running e2e tests separately
- Complete documentation: README with configuration reference, CONTRIBUTING with version tags
- `.gitignore` updates for dist/ and secrets

### Changed

- README status badge updated to v0.5.0
- `make build` now points to `make build-deb`
- Version aligned to 0.5.0 across pyproject.toml, buncker/__init__.py, buncker_fetch/__init__.py

## [0.4.0] - 2026-03-04

### Added

- Online registry client (`buncker_fetch/registry_client.py`): OCI auth discovery (Bearer token), `fetch_manifest()`, `fetch_blob()` streaming, retry with exponential backoff, HTTP 429 rate limit handling, credentials via env vars
- Blob cache (`buncker_fetch/cache.py`): local `~/.buncker/cache/blobs/sha256/` storage with SHA256 verification, atomic writes, `cache_clean()` for old blob removal, `stats()` for cache metrics
- Parallel fetcher (`buncker_fetch/fetcher.py`): `Fetcher` class with `ThreadPoolExecutor`, auto-skip cached blobs, SHA256 retry (3x), progress reporting (human-readable and JSON), `FetchResult` dataclass
- Transfer module (`buncker_fetch/transfer.py`): `process_request()` decrypts and verifies HMAC on incoming `.json.enc` requests, `build_response()` builds OCI layout tar, signs with HMAC, encrypts with AES-256-GCM, includes `ERRORS.json` for failed blobs
- Config module (`buncker_fetch/config.py`): `load_config()`/`save_config()` for `~/.buncker/config.json`, `validate_config()` checks pairing state
- CLI entry point (`buncker_fetch/__main__.py`): argparse with subcommands `pair`, `inspect`, `fetch`, `status`, `cache clean`, `--json` flag for machine output, `--config` override
- Version single source of truth in `buncker_fetch/__init__.__version__`

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

[Unreleased]: https://github.com/Rwx-G/Buncker/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/Rwx-G/Buncker/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Rwx-G/Buncker/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Rwx-G/Buncker/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Rwx-G/Buncker/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Rwx-G/Buncker/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Rwx-G/Buncker/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Rwx-G/Buncker/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Rwx-G/Buncker/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/Rwx-G/Buncker/releases/tag/v0.0.1
