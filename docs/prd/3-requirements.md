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

## Non Functional

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
