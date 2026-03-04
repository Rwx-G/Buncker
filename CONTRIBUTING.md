# Contributing to Buncker

## Development Setup

```bash
# Clone the repo
git clone https://github.com/Rwx-G/Buncker.git
cd Buncker

# Install dev dependencies
sudo apt install python3 python3-cryptography python3-pytest
pip install ruff

# Verify setup
make lint
make test
```

## Project Structure

```
buncker/
├── shared/          # Crypto + OCI modules (used by both packages)
├── buncker/         # Offline daemon
├── buncker_fetch/   # Online CLI
├── tests/           # Tests mirroring source structure
├── packaging/       # .deb build files
└── docs/            # PRD, architecture, stories
```

## Code Standards

- **Python >= 3.11**, type hints on public signatures
- **No pip, no venv** - only stdlib + `python3-cryptography` (apt)
- **Linting:** ruff (rules: E, F, W, I, UP, B, SIM)
- **Formatting:** ruff format
- **Tests:** pytest, 80% coverage minimum, 100% on crypto module

## Commit Convention

This project uses [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <description>
```

**Types:** `feat`, `fix`, `docs`, `refactor`, `test`, `ci`, `chore`

**Scopes:** `crypto`, `oci`, `store`, `resolver`, `server`, `fetch`, `packaging`, `ci`

Examples:

```
feat(resolver): add multi-arch platform selection
fix(store): handle atomic write on full disk
test(crypto): add round-trip encrypt/decrypt tests
docs: update quick start in README
ci: add .deb build step to workflow
```

## Branching

- `main` - stable, release-ready
- `feat/<name>` - new features
- `fix/<name>` - bug fixes

### Version Tags

Releases are tagged with semantic versions: `v0.5.0`, `v0.6.0`, etc.
Tags trigger the CI build-deb job, producing `.deb` artifacts uploaded to GitHub Releases.

## Workflow

1. Create a feature branch from `main`
2. Make your changes with conventional commits
3. Run `make lint` and `make test`
4. Open a PR with a clear description

## Testing

```bash
# Run all tests
make test

# Run specific test file
python -m pytest tests/shared/test_crypto.py -v

# Run with coverage
python -m pytest --cov=shared --cov=buncker --cov=buncker_fetch
```

## Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

Update `CHANGELOG.md` under `[Unreleased]` when your PR introduces a notable change. Use these section headers:

- **Added** - new features
- **Changed** - changes to existing functionality
- **Deprecated** - features that will be removed
- **Removed** - features that were removed
- **Fixed** - bug fixes
- **Security** - vulnerability fixes

## Key Rules

- **Atomic writes only** for store operations (temp + verify SHA256 + rename)
- **Never log secrets** (mnemonic, keys, tokens)
- **No internet access** in `buncker/` package - ever
- **Actionable errors** - what failed + context + what to do
- **OCI compliance** - manifests and blobs follow OCI Image Spec

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
