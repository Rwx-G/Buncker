# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffolding: `shared/`, `buncker/`, `buncker_fetch/`, `tests/`, `packaging/` directories
- `pyproject.toml` with ruff lint/format and pytest configuration
- GitHub Actions CI workflow (`ci.yml`) with lint and test jobs
- `Makefile` with `lint`, `test`, and `build` targets
- Placeholder test to verify CI pipeline end-to-end
- Crypto module (`shared/crypto.py`): AES-256-GCM encrypt/decrypt, PBKDF2 key derivation, HMAC-SHA256 sign/verify, BIP-39 mnemonic generation
- BIP-39 English wordlist (`shared/wordlist.py`): 2048 words embedded
- OCI module (`shared/oci.py`): parse manifests/indexes, select platform, build OCI Image Layout, verify blob digests

## [0.0.1] - 2026-03-04

### Added

- Product Requirements Document (PRD) with 5 epics and detailed stories
- Architecture document covering all components, workflows, data models, and API spec
- 19 implementation stories (1.1-1.4, 2.1-2.5, 3.1-3.4, 4.1-4.4, 5.1-5.3) - all QA-validated
- Repository setup: README, LICENSE (Apache 2.0), CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
- GitHub templates for issues (bug report, feature request) and pull requests
- Conventional Commits convention and branching strategy documented

[Unreleased]: https://github.com/Rwx-G/Buncker/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/Rwx-G/Buncker/releases/tag/v0.0.1
