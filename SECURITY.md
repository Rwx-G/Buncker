# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability in Buncker, please report it responsibly.

**Do NOT open a public issue.**

Instead, send an email to **alerting@rwx-g.fr** or use [GitHub's private vulnerability reporting](https://github.com/Rwx-G/Buncker/security/advisories/new) with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You will receive an acknowledgment within 48 hours. We will work with you to understand and address the issue before any public disclosure.

## Scope

Security-relevant areas of Buncker include:

- Cryptographic operations (AES-256-GCM, HMAC-SHA256, PBKDF2, mnemonic generation)
- Transfer file integrity (request.json.enc, response.tar.enc)
- Store integrity (SHA256 blob verification, atomic writes)
- Input validation (digest format, path traversal prevention)
- Secret handling (mnemonic, derived keys, registry tokens)
