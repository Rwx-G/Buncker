# 13. Coding Standards

## Core Standards

- **Language:** Python >=3.11
- **Linting:** ruff (`E,F,W,I,UP,B,SIM`)
- **Tests:** pytest, `tests/` mirroring source structure

## Critical Rules

1. **No pip, no venv:** Only stdlib + `python3-cryptography`. Whitelist of allowed imports enforced.
2. **Atomic writes only:** All store writes via temp + verify + rename.
3. **SHA256 verify on every blob read/write:** No exceptions, no skip.
4. **No secrets in logs:** Never log mnemonic, keys, tokens.
5. **Errors must be actionable:** What failed + context + what to do.
6. **No internet fallback (offline):** No `urllib.request.urlopen` in `buncker/` package. Ever.
7. **OCI compliance:** Manifests, index, blobs follow OCI Image Spec. `_buncker` is the only allowed extension.
8. **HTTP responses match OCI Distribution Spec:** Required headers must be present and correct.

## Python Specifics

- Type hints on public signatures
- `@dataclass` for data structures crossing module boundaries
- f-strings only (no `%` or `.format()`)
- `pathlib.Path` for paths (except `os.rename` for atomic writes)

---
