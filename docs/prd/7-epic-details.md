# 7. Epic Details

## Epic 1 - Foundation & Crypto Core

**Goal:** Establish project foundations (structure, CI, tooling) and deliver the two shared modules (crypto + OCI) fully tested. At the end of this epic, one can generate a mnemonic, derive keys, encrypt/decrypt data, sign/verify HMACs, and manipulate OCI Image Layout structures. CI runs (lint + tests).

### Story 1.1 - Project Scaffolding & CI

As a **developer**,
I want the project structure, linting, and CI pipeline set up,
so that all subsequent development has a solid, tested foundation.

**Acceptance Criteria:**
1. Repo contains the complete folder structure (shared/, buncker/, buncker_fetch/, tests/, packaging/)
2. pyproject.toml configures ruff (select E,F,W,I,UP,B,SIM) and pytest
3. .gitignore covers Python (__pycache__, .egg-info, dist/, *.pyc, .pytest_cache, .ruff_cache)
4. GitHub Actions workflow: lint (ruff check + format --check) + pytest on push/PR
5. A placeholder test passes in CI
6. Makefile with targets: lint, test, build (placeholder)
7. Minimal README.md with project name, one-liner description, and dev instructions

### Story 1.2 - Crypto Module (shared/crypto)

As an **operator**,
I want the cryptographic primitives implemented and tested,
so that all transfer security relies on proven, audited code.

**Acceptance Criteria:**
1. generate_mnemonic() returns 12 words from BIP-39 wordlist (2048 words) with secrets.token_bytes entropy
2. derive_keys(mnemonic, salt, iterations=600_000) returns tuple (aes_key, hmac_key) via PBKDF2-SHA256
3. encrypt(data, aes_key) encrypts with AES-256-GCM and returns nonce + ciphertext + tag
4. decrypt(data, aes_key) decrypts and verifies auth tag. Raises CryptoError if invalid
5. sign(data, hmac_key) returns HMAC-SHA256 hex digest
6. verify(data, hmac_key, signature) returns bool (constant-time comparison)
7. shared/wordlist.py contains the complete BIP-39 wordlist (2048 words) embedded
8. Unit tests: round-trip encrypt/decrypt, wrong key → CryptoError, valid/invalid HMAC, mnemonic has 12 valid words
9. 100% coverage on this module

### Story 1.3 - OCI Module (shared/oci)

As a **developer**,
I want OCI Image Layout parsing and building primitives,
so that both packages can manipulate standard OCI structures.

**Acceptance Criteria:**
1. parse_manifest(data) parses an OCI v2 manifest and returns an OCIManifest dataclass
2. parse_index(data) parses an OCI Image Index and returns an OCIIndex dataclass
3. select_platform(index, platform) returns the manifest digest for the requested platform
4. build_image_layout(blobs_dir, manifests, index) builds a valid OCI Image Layout directory
5. verify_blob(path, expected_digest) verifies SHA256. Returns bool
6. Dataclasses: OCIManifest, OCIIndex, OCIPlatform, OCILayer
7. Unit tests with JSON fixtures (real manifests from hello-world or nginx, simplified)

### Story 1.4 - Exception Hierarchy & Logging Setup

As a **developer**,
I want a consistent error and logging framework from day one,
so that all subsequent modules use the same patterns.

**Acceptance Criteria:**
1. shared/exceptions.py defines: BunckerError, ConfigError, CryptoError, StoreError, ResolverError, RegistryError, TransferError
2. Each exception accepts a message string and an optional context dict
3. shared/logging.py configures a JSON Lines logger: each entry contains ts, event, level, and additional fields
4. Helper setup_logging(level, output_path) configures the handler (JSONL file + stderr for ERROR+)
5. Tests: exceptions raise correctly, logger produces valid JSON

---

## Epic 2 - Store & Resolver

**Goal:** Implement local OCI blob storage and Dockerfile resolver. At the end of this epic, one can analyze a Dockerfile and get the list of resolved images and missing blobs in the store. The store handles import, read, metadata, and GC reporting.

### Story 2.1 - Store Core (import, read, metadata)

As an **operator**,
I want a reliable blob store on the filesystem,
so that imported Docker layers are safely stored and retrievable.

