# Buncker - Product Requirements Document (PRD)

> Version: 3.0 | Date: 2026-03-09 | Author: Romain G.

---

## 1. Goals and Background Context

### Goals

- **Enable Docker builds in 100% air-gapped environments** without internet access, reliably and reproducibly
- **Surgically synchronize** only the missing Docker layers (delta sync), not bulk snapshots
- **Secure the USB transfer channel** with AES-256 encryption and HMAC signatures, key shared via BIP-39 mnemonic
- **Provide a local OCI registry** transparent to Docker clients (standard pull, no client-side changes beyond hosts.toml)
- **Complete traceability**: audit trail of every operation (analysis, transfer, import, pull, GC)
- **Zero exotic dependencies**: Python stdlib + a single apt package (`python3-cryptography`), .deb packaging
- **Open source on GitHub** under Apache 2.0 license, owner-maintained

### Target Persona

**Infrastructure operator / DevOps engineer** working in a secured environment (defense, industry, isolated datacenter, critical infrastructure). Responsible for maintaining Docker build environments on air-gapped networks. Proficient in Linux, Docker, and system administration. Looking for a reliable, auditable, no-magic tool - not a "cloud-native" solution ill-suited to their context.

### Background Context

Air-gapped environments (defense, industry, critical infrastructure) need to build Docker images without internet access. Existing solutions like Hauler (SUSE/Rancher) do bulk snapshot-and-ship: they export complete images with no diff intelligence, no Dockerfile resolution, and no transfer channel encryption.

Buncker solves this with a surgical approach: a static Dockerfile resolver identifies missing layers, an encrypted transfer manifest is carried via USB to a connected machine, which fetches only the needed delta and returns an encrypted OCI package. All without ever compromising the offline environment's network isolation.

### Competitive Analysis

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

### Success Metrics

- **V1 Success:** Full USB cycle functional (analyze → generate → fetch → import → pull) with 0 verified corruption over 100 consecutive transfers
- **Integrity:** 100% of imported blobs pass SHA256 verification - no silent corruption
- **Delta efficiency:** >50% reduction in transferred volume compared to a full `docker save` on a multi-image case with shared layers
- **Adoption:** First functional installation by an external user (not the author) within 3 months of v1.0 release

### Change Log

| Date | Version | Description | Author |
|------|---------|-------------|--------|
| 2026-03-04 | 1.0 | Initial PRD from brainstorm + architecture | Romain G. |
| 2026-03-04 | 1.1 | Added persona, competitive analysis, success metrics, out-of-scope, license | Romain G. |
| 2026-03-04 | 1.2 | Translated to English (public repo) | Romain G. |
| 2026-03-06 | 2.0 | Added Epic 6: Admin API Authentication & LAN Client Operations | Romain G. |
| 2026-03-09 | 3.0 | Added Epic 7: Compose, RPM, log rotation, OCI auth restriction, manifest TTL | Romain G. |

---

## 2. Scope

### In Scope (V1 MVP)

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

### In Scope (V2 - Admin API Auth & LAN Client Operations)

- Optional Bearer token authentication on `/admin/*` endpoints via `buncker api-setup`
- Two access levels: read-only (status, logs, gc report) and admin (analyze, generate-manifest, import, gc execute)
- TLS activation (operator-provided certificate or auto-signed) mandatory when auth is enabled
- Token management commands (`api-show`, `api-reset`)
- Remote Dockerfile analysis via content upload (curl)
- Streaming import via PUT with resume support (`Content-Range`) and pre-decryption checksum (`X-Buncker-Checksum`)
- Enriched audit trail with `client_ip`, `auth_level`, `user_agent` on all API requests

### In Scope (V3 - Compose, Packaging, Hardening)

- Docker Compose analysis: `buncker analyze --compose docker-compose.yml` extracts `image:` and `build.dockerfile` from all services
- New dependency: `python3-yaml` (via apt) for YAML parsing
- RPM packaging for RHEL/Fedora (`.spec` files, `make build-rpm`)
- Log rotation via `logrotate.d/buncker` config shipped in .deb and .rpm
- `--restrict-oci` server flag to require read-only token on `/v2/*` endpoints
- Manifest cache TTL (default 30 days) with staleness warning in `buncker analyze` and `--refresh-stale` flag on `generate-manifest`

### Out of Scope (V4+)

