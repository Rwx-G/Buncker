# 6. Epic List

## Epic 1 - Foundation & Crypto Core
Establish the project (repo, CI, structure), implement the shared crypto and OCI modules. First testable end-to-end pass: generate mnemonic, encrypt/decrypt, parse/build OCI layout.

## Epic 2 - Store & Resolver
Implement the local blob store (blobs + metadata + GC) and Dockerfile resolver (parsing, FROM resolution, blob diff). At the end, one can analyze a Dockerfile and get the list of missing blobs.

## Epic 3 - Daemon Offline (buncker)
Implement the HTTP server (OCI Distribution API + Admin API), transfer (request generation, response import), and systemd service. At the end, the daemon runs and serves Docker pulls.

## Epic 4 - CLI Online (buncker-fetch)
Implement the fetcher (auth discovery, download, cache), transfer (request decryption, response building), and complete CLI. At the end, the full USB cycle works.

## Epic 5 - Packaging, E2E & Release
Build .deb packages, run e2e tests for the full cycle, document the project, produce first GitHub release.

---
