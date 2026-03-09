"""API token generation, persistence, TLS, and auth middleware."""

from __future__ import annotations

import contextlib
import hmac as _hmac
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

_log = logging.getLogger("buncker.auth")

_DEFAULT_TOKENS_PATH = Path("/etc/buncker/api-tokens.json")


def generate_api_tokens() -> dict[str, str]:
    """Generate a pair of 256-bit Bearer tokens.

    Returns:
        Dict with 'readonly' and 'admin' hex-encoded tokens.
    """
    return {
        "readonly": secrets.token_hex(32),
        "admin": secrets.token_hex(32),
    }


def save_api_tokens(tokens: dict[str, str], path: Path | None = None) -> Path:
    """Write tokens to disk with restrictive permissions (0600).

    Args:
        tokens: Dict with 'readonly' and 'admin' keys.
        path: Target file path. Defaults to /etc/buncker/api-tokens.json.

    Returns:
        The path where tokens were saved.
    """
    token_path = path or _DEFAULT_TOKENS_PATH
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        token_path.chmod(0o600)
    return token_path


def load_api_tokens(path: Path | None = None) -> dict[str, str] | None:
    """Load API tokens from disk.

    Args:
        path: Token file path. Defaults to /etc/buncker/api-tokens.json.

    Returns:
        Dict with 'readonly' and 'admin' keys, or None if file doesn't exist.
    """
    token_path = path or _DEFAULT_TOKENS_PATH
    if not token_path.exists():
        return None
    raw = token_path.read_text(encoding="utf-8")
    return json.loads(raw)


def generate_self_signed_cert(tls_dir: Path) -> tuple[Path, Path, Path]:
    """Generate a self-signed CA and server certificate.

    Creates:
        - ca.pem / ca-key.pem: CA certificate and private key
        - server.pem / server-key.pem: Server cert signed by CA

    Args:
        tls_dir: Directory to write certificate files.

    Returns:
        Tuple of (cert_path, key_path, ca_path).
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    tls_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(tz=UTC)
    ten_years = timedelta(days=3650)

    # Generate CA key and certificate
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Buncker CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Buncker"),
        ]
    )
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + ten_years)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # Generate server key and certificate
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "buncker"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Buncker"),
        ]
    )
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + ten_years)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("buncker"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress_from_string("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # Write files
    ca_path = tls_dir / "ca.pem"
    ca_key_path = tls_dir / "ca-key.pem"
    cert_path = tls_dir / "server.pem"
    key_path = tls_dir / "server-key.pem"

    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    ca_key_path.write_bytes(
        ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    # Restrict key file permissions
    for p in (ca_key_path, key_path):
        with contextlib.suppress(OSError):
            p.chmod(0o600)

    return cert_path, key_path, ca_path


def ipaddress_from_string(addr: str):
    """Convert string to ipaddress object for SAN extension."""
    import ipaddress

    return ipaddress.IPv4Address(addr)


# -- Auth Middleware (Story 6.2) --

# Endpoint access matrix: path -> (method, required_level)
_READONLY_ENDPOINTS = {
    ("/admin/status", "GET"),
    ("/admin/health", "GET"),
    ("/admin/logs", "GET"),
    ("/admin/gc/report", "GET"),
}

_ADMIN_ENDPOINTS = {
    ("/admin/analyze", "POST"),
    ("/admin/generate-manifest", "POST"),
    ("/admin/import", "POST"),
    ("/admin/import", "PUT"),
    ("/admin/gc/impact", "POST"),
    ("/admin/gc/execute", "POST"),
}


def get_required_level(path: str, method: str) -> str | None:
    """Return the required auth level for an endpoint.

    Returns:
        'readonly', 'admin', or None if the endpoint doesn't need auth.
    """
    key = (path, method)
    if key in _READONLY_ENDPOINTS:
        return "readonly"
    if key in _ADMIN_ENDPOINTS:
        return "admin"
    # Unknown admin endpoint defaults to admin
    if path.startswith("/admin/"):
        return "admin"
    return None


def authenticate_request(
    handler,
    tokens: dict[str, str] | None,
    api_enabled: bool,
) -> str:
    """Authenticate an HTTP request and return the auth level.

    Args:
        handler: The BaseHTTPRequestHandler instance.
        tokens: Loaded API tokens dict, or None if not configured.
        api_enabled: Whether API auth is enabled in config.

    Returns:
        Auth level string: 'admin', 'readonly', or 'local'.

    Raises:
        AuthError: If authentication fails (401 or 403).
    """
    path = handler.path.split("?")[0]
    method = handler.command

    # OCI endpoints are always unauthenticated
    if path.startswith("/v2"):
        return "local"

    required_level = get_required_level(path, method)

    # If auth is not enabled, all endpoints are open
    if not api_enabled or tokens is None:
        return "local"

    # No auth required for this endpoint
    if required_level is None:
        return "local"

    # Extract Bearer token
    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise AuthError(401, "Authentication required", "AUTH_REQUIRED")

    token = auth_header[7:]  # Strip "Bearer "

    # Check admin token first (grants full access)
    if _hmac.compare_digest(token, tokens.get("admin", "")):
        return "admin"

    # Check readonly token
    if _hmac.compare_digest(token, tokens.get("readonly", "")):
        if required_level == "admin":
            raise AuthError(403, "Insufficient permissions", "FORBIDDEN")
        return "readonly"

    # Invalid token
    raise AuthError(401, "Authentication required", "AUTH_REQUIRED")


class AuthError(Exception):
    """Raised when authentication fails."""

    def __init__(self, status: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