- **Cosign / supply chain signatures** - optional `--verify-signature` flag on buncker-fetch
- **Multi-registry offline sync** - shared store via NFS/CIFS (filesystem config), later active replication
- **pip / PyPI packaging** - Not planned (contradicts zero-pip philosophy)
- **Web interface / dashboard** - Not planned. CLI + HTTP API suffice
- **External monitoring** (Prometheus, Grafana) - Not planned. Audit trail logs cover traceability
- **Helm charts / Kubernetes manifests support** - Docker images only
- **Windows / macOS native support** - buncker-fetch on Windows via WSL2 (documented), no native binary
- **Daemon auto-update** - The .deb can be included in response.tar.enc but installation remains manual

---

## 3. Requirements

### Functional

- **FR1:** The system statically analyzes one or more Dockerfiles and resolves FROM instructions into concrete OCI images (registry, repository, tag, digest, platform), supporting pre-FROM ARGs with defaults and overrides via `--build-arg`
- **FR2:** The system identifies missing Docker layers in the local store by comparing SHA256 digests and produces a detailed report (present/missing/size) before any action
- **FR3:** The system generates an encrypted transfer manifest (AES-256-GCM + HMAC-SHA256) containing the list of missing blobs, transportable via USB
- **FR4:** The online tool (buncker-fetch) decrypts the manifest, verifies its HMAC integrity, and downloads missing blobs from public registries by digest with configurable parallelization
- **FR5:** buncker-fetch produces an encrypted OCI Image Layout package (response.tar.enc) containing downloaded blobs, verified by SHA256
- **FR6:** The offline daemon imports a response.tar.enc by sequentially verifying: decryption → HMAC → SHA256 per blob → atomic storage in the store
- **FR7:** The offline daemon exposes the OCI Distribution API (pull subset: GET/HEAD manifests and blobs) to allow Docker clients to pull images without configuration changes (beyond hosts.toml)
- **FR8:** The offline daemon is a permanent HTTP service (systemd) simultaneously serving the OCI API to build clients and the administration API to the operator
- **FR9:** The system supports initial pairing via BIP-39 mnemonic (16 words: 12 secret + 4 salt) communicated through a human channel, with PBKDF2 derivation of AES and HMAC keys
- **FR10:** The system supports key rotation with a configurable grace period
- **FR11:** Blob GC is manual only: inactive candidates report → operator confirmation → deletion. Never automatic deletion
- **FR12:** The system produces structured JSON Lines logs (append-only) for every event: analysis, manifest generation, import, pull, GC, key rotation
- **FR13:** buncker-fetch maintains a local blob cache to avoid re-downloads (automatic resume)
- **FR14:** buncker-fetch supports inspection of a request.json.enc without downloading (inspect command)
- **FR15:** The request.json.enc includes the buncker version; buncker-fetch includes the updated .deb in response.tar.enc if a newer version exists
- **FR16:** The offline daemon NEVER attempts an external network connection. A missing blob = 404 error, no fallback
- **FR17:** `buncker api-setup` generates two Bearer tokens (read-only and admin), activates TLS, and persists tokens in a restricted file (mode 0600)
- **FR18:** The daemon validates Bearer tokens on `/admin/*` endpoints with two access levels: read-only (`status`, `logs`, `gc/report`) and admin (`analyze`, `generate-manifest`, `import`, `gc/execute`)
- **FR19:** `/v2/*` OCI Distribution endpoints remain unauthenticated regardless of auth configuration, so Docker clients can pull without token management
- **FR20:** `POST /admin/analyze` accepts Dockerfile content in the request body for remote clients; path-based analysis remains available for localhost requests only
- **FR21:** `PUT /admin/import` supports streaming upload of `response.tar.enc` with chunked write-to-disk and resume via `Content-Range` headers (`curl -C -`)
- **FR22:** The daemon verifies a `X-Buncker-Checksum: sha256:...` header on uploaded files before attempting decryption, to reject corrupted uploads early
- **FR23:** `buncker api-show readonly|admin` re-displays the specified token; `buncker api-reset readonly|admin` regenerates it and invalidates the previous one
- **FR24:** `buncker api-setup` activates TLS: accepts an operator-provided certificate (`--cert`, `--key`) or generates an auto-signed certificate with an explicit security warning. Reuses the existing `buncker export-ca` mechanism
- **FR25:** All API log entries include `client_ip`, `auth_level` (`admin`, `readonly`, `local`, `rejected`), and `user_agent` fields
- **FR26:** Failed authentication attempts (invalid or missing token) are logged with `auth_level: rejected` and do not reveal whether the token was close to valid
- **FR27:** `buncker analyze --compose <path>` parses a Docker Compose YAML file, extracts `image:` references and `build.dockerfile` paths from all services, and runs the resolver pipeline on each
- **FR28:** `--restrict-oci` server flag (config: `oci.restrict: true`) requires a valid read-only or admin Bearer token on `/v2/*` endpoints; Docker clients must configure registry auth via `hosts.toml`
- **FR29:** Manifest cache tracks `cached_at` timestamp; `buncker analyze` emits a warning for manifests older than `manifest_ttl` (default 30 days, configurable in config.json)
- **FR30:** `buncker generate-manifest --refresh-stale` includes stale manifests (TTL exceeded) in the transfer request so buncker-fetch re-downloads them

