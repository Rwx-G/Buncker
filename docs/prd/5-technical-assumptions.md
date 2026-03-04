# 5. Technical Assumptions

## Repository Structure: Monorepo

Single GitHub repository containing both packages (`buncker/`, `buncker_fetch/`) and shared code (`shared/`). Both .deb are built from the same repo.

## Service Architecture

Two independent components, no network communication between them:
- **buncker**: permanent HTTP daemon (systemd) on isolated LAN
- **buncker-fetch**: one-shot CLI on a connected machine

Non-cloud, non-microservices, non-serverless. Two system binaries packaged as .deb.

## Testing Requirements

Full testing pyramid:
- Unit tests (70%): pytest + unittest.mock
- Integration tests (25%): inter-module workflows, local HTTP server, mock OCI registry
- E2E tests (5%): full cycle setup → analyze → generate → fetch → import → pull
- CI: ruff check → ruff format --check → pytest → coverage

## Additional Technical Assumptions

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