**Acceptance Criteria:**
1. Store(path) initializes an OCI Image Layout (oci-layout, index.json, blobs/sha256/, meta/sha256/)
2. import_blob(data, expected_digest) writes atomically (temp + verify SHA256 + rename). Raises StoreError on mismatch
3. has_blob(digest) returns bool
4. get_blob(digest) returns file path. Raises StoreError if absent
5. list_missing(digests) returns list of absent digests
6. Each import creates/updates the sidecar metadata JSON
7. update_metadata(digest, event) updates last_requested and increments request_count
8. Unit tests with tmp_path: import, idempotent re-import, corrupt blob rejected, missing list

### Story 2.2 - Store GC

As an **operator**,
I want to identify and clean up unused blobs,
so that disk space is managed without risking active images.

**Acceptance Criteria:**
1. gc_report(inactive_days) scans meta sidecars and returns candidates list
2. Shared blob is candidate only if ALL its image_refs are inactive
3. gc_execute(digests, operator) deletes blobs and sidecars, logs each deletion
4. gc_execute refuses to delete a digest not in the latest report
5. Tests: active blob not candidate, shared blob protected, effective deletion, log produced

### Story 2.3 - Manifest Cache (registry_client offline)

As a **developer**,
I want cached OCI manifests readable from the store,
so that the resolver can work without network access.

**Acceptance Criteria:**
1. cache_manifest() stores in manifests/{registry}/{repo}/{tag}/{platform}.json with _buncker.cached_at
2. get_manifest() returns cached manifest or None
3. Digest reference lookup via _buncker.source_digest
4. Tests: cache, retrieve, miss → None, digest lookup

### Story 2.4 - Dockerfile Parser

As an **operator**,
I want to analyze Dockerfiles and extract base image references,
so that I know exactly which images need to be synchronized.

**Acceptance Criteria:**
1. parse_dockerfile(path, build_args) returns a list of ResolvedImage dataclasses
2. Handles pre-FROM ARGs with defaults and overrides
3. Missing ARG without default → ResolverError with line info
4. Detects internal aliases (is_internal: True, skipped)
5. Handles FROM scratch → skipped
6. Handles --platform flag → extracts to platform field
7. Handles digests (@sha256:...) → extracts to digest field
8. Handles \ continuations and # comments
9. Docker Hub normalization (nginx → docker.io/library/nginx)
10. Tests: multi-stage, ARGs, platform, digest, scratch, alias, continuation, errors

### Story 2.5 - Resolver Pipeline (parser + cache + diff)

As an **operator**,
I want a complete analysis that tells me exactly which blobs are missing,
so that I can decide to generate a transfer request.

**Acceptance Criteria:**
1. resolve_dockerfile() orchestrates: parse → manifest lookup → list_missing
2. Returns AnalysisResult with resolved images, present/missing blobs, total missing size
3. Deduplication: shared blob counted once
4. Private registries → skipped with warning
5. Tag latest → warning logged
6. Manifest not in cache → image marked "unresolved" with explicit message
7. Tests: multi-image with shared blobs, private image skipped, missing manifest signaled

---

## Epic 3 - Daemon Offline (buncker)

**Goal:** Deliver the complete HTTP daemon: OCI API for Docker builds, Admin API for the operator, request generation, response import, configuration, and systemd service. At the end, buncker runs in production on an isolated LAN.

### Story 3.1 - HTTP Server & OCI Distribution API

As a **Docker build client**,
I want to pull images from the buncker registry,
so that my builds work without internet access.

**Acceptance Criteria:**
1. BunckerServer starts a ThreadingHTTPServer with bounded ThreadPoolExecutor
2. GET /v2/ returns 200
3. GET /v2/{name}/manifests/{reference} returns manifest with OCI headers
4. HEAD /v2/{name}/manifests/{reference} returns 200 or 404
5. GET /v2/{name}/blobs/{digest} streams blob with correct headers, updates metadata
6. HEAD /v2/{name}/blobs/{digest} returns 200 with Content-Length or 404
7. Unmatched requests → 404
8. Integration tests: server on localhost:0, simulated Docker pull

### Story 3.2 - Admin API

As an **operator**,
I want HTTP endpoints to manage the registry,
so that I can analyze Dockerfiles, generate transfers, and monitor the system.

**Acceptance Criteria:**
1. POST /admin/analyze accepts JSON, calls resolver, returns JSON report
2. POST /admin/generate-manifest generates request.json.enc. 409 if no analysis pending
3. POST /admin/import accepts multipart/form-data, executes verified import
4. GET /admin/status returns version, store stats, uptime
5. GET /admin/gc/report returns GC candidates
6. POST /admin/gc/execute accepts digests + operator
7. GET /admin/logs returns filtered logs
8. Input validation: digest regex, path traversal prevention
9. Integration tests for each endpoint