### Non Functional

- **NFR1:** Python >=3.11 as baseline. External dependencies: `python3-cryptography` and `python3-yaml` installed via apt. No pip.
- **NFR2:** .deb packaging for both components (buncker and buncker-fetch), with Depends: python3 (>= 3.11), python3-cryptography
- **NFR3:** The store uses the standard OCI Image Layout format. No database - everything is filesystem
- **NFR4:** Store writes are atomic (temp + verify SHA256 + rename). A crash never corrupts the store
- **NFR5:** Error messages are actionable: what failed + context + what to do
- **NFR6:** The daemon supports N simultaneous build clients via bounded ThreadPoolExecutor (max_workers configurable, default 16)
- **NFR7:** Complete audit trail: every operation is logged in JSON Lines with timestamp, event, context. Logs NEVER contain secrets (mnemonic, keys, tokens)
- **NFR8:** TLS optional and configurable on the internal LAN. TLS mandatory for buncker-fetch to public registries (native HTTPS). TLS mandatory when API authentication is enabled
- **NFR9:** The project is open source on GitHub with CI (GitHub Actions: ruff lint + pytest tests + .deb build)
- **NFR10:** Test coverage: 80% minimum overall, 100% on the crypto module
- **NFR11:** Bearer tokens are cryptographically random (256-bit, `secrets.token_hex(32)`) and stored with restricted file permissions (0600)
- **NFR12:** Streaming import handles files up to multiple GB without loading them entirely in memory (chunked read/write to disk)
- **NFR13:** The daemon refuses to start with API authentication enabled and TLS disabled, preventing token transmission in cleartext

---

## 4. User Interface Design Goals

**N/A** - Buncker is a CLI + HTTP daemon tool. No graphical interface.

---

## 5. Technical Assumptions

### Repository Structure: Monorepo

Single GitHub repository containing both packages (`buncker/`, `buncker_fetch/`) and shared code (`shared/`). Both .deb are built from the same repo.

### Service Architecture

Two independent components, no network communication between them:
- **buncker**: permanent HTTP daemon (systemd) on isolated LAN
- **buncker-fetch**: one-shot CLI on a connected machine

Non-cloud, non-microservices, non-serverless. Two system binaries packaged as .deb.

### Testing Requirements

Full testing pyramid:
- Unit tests (70%): pytest + unittest.mock
- Integration tests (25%): inter-module workflows, local HTTP server, mock OCI registry
- E2E tests (5%): full cycle setup → analyze → generate → fetch → import → pull
- CI: ruff check → ruff format --check → pytest → coverage

### Additional Technical Assumptions

- Python >=3.11 (Debian 12 bookworm baseline)
- Only external dependency: `python3-cryptography` via apt
- HTTP server: ThreadingHTTPServer + ThreadPoolExecutor (stdlib)
- HTTP client: urllib.request (stdlib)
- Crypto: AES-256-GCM via cryptography, HMAC-SHA256/PBKDF2 via stdlib
- No web framework (Flask, FastAPI, etc.)
- No database (filesystem OCI Image Layout)
- Packaging: .deb for both components
- Service manager: systemd with hardening
- Linting: ruff
- CI/CD: GitHub Actions
- License: Apache 2.0
- Git branching: main + feature branches, tags for releases
- Open source on GitHub, owner-maintained

---

## 6. Epic List

