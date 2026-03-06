# 3. Requirements

## Functional

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

## Non Functional

- **NFR1:** Python >=3.11 as baseline. Only external dependency: `python3-cryptography` installed via apt. No pip.
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
