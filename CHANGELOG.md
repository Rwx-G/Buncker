# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.3] - 2026-03-12

### Changed

- Handler converted from `BaseHTTPRequestHandler` to standalone WSGI-compatible class with streaming support
- Blob responses now use WSGI streaming iterator instead of full memory buffering, eliminating RAM overhead for large blobs
- Chunk size increased from 64 KiB to 1 MiB for reduced Python loop overhead on large blob transfers
- TCP_NODELAY enabled on accepted connections to reduce latency on small responses (manifests, HEAD)
- Server TCP backlog set to 32 for predictable connection queuing under load

### Security

- File descriptor leak in `Store.import_blob` error path now properly closed before cleanup
- Content-Length header validated as integer before use, preventing type-confusion attacks
- Log limit parameter bounds-checked (0-10000) to prevent abuse via excessively large values
- Tar extraction in transfer import now rejects symlinks and hardlinks (Python < 3.12 path)
- HMAC signature decode errors now raise `TransferError` instead of unhandled `UnicodeDecodeError`
- SHA256 pre-verification on blob GET: file integrity checked before HTTP headers are sent, returning 500 BLOB_CORRUPT on mismatch
- TOCTOU fix on analysis cache: `analysis_id` validation now runs inside the same lock scope as the cache read
- Per-IP rate limiting (200 req/min sliding window) on OCI manifest and blob endpoints

### Fixed

- GC report/execute race condition: `_last_gc_report` in Store now protected by a threading lock
- Multi-arch platform resolution in buncker-fetch now supports os/arch/variant format (e.g. `linux/arm/v7`)
- Reserved `filename` key in log record extra dict renamed to avoid conflict with Python 3.14 stricter logging

## [1.0.2] - 2026-03-12

### Security

- Mnemonic encryption now combines `/etc/machine-id` with a root-only `/etc/buncker/key-material` file (mode 0600), preventing non-root processes from reconstructing the AES key
- HTTP handler socket timeout set to 60 seconds per read, mitigating slowloris-style thread exhaustion on the bounded 16-worker pool

### Added

- `analysis_id` (UUID) returned by `/admin/analyze` and required by `/admin/generate-manifest` to detect concurrent analysis overwrites (409 ANALYSIS_REPLACED)
- `--analysis-id` required argument on `buncker generate-manifest` CLI command
- `generate_key_material()` function in `shared.crypto` for root-only key file generation
- Integration test Phase 4: 4th Docker container (`client-offline`) for concurrent analysis race detection, socket timeout (slowloris), and large transfer limit validation
- Integration test Phase 5: real `docker pull` from buncker registry, daemon crash recovery (kill -9 + restart + blob persistence), and garbage collection lifecycle (report/impact/execute with post-GC pull failure)

### Changed

- Import size limit raised from 4 GiB to 40 GiB to accommodate large ML image bundles via USB or LAN
- Path validation in `/admin/analyze` simplified: redundant `..` check removed, `Path.resolve()` + `is_file()` is sufficient

## [1.0.1] - 2026-03-10

### Fixed

- Default bind address changed from `0.0.0.0` to `127.0.0.1` to prevent accidental exposure on multi-VLAN networks
- `buncker api-setup` now switches bind to `0.0.0.0` when activating API auth + TLS for LAN client access

### Changed

- README feature pitch renamed from "Zero exotic deps" to "OS-packaged deps only"

### Added

- Windows/WSL2 requirement note in README
- Security documentation: GPG out-of-scope rationale and network binding defaults
- `python3-yaml` added to tech stack documentation

## [1.0.0] - 2026-03-09

### Added

- Docker Compose analysis: `buncker analyze --compose <file>` parses `docker-compose.yml` to resolve all service images and Dockerfiles in one pass
- Remote Compose analysis via `compose_content` / `compose_path` fields in `POST /admin/analyze`
- RPM packaging for RHEL/Fedora: `.spec` files, `make build-rpm` target, CI build and install jobs
- Logrotate configuration for `/var/log/buncker/*.log` (daily rotation, 30 days retention, compress + copytruncate)
- `--restrict-oci` flag on `buncker serve`: requires Bearer token on `/v2/*` endpoints with `WWW-Authenticate` challenge per OCI Distribution Spec
- `oci.restrict` config option (default: false) for persistent OCI auth restriction
- Manifest cache TTL: `manifest_ttl` config option (default: 30 days) triggers warnings for stale manifests during analyze
- `--refresh-stale` flag on `buncker generate-manifest` to include stale manifests for re-download by buncker-fetch
- `stale_manifests` count in `GET /admin/status` response
- `stale_manifests` list in analyze response for programmatic detection