### Epic 1 - Foundation & Crypto Core
Establish the project (repo, CI, structure), implement the shared crypto and OCI modules. First testable end-to-end pass: generate mnemonic, encrypt/decrypt, parse/build OCI layout.

### Epic 2 - Store & Resolver
Implement the local blob store (blobs + metadata + GC) and Dockerfile resolver (parsing, FROM resolution, blob diff). At the end, one can analyze a Dockerfile and get the list of missing blobs.

### Epic 3 - Daemon Offline (buncker)
Implement the HTTP server (OCI Distribution API + Admin API), transfer (request generation, response import), and systemd service. At the end, the daemon runs and serves Docker pulls.

### Epic 4 - CLI Online (buncker-fetch)
Implement the fetcher (auth discovery, download, cache), transfer (request decryption, response building), and complete CLI. At the end, the full USB cycle works.

### Epic 5 - Packaging, E2E & Release
Build .deb packages, run e2e tests for the full cycle, document the project, produce first GitHub release.

### Epic 6 - Admin API Authentication & LAN Client Operations
Secure the admin API with Bearer token authentication and TLS, then enable LAN clients to perform registry operations (analyze, generate-manifest, import) remotely via curl. Optional setup via `buncker api-setup` - installations using local-only access remain unchanged.

### Epic 7 - Compose, Packaging, Hardening & Release 1.0
Docker Compose analysis, RPM packaging for enterprise Linux, log rotation, OCI auth restriction for high-security environments, manifest cache TTL for freshness control. Feature-complete release.

---

## 7. Epic Details

### Epic 1 - Foundation & Crypto Core

**Goal:** Establish project foundations (structure, CI, tooling) and deliver the two shared modules (crypto + OCI) fully tested. At the end of this epic, one can generate a mnemonic, derive keys, encrypt/decrypt data, sign/verify HMACs, and manipulate OCI Image Layout structures. CI runs (lint + tests).

#### Story 1.1 - Project Scaffolding & CI

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

#### Story 1.2 - Crypto Module (shared/crypto)

As an **operator**,
I want the cryptographic primitives implemented and tested,
so that all transfer security relies on proven, audited code.

**Acceptance Criteria:**
1. generate_mnemonic() returns 16 words from BIP-39 wordlist (12 secret + 4 salt) with secrets.token_bytes entropy
2. derive_keys(mnemonic, salt, iterations=1_200_000) returns tuple (aes_key, hmac_key) via PBKDF2-SHA256
3. encrypt(data, aes_key) encrypts with AES-256-GCM and returns nonce + ciphertext + tag
4. decrypt(data, aes_key) decrypts and verifies auth tag. Raises CryptoError if invalid
5. sign(data, hmac_key) returns HMAC-SHA256 hex digest
6. verify(data, hmac_key, signature) returns bool (constant-time comparison)
7. shared/wordlist.py contains the complete BIP-39 wordlist (2048 words) embedded
8. Unit tests: round-trip encrypt/decrypt, wrong key → CryptoError, valid/invalid HMAC, mnemonic has 16 valid words
9. 100% coverage on this module

#### Story 1.3 - OCI Module (shared/oci)

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

#### Story 1.4 - Exception Hierarchy & Logging Setup

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

### Epic 2 - Store & Resolver

**Goal:** Implement local OCI blob storage and Dockerfile resolver. At the end of this epic, one can analyze a Dockerfile and get the list of resolved images and missing blobs in the store. The store handles import, read, metadata, and GC reporting.

#### Story 2.1 - Store Core (import, read, metadata)

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

#### Story 2.2 - Store GC

As an **operator**,
I want to identify and clean up unused blobs,
so that disk space is managed without risking active images.

**Acceptance Criteria:**
1. gc_report(inactive_days) scans meta sidecars and returns candidates list
2. Shared blob is candidate only if ALL its image_refs are inactive
3. gc_execute(digests, operator) deletes blobs and sidecars, logs each deletion
4. gc_execute refuses to delete a digest not in the latest report
5. Tests: active blob not candidate, shared blob protected, effective deletion, log produced

#### Story 2.3 - Manifest Cache (registry_client offline)

As a **developer**,
I want cached OCI manifests readable from the store,
so that the resolver can work without network access.

