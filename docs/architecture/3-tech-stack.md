# 3. Tech Stack

## Cloud Infrastructure

**N/A** - Buncker is 100% on-premise by design. No cloud provider, no SaaS.

## Technology Stack Table

| Category | Technology | Version | Purpose | Rationale |
|----------|-----------|---------|---------|-----------|
| **Language** | Python | >=3.11 | Sole language for both packages | Rich stdlib, native on Debian 12 |
| **Crypto** | python3-cryptography | >=41.0 (apt) | AES-256-GCM, optional TLS cert gen | Only external dependency. Installed via apt, not pip |
| **HTTP Server** | ThreadingHTTPServer + ThreadPoolExecutor | stdlib | Permanent daemon for OCI + Admin API | Zero dependency. Bounded thread pool (max_workers=16 configurable) |
| **HTTP Client** | urllib.request | stdlib | Fetch blobs from public registries | Native HTTPS, certificate verification by default |
| **Hashing** | hashlib | stdlib | SHA256 (blob digests), PBKDF2 (key derivation) | Standard, performant |
| **Auth Signatures** | hmac | stdlib | HMAC-SHA256 (transfer manifest integrity) | stdlib, standard crypto |
| **Concurrency** | concurrent.futures | stdlib | ThreadPoolExecutor for parallel fetch | stdlib, simple, sufficient for I/O-bound |
| **Logging** | logging | stdlib | Structured JSON Lines logs | stdlib, configurable handlers |
| **Arg Parsing** | argparse | stdlib | CLI for both tools | stdlib, subcommands support |
| **Packaging (offline)** | dpkg / .deb | - | buncker distribution on Debian/Ubuntu | Native apt dependency management |
| **Packaging (online)** | dpkg / .deb | - | buncker-fetch distribution on Debian/Ubuntu | Same format, consistency |
| **Service Manager** | systemd | - | buncker offline daemon | Standard on Debian 12+, auto-restart, journald |
| **Testing** | pytest | 8.x | Unit and integration tests | Dev only, not in .deb |
| **Linting** | ruff | 0.8.x | Linting + formatting | Fast, replaces flake8+black+isort |

---