### Story 3.3 - Transfer: Generate Request & Import Response

As an **operator**,
I want to generate encrypted transfer requests and import encrypted responses,
so that I can securely sync layers via USB.

**Acceptance Criteria:**
1. generate_request() produces encrypted JSON with version, buncker_version, source_id, blobs
2. File named buncker-request-{datetime}-{source_id}.json.enc
3. import_response() decrypts → HMAC verify → SHA256 per blob → atomic import → cache manifests
4. ERRORS.json in response → partial import, valid blobs kept
5. Logging: transfer_manifest_generated and transfer_imported events
6. Tests: round-trip, invalid HMAC rejected, corrupt blob rejected

### Story 3.4 - Config, Setup & systemd

As an **operator**,
I want to set up buncker with a single command and run it as a system service,
so that it's production-ready on my isolated LAN.

**Acceptance Criteria:**
1. buncker setup generates mnemonic, derives keys, creates config and store
2. buncker serve starts the HTTP daemon
3. CLI: buncker {setup|serve|analyze|generate-manifest|import|status|gc|rotate-keys|export-ca}
4. CLI commands call admin API locally (except setup and serve)
5. rotate-keys generates new mnemonic with configurable grace period
6. export-ca exports CA certificate if TLS enabled
7. Config loaded from /etc/buncker/config.json with sensible defaults
8. systemd unit with hardening is functional

---

## Epic 4 - CLI Online (buncker-fetch)

**Goal:** Deliver the complete online tool: pairing, inspection, fetch with cache and parallelization, response building. At the end, the full USB cycle works end-to-end.

### Story 4.1 - Registry Client Online (auth + fetch)

As **buncker-fetch**,
I want to authenticate and download blobs from any OCI registry,
so that I can fulfill transfer requests.

**Acceptance Criteria:**
1. Auth discovery: GET /v2/ → 401 → parse Www-Authenticate → extract realm, service, scope
2. Token exchange with Bearer token support and Basic Auth for credentials
3. fetch_manifest() returns manifest JSON
4. fetch_blob() returns blob bytes streaming
5. Credentials from ~/.buncker/config.json via env vars
6. Retry 3x exponential backoff (1s, 3s, 9s). Timeout connect 30s, read 120s
7. HTTP 429 → explicit message
8. Tests: mock HTTP server simulating Docker Hub auth

### Story 4.2 - Fetcher & Cache

As an **operator**,
I want downloaded blobs cached locally and parallel downloads,
so that transfers are fast and resumable.

**Acceptance Criteria:**
1. Cache manages ~/.buncker/cache/blobs/sha256/
2. has_blob/get_blob/store_blob with SHA256 verification
3. Fetcher downloads blob list with ThreadPoolExecutor
4. Cached blobs auto-skipped (resume)
5. SHA256 mismatch → retry 3x → errors list
6. Progress reporting
7. cache clean --older-than Nd
8. Tests: cache hit/miss, parallelism, corrupt blob retried

### Story 4.3 - Transfer: Decrypt Request & Build Response

As an **operator**,
I want buncker-fetch to process requests and build encrypted responses,
so that the USB transfer cycle is complete.

**Acceptance Criteria:**
1. process_request() decrypts → HMAC verify → parse JSON → blob list
2. Invalid HMAC → CryptoError, nothing downloaded
3. build_response() builds OCI layout → HMAC → tar → encrypt
4. ERRORS.json included if blobs failed
5. Default naming: buncker-response-{datetime}-{source_id}.tar.enc
6. buncker_version check: include newer .deb if available
7. Tests: round-trip with crypto, ERRORS.json case

### Story 4.4 - CLI buncker-fetch

As an **operator**,
I want a complete CLI to manage the online side,
so that I can pair, inspect, fetch, and manage the cache.

**Acceptance Criteria:**
1. buncker-fetch pair: enter 12 words, derive keys, save config
2. buncker-fetch inspect: decrypt, display summary
3. buncker-fetch fetch: full cycle with --output and --parallelism options
4. buncker-fetch status: cache state
5. buncker-fetch cache clean: cleanup
6. Human-readable output by default, --json for machine output
7. All errors actionable with context
8. Tests: each subcommand tested

