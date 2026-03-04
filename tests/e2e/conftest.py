"""Shared fixtures for end-to-end tests."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from buncker.registry_client import ManifestCache
from buncker.store import Store
from buncker_fetch.cache import Cache
from shared.crypto import derive_keys, generate_mnemonic

# ---------------------------------------------------------------------------
# Sample blob data - random suffix ensures unique digests per test session
# ---------------------------------------------------------------------------

BLOB_A_DATA = b"layer-a-content-" + os.urandom(64)
BLOB_A_DIGEST = f"sha256:{hashlib.sha256(BLOB_A_DATA).hexdigest()}"

BLOB_B_DATA = b"layer-b-content-" + os.urandom(64)
BLOB_B_DIGEST = f"sha256:{hashlib.sha256(BLOB_B_DATA).hexdigest()}"

CONFIG_DATA = b'{"architecture":"amd64","os":"linux"}'
CONFIG_DIGEST = f"sha256:{hashlib.sha256(CONFIG_DATA).hexdigest()}"

SAMPLE_MANIFEST = {
    "schemaVersion": 2,
    "mediaType": "application/vnd.oci.image.manifest.v1+json",
    "config": {
        "mediaType": "application/vnd.oci.image.config.v1+json",
        "digest": CONFIG_DIGEST,
        "size": len(CONFIG_DATA),
    },
    "layers": [
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": BLOB_A_DIGEST,
            "size": len(BLOB_A_DATA),
        },
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": BLOB_B_DIGEST,
            "size": len(BLOB_B_DATA),
        },
    ],
}

BLOB_REGISTRY: dict[str, bytes] = {
    CONFIG_DIGEST: CONFIG_DATA,
    BLOB_A_DIGEST: BLOB_A_DATA,
    BLOB_B_DIGEST: BLOB_B_DATA,
}


# ---------------------------------------------------------------------------
# Mock OCI registry HTTP server
# ---------------------------------------------------------------------------


class _MockRegistryHandler(BaseHTTPRequestHandler):
    """Serves OCI manifests and blobs for e2e tests."""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Silence request logging

    def do_GET(self) -> None:
        # GET /v2/ - version check (no auth required for mock)
        if self.path == "/v2/" or self.path == "/v2":
            self._json_response(200, {"status": "ok"})
            return

        # GET /v2/{repo}/manifests/{ref}
        if "/manifests/" in self.path:
            body = json.dumps(SAMPLE_MANIFEST).encode()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/vnd.oci.image.manifest.v1+json",
            )
            self.send_header("Content-Length", str(len(body)))
            digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
            self.send_header("Docker-Content-Digest", digest)
            self.end_headers()
            self.wfile.write(body)
            return

        # GET /v2/{repo}/blobs/{digest}
        if "/blobs/" in self.path:
            digest = self.path.rsplit("/", 1)[-1]
            data = BLOB_REGISTRY.get(digest)
            if data is not None:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._json_response(
                404, {"errors": [{"code": "BLOB_UNKNOWN", "message": "not found"}]}
            )
            return

        self._json_response(404, {"errors": [{"code": "NOT_FOUND"}]})

    def _json_response(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="session")
def mock_registry():
    """Start a mock OCI registry HTTP server for the test session."""
    server = HTTPServer(("127.0.0.1", 0), _MockRegistryHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{port}"
    server.shutdown()


# ---------------------------------------------------------------------------
# Crypto fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mnemonic():
    """Generate a fresh BIP-39 mnemonic."""
    return generate_mnemonic()


@pytest.fixture()
def crypto_keys(mnemonic):
    """Derive AES and HMAC keys from the mnemonic."""
    salt = os.urandom(32)
    # Reduced iterations for test speed (production uses 600,000)
    aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)
    return aes_key, hmac_key, salt


# ---------------------------------------------------------------------------
# Store and cache fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def offline_store(tmp_path):
    """Create a fresh offline buncker store."""
    store_path = tmp_path / "offline_store"
    store_path.mkdir()
    return Store(store_path)


@pytest.fixture()
def manifest_cache(offline_store):
    """Create a ManifestCache backed by the offline store."""
    return ManifestCache(offline_store.path)


@pytest.fixture()
def online_cache(tmp_path):
    """Create a fresh online buncker-fetch cache."""
    cache_path = tmp_path / "online_cache"
    cache_path.mkdir()
    return Cache(cache_path)


# ---------------------------------------------------------------------------
# Dockerfile fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_dockerfile(tmp_path):
    """Create a sample Dockerfile referencing mock registry images."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM test.registry.io/myapp:v1\n")
    return dockerfile