### Security

- Tar extraction: validate member paths for `..` and absolute paths on Python < 3.12 (zip-slip protection)
- TLS: enforce minimum TLS 1.2 and restrict cipher suites to ECDHE+AESGCM/CHACHA20
- API tokens: warn in logs if `api-tokens.json` has insecure file permissions on load
- Auth: reject empty Bearer token value early instead of relying on comparison fallthrough
- Import: validate `X-Buncker-Checksum` header as strict `sha256:<64 hex chars>` format
- Store: set blob and metadata files to mode 0600 (no world-readable defaults)
- Store: reject symlinks at blob destination path before write
- Crypto: bump PBKDF2 iterations for env key derivation from 100k to 600k (OWASP minimum)
- TLS: use RSA-4096 for self-signed CA and server certificates (was 2048)
- HTTP: add `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Cache-Control: no-store` on all responses

### Changed

- `packaging/buncker/debian/control` now depends on `python3-yaml` for Compose support
- `.deb` install file updated with `auth.py` and `compose.py` modules

## [0.9.0] - 2026-03-09

### Added

- `buncker verify` command to re-hash all blobs and detect silent corruption (bit-rot)
- `/admin/health` endpoint returning store integrity, TLS cert expiry, disk space, and uptime
- TLS support in BunckerServer via `ssl.SSLContext` (server now serves HTTPS when configured)
- `gc --report` now shows which images become non-pullable if candidates are deleted (`gc_impact_report`)
- `POST /admin/gc/impact` endpoint to analyze image impact before executing GC
- `gc --execute` requires `--yes` flag or interactive confirmation to prevent accidental deletions
- Fetch rate limiting: `buncker-fetch` now retries on HTTP 429 using `Retry-After` header instead of failing immediately
- Rate limit observability: logs warning when `RateLimit-Remaining` drops below 10 on successful responses
- Manifest auto-refresh: `buncker-fetch` tracks manifest digests across fetches and warns when upstream content changes
- Dockerfile ARG substitution now supports `${VAR:-default}` and `${VAR:+replacement}` syntax
- Security hardening documentation (mnemonic encryption, `/v2/*` risks, admin API protections)
- Mnemonic at-rest encryption using machine-id derived AES key (`BUNCKER_MNEMONIC_ENC` in `/etc/buncker/env`)
- `buncker-fetch fetch --deb <path>` flag to bundle a `.deb` update in the encrypted response
- `.deb` auto-extraction on `buncker import`: update files saved to `store/updates/` with notification

### Fixed

- CA certificate missing `KeyUsage` and `SubjectKeyIdentifier` extensions (Python 3.14 compatibility)
- Server certificate missing `AuthorityKeyIdentifier` extension
- `blob_integrity_error` and `metadata_update_failed` logs missing audit fields (client_ip, auth_level, user_agent)
- Integration test Phase 2 now uses HTTPS URLs and `-k` flag for self-signed certs
- `procps` added to offline Dockerfile for reliable daemon process management

### Security

- `/admin/health` accessible with read-only token (same level as `/admin/status`)
- Mnemonic encrypted at rest with PBKDF2-derived key from `/etc/machine-id` (no longer stored in cleartext)

## [0.8.1] - 2026-03-08

### Added

- Per-IP rate limiting on `/admin/*` endpoints (60 req/min sliding window, 429 + Retry-After)
- Disk space pre-check before import (507 Insufficient Storage if < 2x upload size)
- TLS certificate expiry warning at daemon startup (warns if <= 30 days or expired)
- `--cleanup` flag on `buncker import` to delete `.tar.enc` file after successful import
- Disk usage info in `/admin/status` and `buncker status` (disk_total, disk_used, disk_free)
- `api-setup` now copies `ca.pem` to `/etc/buncker/ca.pem` and displays cert SHA-256 fingerprint

### Changed

- PBKDF2 iterations bumped from 600,000 to 1,200,000 (2x OWASP 2023 minimum for SHA-256)

### Security

