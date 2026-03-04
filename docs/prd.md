# Buncker - Product Requirements Document (PRD)

> Version: 1.0 | Date: 2026-03-04 | Author: Romain G.

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

### Out of Scope (V2+)

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
- **FR9:** The system supports initial pairing via BIP-39 mnemonic (12 words) communicated through a human channel, with PBKDF2 derivation of AES and HMAC keys
- **FR10:** The system supports key rotation with a configurable grace period
- **FR11:** Blob GC is manual only: inactive candidates report → operator confirmation → deletion. Never automatic deletion
- **FR12:** The system produces structured JSON Lines logs (append-only) for every event: analysis, manifest generation, import, pull, GC, key rotation
- **FR13:** buncker-fetch maintains a local blob cache to avoid re-downloads (automatic resume)
- **FR14:** buncker-fetch supports inspection of a request.json.enc without downloading (inspect command)
- **FR15:** The request.json.enc includes the buncker version; buncker-fetch includes the updated .deb in response.tar.enc if a newer version exists
- **FR16:** The offline daemon NEVER attempts an external network connection. A missing blob = 404 error, no fallback

### Non Functional

- **NFR1:** Python >=3.11 as baseline. Only external dependency: `python3-cryptography` installed via apt. No pip.
- **NFR2:** .deb packaging for both components (buncker and buncker-fetch), with Depends: python3 (>= 3.11), python3-cryptography
- **NFR3:** The store uses the standard OCI Image Layout format. No database - everything is filesystem
- **NFR4:** Store writes are atomic (temp + verify SHA256 + rename). A crash never corrupts the store
- **NFR5:** Error messages are actionable: what failed + context + what to do
- **NFR6:** The daemon supports N simultaneous build clients via bounded ThreadPoolExecutor (max_workers configurable, default 16)
- **NFR7:** Complete audit trail: every operation is logged in JSON Lines with timestamp, event, context. Logs NEVER contain secrets (mnemonic, keys, tokens)
- **NFR8:** TLS optional and configurable on the internal LAN. TLS mandatory for buncker-fetch to public registries (native HTTPS)
- **NFR9:** The project is open source on GitHub with CI (GitHub Actions: ruff lint + pytest tests + .deb build)
- **NFR10:** Test coverage: 80% minimum overall, 100% on the crypto module

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
1. generate_mnemonic() returns 12 words from BIP-39 wordlist (2048 words) with secrets.token_bytes entropy
2. derive_keys(mnemonic, salt, iterations=600_000) returns tuple (aes_key, hmac_key) via PBKDF2-SHA256
3. encrypt(data, aes_key) encrypts with AES-256-GCM and returns nonce + ciphertext + tag
4. decrypt(data, aes_key) decrypts and verifies auth tag. Raises CryptoError if invalid
5. sign(data, hmac_key) returns HMAC-SHA256 hex digest
6. verify(data, hmac_key, signature) returns bool (constant-time comparison)
7. shared/wordlist.py contains the complete BIP-39 wordlist (2048 words) embedded
8. Unit tests: round-trip encrypt/decrypt, wrong key → CryptoError, valid/invalid HMAC, mnemonic has 12 valid words
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
1. buncker-fetch pair: enter 12 words, derive keys, save config
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

## 8. Next Steps

### Architect Prompt

Architecture document already created at `docs/architecture.md`. No further action needed - architecture was produced before this PRD.

### UX Expert Prompt

N/A - No user interface. CLI-only project.