**Acceptance Criteria:**
1. cache_manifest() stores in manifests/{registry}/{repo}/{tag}/{platform}.json with _buncker.cached_at
2. get_manifest() returns cached manifest or None
3. Digest reference lookup via _buncker.source_digest
4. Tests: cache, retrieve, miss → None, digest lookup

#### Story 2.4 - Dockerfile Parser

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

#### Story 2.5 - Resolver Pipeline (parser + cache + diff)

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

### Epic 3 - Daemon Offline (buncker)

**Goal:** Deliver the complete HTTP daemon: OCI API for Docker builds, Admin API for the operator, request generation, response import, configuration, and systemd service. At the end, buncker runs in production on an isolated LAN.

#### Story 3.1 - HTTP Server & OCI Distribution API

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

#### Story 3.2 - Admin API

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

#### Story 3.3 - Transfer: Generate Request & Import Response

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

#### Story 3.4 - Config, Setup & systemd

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

### Epic 4 - CLI Online (buncker-fetch)

**Goal:** Deliver the complete online tool: pairing, inspection, fetch with cache and parallelization, response building. At the end, the full USB cycle works end-to-end.

#### Story 4.1 - Registry Client Online (auth + fetch)

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

#### Story 4.2 - Fetcher & Cache

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

#### Story 4.3 - Transfer: Decrypt Request & Build Response

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

#### Story 4.4 - CLI buncker-fetch

As an **operator**,
I want a complete CLI to manage the online side,
so that I can pair, inspect, fetch, and manage the cache.

**Acceptance Criteria:**
1. buncker-fetch pair: enter 16 words, derive keys, save config
2. buncker-fetch inspect: decrypt, display summary
3. buncker-fetch fetch: full cycle with --output and --parallelism options
4. buncker-fetch status: cache state
5. buncker-fetch cache clean: cleanup
6. Human-readable output by default, --json for machine output
7. All errors actionable with context
8. Tests: each subcommand tested

---

### Epic 5 - Packaging, E2E & Release

**Goal:** Build functional .deb packages, run end-to-end tests for the full cycle, document the project, and produce the first GitHub release.

#### Story 5.1 - .deb Packaging

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

#### Story 5.2 - End-to-End Tests

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

#### Story 5.3 - Documentation & First Release

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

### Epic 6 - Admin API Authentication & LAN Client Operations

**Goal:** Secure the admin API with optional Bearer token authentication and TLS, then enable LAN clients to perform all registry operations remotely via curl. At the end of this epic, an operator can run `buncker api-setup` to activate authentication and TLS, and LAN clients can analyze Dockerfiles, generate transfer manifests, and import responses using curl with Bearer tokens. Installations without `api-setup` remain unchanged (local HTTP, no auth).

#### Story 6.1 - Token Generation & Storage

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

#### Story 6.2 - Auth Middleware

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

#### Story 6.3 - Audit Trail Enrichment

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

#### Story 6.4 - Token Management Commands

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

#### Story 6.5 - Remote Dockerfile Analysis

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

#### Story 6.6 - Streaming Import via PUT

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

#### Story 6.7 - E2E Tests & Documentation

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

### Epic 7 - Compose, Packaging, Hardening & Release 1.0

**Goal:** Deliver Docker Compose support, RPM packaging, log rotation, OCI auth restriction, and manifest cache TTL. At the end of this epic, Buncker is feature-complete for v1.0.0: it handles both single Dockerfiles and Compose projects, ships on Debian and RHEL, and provides full security hardening and operational maturity.

#### Story 7.1 - Docker Compose Analysis

As an **operator**,
I want to analyze a `docker-compose.yml` file to extract all image references,
so that I can synchronize all images needed for a multi-service project in one operation.

**Acceptance Criteria:**
1. `buncker analyze --compose docker-compose.yml` parses the YAML file and extracts `image:` fields from all services
2. Services with `build.dockerfile` have their Dockerfile resolved through the existing resolver pipeline
3. Services with both `image:` and `build:` use the `image:` reference (same as Docker Compose behavior for pre-built images)
4. Services with only `build.context` (no explicit `dockerfile`) default to `Dockerfile` in the build context directory
5. The Compose file is validated: missing `services:` key or empty services returns an actionable error
6. `POST /admin/analyze` accepts `compose_content` (string) for remote clients alongside existing `dockerfile_content`
7. The analysis result aggregates all images with deduplication (same image used by multiple services counted once)
8. `python3-yaml` dependency added to .deb control files
9. Tests: multi-service Compose file, mixed image+build services, deduplication, invalid YAML, remote content mode