- Thread-safe `_last_analysis` state with `threading.Lock` (race condition fix)
- Path traversal hardening in `/admin/analyze`: `Path.resolve()` + `is_file()` check

## [0.8.0] - 2026-03-06

### Added

- `buncker api-setup` command: generates 256-bit Bearer tokens (read-only + admin) and activates TLS with auto-signed CA or user-provided certificates
- `buncker api-show readonly|admin` and `buncker api-reset readonly|admin` for token management
- Auth middleware on `/admin/*` endpoints with constant-time token comparison (`hmac.compare_digest`)
- Endpoint access matrix: read-only token for status/logs/gc-report, admin token for analyze/generate-manifest/import/gc-execute
- `/v2/*` OCI endpoints remain always unauthenticated regardless of auth config
- Audit trail enrichment: all API log entries now include `client_ip`, `auth_level`, `user_agent`
- `api_auth_rejected` log event for failed authentication attempts
- Remote Dockerfile analysis via `dockerfile_content` field in `POST /admin/analyze` (path mode restricted to localhost)
- `PUT /admin/import` endpoint: streaming upload with SHA256 checksum verification (`X-Buncker-Checksum` header) and `Content-Range` resume support
- E2E tests for full authenticated cycle, access control, token reset, and backward compatibility
- README restructured with two explicit Quick Start flows (Direct mode and LAN client mode)
- Docker integration test Phase 2: LAN client flow with auth enforcement (31 checks total)

### Security

- TLS is mandatory when API auth is enabled (daemon refuses to start without it)
- Token values are never logged (NFR7 compliance)
- `api-tokens.json` file permissions set to 0600

## [0.7.0] - 2026-03-05

### Added

- Multi-container integration test environment (`tests/integration/`): 3 services (online, buncker-offline, client), 2 networks (bridge + internal), shared transfer volume simulating USB key
- Step-by-step README walkthrough for the full air-gapped cycle (setup, pair, analyze, fetch, import, docker build)
- 16-word mnemonic with embedded salt (12 secret + 4 salt words) for air-gapped pairing without separate salt exchange
- OCI manifests included in transfer response - online side fetches manifests alongside blobs so offline can cache them on import
- Docker client setup guide in README: explicit registry references and registry mirror approaches
- `buncker setup` now auto-starts the daemon via `systemctl enable --now` and stores the mnemonic in `/etc/buncker/env` (mode 0600) for automatic service restarts
- Colored CLI output for `buncker setup` with staged progress indicators (step 1/4 through 4/4)
- `buncker prepare <Dockerfile>` command combining analyze + generate-manifest in a single step with colored summary output
- Configurable `transfer_path` in both `buncker` and `buncker-fetch` configs for default transfer file directory
- `buncker import` auto-scans `transfer_path` for newest `*.tar.enc` when no file argument is given
- `buncker-fetch fetch` auto-scans `transfer_path` for newest `*.json.enc` when no file argument is given and writes response to `transfer_path` when no `--output` flag is given
- `--output` flag on `buncker generate-manifest` to specify output directory
- Roadmap section in README tracking planned features (admin API auth, LAN client operations)

### Fixed

- Thread pool initialization order in server: `_pool` created before `super().__init__()` to prevent `AttributeError` when port bind fails
- .deb package no longer ships skeleton `config.json` that blocked `buncker setup`
- `generate-manifest` now succeeds when images are detected but no blobs are cached yet (first-run scenario)
- `buncker-fetch fetch` extracts blob digests from fetched manifests and downloads them automatically (no pre-existing manifest cache required)
- Setup message corrected from "12-word" to "16-word" mnemonic

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

[Unreleased]: https://github.com/Rwx-G/Buncker/compare/v1.0.3...HEAD
[1.0.3]: https://github.com/Rwx-G/Buncker/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/Rwx-G/Buncker/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/Rwx-G/Buncker/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Rwx-G/Buncker/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/Rwx-G/Buncker/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/Rwx-G/Buncker/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/Rwx-G/Buncker/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Rwx-G/Buncker/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Rwx-G/Buncker/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Rwx-G/Buncker/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Rwx-G/Buncker/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Rwx-G/Buncker/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Rwx-G/Buncker/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Rwx-G/Buncker/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Rwx-G/Buncker/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/Rwx-G/Buncker/releases/tag/v0.0.1