---

## Epic 5 - Packaging, E2E & Release

**Goal:** Build functional .deb packages, run end-to-end tests for the full cycle, document the project, and produce the first GitHub release.

### Story 5.1 - .deb Packaging

As a **release engineer**,
I want both components packaged as .deb,
so that they can be installed on Debian/Ubuntu with proper dependency management.

**Acceptance Criteria:**
1. make build-deb produces buncker and buncker-fetch .deb files
2. buncker.deb installs: /usr/bin/buncker, /usr/lib/buncker/, /etc/buncker/config.json, buncker.service
3. buncker-fetch.deb installs: /usr/bin/buncker-fetch, /usr/lib/buncker-fetch/
4. Depends: python3 (>= 3.11), python3-cryptography
5. postinst creates buncker user and directories
6. dpkg -i + systemctl enable --now = working daemon
7. shared/ copied in each .deb
8. GitHub Actions builds .deb as artifacts

### Story 5.2 - End-to-End Tests

As a **developer**,
I want automated e2e tests covering the full USB cycle,
so that we can release with confidence.

**Acceptance Criteria:**
1. E2E: setup → pair → analyze → generate → fetch (mock) → import → pull → blob verified
2. E2E: key rotation → new request → fetch with new key → import OK
3. E2E: corrupt blob in response → partial import, valid blobs kept
4. E2E: invalid HMAC → total refusal
5. All tests run in CI without real network (mock HTTP servers)
6. Total coverage >= 80%

### Story 5.3 - Documentation & First Release

As an **open source user**,
I want clear documentation,
so that I can understand, install, and contribute to Buncker.

**Acceptance Criteria:**
1. Complete README.md: description, features, architecture overview, installation, quick start, configuration, contributing
2. CONTRIBUTING.md: dev setup, linting, tests, PR process
3. LICENSE: Apache 2.0
4. Complete .gitignore
5. Git branching documented: main + feature branches + version tags
6. First tag v1.0.0 + GitHub Release with .deb assets
7. CHANGELOG.md initialized

---

## Epic 6 - Admin API Authentication & LAN Client Operations

**Goal:** Secure the admin API with optional Bearer token authentication and TLS, then enable LAN clients to perform all registry operations remotely via curl. At the end of this epic, an operator can run `buncker api-setup` to activate authentication and TLS, and LAN clients can analyze Dockerfiles, generate transfer manifests, and import responses using curl with Bearer tokens. Installations without `api-setup` remain unchanged (local HTTP, no auth).

### Story 6.1 - Token Generation & Storage

As an **operator**,
I want `buncker api-setup` to generate authentication tokens and activate TLS,
so that I can securely open the admin API to LAN clients.

**Acceptance Criteria:**
1. `buncker api-setup` generates two cryptographically random Bearer tokens (256-bit via `secrets.token_hex(32)`): one read-only, one admin
2. Tokens are stored in `/etc/buncker/api-tokens.json` with file permissions 0600
3. `api-setup` prompts for a TLS certificate (`--cert`, `--key` flags) or generates an auto-signed certificate with an explicit security warning
4. TLS is activated on the daemon (HTTPS on same port 5000)
5. The existing `buncker export-ca` mechanism is reused for auto-signed certificates
6. Config is updated with `api.enabled: true` and `tls: true`
7. The daemon refuses to start if `api.enabled: true` and `tls: false`
8. Both tokens are displayed once at setup, clearly labeled (read-only vs admin)
9. Tests: token file created with correct permissions, config updated, TLS activated

### Story 6.2 - Auth Middleware

As a **daemon**,
I want to validate Bearer tokens on admin endpoints,
so that only authorized clients can access registry operations.

**Acceptance Criteria:**
1. When `api.enabled: true`, all `/admin/*` requests must include `Authorization: Bearer <token>`
2. Read-only token grants access to: `GET /admin/status`, `GET /admin/logs`, `GET /admin/gc/report`
3. Admin token grants access to all `/admin/*` endpoints
4. `/v2/*` OCI endpoints remain unauthenticated regardless of auth configuration
5. Missing or invalid token returns 401 Unauthorized with a JSON error body
6. Read-only token on an admin-only endpoint returns 403 Forbidden
7. Token comparison uses constant-time comparison (`hmac.compare_digest`)
8. When `api.enabled: false` (no `api-setup` run), all endpoints behave as before (no auth)
9. Tests: valid RO token on RO endpoint, valid admin token on admin endpoint, RO token on admin endpoint rejected, invalid token rejected, no auth when disabled