#### Story 7.2 - RPM Packaging

As a **RHEL/Fedora operator**,
I want `.rpm` packages for buncker and buncker-fetch,
so that I can install them on enterprise Linux with native package management.

**Acceptance Criteria:**
1. `packaging/buncker/rpm/buncker.spec` and `packaging/buncker-fetch/rpm/buncker-fetch.spec` define RPM specs
2. `make build-rpm` produces `.rpm` files in `dist/` using `rpmbuild`
3. RPM Requires: `python3 >= 3.11`, `python3-cryptography`, `python3-pyyaml`
4. buncker.rpm installs same file layout as .deb: `/usr/bin/buncker`, `/usr/lib/buncker/`, `/etc/buncker/config.json`, `buncker.service`
5. buncker-fetch.rpm installs: `/usr/bin/buncker-fetch`, `/usr/lib/buncker-fetch/`
6. `%post` scriptlet creates buncker user and directories (mirrors .deb postinst)
7. `logrotate.d/buncker` config included in buncker.rpm (shared with Story 7.3)
8. CI: `build-rpm` job in GitHub Actions using Fedora container, RPM artifacts uploaded
9. Tests: RPM metadata validation (Name, Version, Requires), file list verification

#### Story 7.3 - Log Rotation

As an **operator**,
I want automatic log rotation for Buncker logs,
so that disk space is managed without manual intervention.

**Acceptance Criteria:**
1. `packaging/buncker/debian/logrotate` provides a `logrotate.d/buncker` config file
2. Config rotates `/var/log/buncker/*.log` daily, keeps 30 days, compresses old logs with gzip
3. Rotation uses `copytruncate` (daemon keeps file handle open, no signal-based rotation needed)
4. The logrotate config is installed to `/etc/logrotate.d/buncker` by both .deb and .rpm packages
5. .deb `conffiles` updated to include the logrotate config (preserved on upgrade)
6. Tests: verify logrotate config syntax is valid (`logrotate -d`), config file present in package

#### Story 7.4 - OCI Auth Restriction

As a **security-conscious operator**,
I want to require authentication on OCI pull endpoints,
so that only authorized Docker clients can pull images in high-security environments.

**Acceptance Criteria:**
1. `--restrict-oci` flag on `buncker serve` enables auth on `/v2/*` endpoints
2. Config option `oci.restrict: true` persisted in `config.json` (default: `false`)
3. When enabled, `/v2/*` endpoints require a valid read-only or admin Bearer token
4. `GET /v2/` returns 401 with `WWW-Authenticate: Bearer realm="buncker"` when no valid token is present (standard OCI auth challenge)
5. Docker clients authenticate via `hosts.toml` configuration with the read-only token
6. When disabled (default), `/v2/*` behavior is unchanged (unauthenticated, backward compatible)
7. README updated with `hosts.toml` configuration example for restricted mode
8. Tests: pull with valid token succeeds, pull without token returns 401 with proper challenge, default mode unchanged

#### Story 7.5 - Manifest Cache TTL & Staleness

As an **operator**,
I want to know when cached manifests are outdated,
so that I can re-fetch them to stay in sync with upstream registries.

**Acceptance Criteria:**
1. `manifest_ttl` config option (integer, days, default 30) in `config.json`
2. `buncker analyze` emits a warning for each manifest whose `_buncker.cached_at` is older than `manifest_ttl` days
3. Warning format: `"Manifest for {image}:{tag} is {N} days old (TTL: {ttl}d) - consider using --refresh-stale"`
4. `buncker generate-manifest --refresh-stale` includes stale manifest digests in the transfer request with a `refresh: true` flag
5. `buncker-fetch` re-downloads manifests flagged with `refresh: true` even if already in local cache
6. Updated manifests are included in `response.tar.enc` and imported back into the offline manifest cache
7. `GET /admin/status` includes `stale_manifests` count in the response
8. Tests: fresh manifest no warning, stale manifest triggers warning, --refresh-stale includes stale in request, buncker-fetch re-fetches flagged manifests

---

## 8. Next Steps

### Architect Prompt

Architecture document already created at `docs/architecture.md`. No further action needed - architecture was produced before this PRD.

### UX Expert Prompt

N/A - No user interface. CLI-only project.