### Story 6.3 - Audit Trail Enrichment

As an **operator**,
I want to see who accessed the API and from where,
so that I have full traceability of remote operations.

**Acceptance Criteria:**
1. All API log entries include `client_ip` (source IP of the request)
2. All API log entries include `auth_level`: `admin`, `readonly`, `local`, or `rejected`
3. All API log entries include `user_agent` (User-Agent header value)
4. Local CLI requests (via localhost) are logged with `auth_level: local`
5. Failed authentication attempts are logged with `auth_level: rejected`, `client_ip`, and `user_agent`
6. No token values are ever logged (existing NFR7 principle)
7. Tests: verify log entries contain new fields for each auth level scenario

### Story 6.4 - Token Management Commands

As an **operator**,
I want to view and regenerate API tokens after initial setup,
so that I can manage access without re-running full setup.

**Acceptance Criteria:**
1. `buncker api-show readonly` displays the current read-only token
2. `buncker api-show admin` displays the current admin token
3. `buncker api-reset readonly` generates a new read-only token, invalidates the previous one, and displays the new token
4. `buncker api-reset admin` generates a new admin token, invalidates the previous one, and displays the new token
5. All commands are local-only (CLI, not exposed via HTTP)
6. Token reset is logged as an audit event (`api_token_reset` with token type)
7. Tests: show displays correct token, reset changes token, old token rejected after reset

### Story 6.5 - Remote Dockerfile Analysis

As a **LAN client operator**,
I want to send a Dockerfile to the daemon for analysis via curl,
so that I can identify missing blobs without being on the buncker machine.

**Acceptance Criteria:**
1. `POST /admin/analyze` accepts `dockerfile_content` (string) in the JSON body for remote clients
2. `POST /admin/analyze` continues to accept `dockerfile_path` (string) for localhost requests only
3. If a remote request sends `dockerfile_path`, the daemon returns 400 with an explicit error: "Path-based analysis is only available from localhost. Send dockerfile_content instead."
4. `build_args` field works identically for both content and path modes
5. `POST /admin/generate-manifest` returns the encrypted `.json.enc` file directly in the response body (Content-Type: application/octet-stream) so the client can `curl -o request.json.enc`
6. Tests: remote with content, remote with path rejected, localhost with path OK, generate-manifest downloads file

### Story 6.6 - Streaming Import via PUT

As a **LAN client operator**,
I want to upload a response.tar.enc to the daemon via curl,
so that I can import blobs without physical access to the buncker machine.

**Acceptance Criteria:**
1. `PUT /admin/import` accepts a streaming upload of `response.tar.enc` (body = raw file content)
2. The daemon writes the uploaded file to disk in chunks (never loads entirely in memory)
3. `X-Buncker-Checksum: sha256:<hex>` header is required; the daemon verifies the checksum against the received file before attempting decryption
4. Checksum mismatch returns 400 with error: "Upload integrity check failed"
5. Resume support: the daemon accepts `Content-Range` headers and appends to a partial upload
6. `curl -C - -T response.tar.enc` resumes a previously interrupted upload
7. After successful upload and checksum verification, the existing import pipeline (decrypt, HMAC verify, SHA256 per blob, atomic store) runs as before
8. The existing `POST /admin/import` endpoint continues to work for local CLI usage
9. Tests: full upload, resumed upload, checksum mismatch rejected, successful import after upload

### Story 6.7 - E2E Tests & Documentation

As a **developer**,
I want end-to-end tests and updated documentation for the API auth feature,
so that we can release with confidence.

**Acceptance Criteria:**
1. E2E: `api-setup` with auto-signed cert, then curl with admin token: analyze (content) + generate-manifest (download) + import (PUT streaming) + pull via OCI
2. E2E: curl with read-only token: status OK, analyze rejected (403)
3. E2E: curl without token when auth enabled: 401 on all admin endpoints
4. E2E: `api-reset admin`, old token rejected, new token works
5. E2E: daemon without `api-setup`: all endpoints work without auth (backward compatible)
6. README updated: `buncker api-setup` in command reference, curl examples in quick start
7. Admin API endpoint reference updated with auth requirements and new endpoints (PUT import)
8. All tests run in CI without real network

---
