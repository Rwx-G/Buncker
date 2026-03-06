"""E2E test: global flow demonstrating setup and both operating modes.

Flow:
  1. Setup (offline store + crypto keys)
  2. Full cycle WITHOUT API auth (backward-compatible USB flow)
  3. Full cycle WITH API auth (LAN client flow via HTTP + Bearer tokens)

Both modes go through the live HTTP server to prove end-to-end behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

from buncker.registry_client import ManifestCache
from buncker.resolver import resolve_dockerfile
from buncker.server import BunckerServer
from buncker.store import Store
from buncker_fetch.cache import Cache
from buncker_fetch.fetcher import Fetcher
from buncker_fetch.transfer import build_response, process_request
from shared.crypto import derive_keys, generate_mnemonic

from .conftest import (
    BLOB_A_DATA,
    BLOB_A_DIGEST,
    BLOB_B_DATA,
    BLOB_B_DIGEST,
    BLOB_REGISTRY,
    CONFIG_DATA,
    CONFIG_DIGEST,
    SAMPLE_MANIFEST,
)


def _make_mock_registry_client(mock_registry_addr: str):
    """Create a RegistryClient subclass that talks to mock HTTP server."""

    class MockRegistryClient:
        def __init__(self, registry: str, credentials=None):
            self.registry = registry
            self._base = f"http://{mock_registry_addr}"

        def fetch_manifest(self, repository: str, reference: str) -> dict:
            url = f"{self._base}/v2/{repository}/manifests/{reference}"
            resp = urllib.request.urlopen(url)
            return json.loads(resp.read())

        def fetch_blob(self, repository: str, digest: str):
            url = f"{self._base}/v2/{repository}/blobs/{digest}"
            resp = urllib.request.urlopen(url)
            data = resp.read()
            for offset in range(0, len(data), 65536):
                yield data[offset : offset + 65536]

    return MockRegistryClient


def _http_get(base: str, path: str, *, token: str | None = None) -> bytes:
    """GET request helper."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base}{path}", headers=headers)
    return urllib.request.urlopen(req).read()


def _http_post_json(
    base: str, path: str, body: dict, *, token: str | None = None
) -> bytes:
    """POST JSON request helper."""
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base}{path}", data=data, headers=headers)
    return urllib.request.urlopen(req).read()


def _http_post_raw(base: str, path: str, *, token: str | None = None) -> bytes:
    """POST with empty body - returns raw response bytes."""
    headers = {"Content-Type": "application/octet-stream", "Content-Length": "0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base}{path}", data=b"", headers=headers)
    return urllib.request.urlopen(req).read()


def _http_put(
    base: str,
    path: str,
    body: bytes,
    *,
    token: str | None = None,
    checksum: str | None = None,
) -> bytes:
    """PUT request helper."""
    headers = {"Content-Type": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if checksum:
        headers["X-Buncker-Checksum"] = checksum
    req = urllib.request.Request(
        f"{base}{path}", data=body, headers=headers, method="PUT"
    )
    return urllib.request.urlopen(req).read()


def _setup_store_with_manifest(store: Store) -> ManifestCache:
    """Cache the sample manifest in the store for resolver."""
    mc = ManifestCache(store.path)
    mc.cache_manifest(
        "test.registry.io", "myapp", "v1", "linux/amd64", SAMPLE_MANIFEST
    )
    return mc


def _fetch_blobs_online(
    mock_registry: str,
    blobs: list[dict],
    cache: Cache,
) -> None:
    """Simulate online side: fetch blobs from mock registry."""
    MockRC = _make_mock_registry_client(mock_registry)
    client = MockRC("test.registry.io")
    fetcher = Fetcher(client, cache, parallelism=2)
    result = fetcher.fetch(blobs)
    assert len(result.errors) == 0


@pytest.mark.e2e
class TestGlobalFlowNoAuth:
    """Full cycle through the HTTP server WITHOUT API auth (USB flow)."""

    def test_full_http_cycle_no_auth(self, tmp_path, mock_registry):
        """setup -> analyze -> generate-manifest -> fetch -> import -> OCI pull."""
        # -- Setup --
        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)

        store = Store(tmp_path / "store")
        mc = _setup_store_with_manifest(store)

        # Start server WITHOUT auth
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            crypto_keys=(aes_key, hmac_key),
            source_id="e2e-no-auth",
            api_enabled=False,
        )
        srv.start()
        base = f"http://127.0.0.1:{srv.port}"

        try:
            # 1. Status check - no token needed
            status = json.loads(_http_get(base, "/admin/status"))
            assert status["blob_count"] == 0
            assert status["source_id"] == "e2e-no-auth"

            # 2. Analyze via HTTP (path mode from localhost)
            dockerfile = tmp_path / "Dockerfile"
            dockerfile.write_text("FROM test.registry.io/myapp:v1\n")

            result = json.loads(
                _http_post_json(
                    base,
                    "/admin/analyze",
                    {"dockerfile": str(dockerfile)},
                )
            )
            assert len(result["missing_blobs"]) == 3
            assert len(result["images"]) == 1

            # 3. Generate manifest via HTTP -> encrypted request
            enc_request = _http_post_raw(base, "/admin/generate-manifest")
            assert len(enc_request) > 0

            # 4. Online side: decrypt request + fetch + build response
            req_path = tmp_path / "request.json.enc"
            req_path.write_bytes(enc_request)

            request_data = process_request(
                req_path, aes_key=aes_key, hmac_key=hmac_key
            )
            assert request_data["source_id"] == "e2e-no-auth"
            assert len(request_data["blobs"]) == 3

            cache = Cache(tmp_path / "online_cache")
            _fetch_blobs_online(mock_registry, request_data["blobs"], cache)

            response_path = build_response(
                cache,
                request_data["blobs"],
                [],
                aes_key=aes_key,
                hmac_key=hmac_key,
                source_id="e2e-no-auth",
                output_dir=tmp_path / "responses",
            )

            # 5. Import via POST (classic local CLI mode)
            enc_response = response_path.read_bytes()
            req = urllib.request.Request(
                f"{base}/admin/import",
                data=enc_response,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(enc_response)),
                },
            )
            import_result = json.loads(urllib.request.urlopen(req).read())
            assert import_result["imported"] == 3
            assert import_result["skipped"] == 0
            assert len(import_result["errors"]) == 0

            # 6. Verify via OCI /v2/ endpoints
            v2_resp = json.loads(_http_get(base, "/v2/"))
            assert v2_resp["status"] == "ok"

            # Check status shows blobs
            status = json.loads(_http_get(base, "/admin/status"))
            assert status["blob_count"] == 3

            # Pull blobs via OCI endpoint
            for digest, expected_data in BLOB_REGISTRY.items():
                hex_d = digest.removeprefix("sha256:")
                blob_bytes = _http_get(
                    base, f"/v2/test.registry.io/myapp/blobs/{digest}"
                )
                assert blob_bytes == expected_data
                actual_hash = hashlib.sha256(blob_bytes).hexdigest()
                assert actual_hash == hex_d

        finally:
            srv.stop()


@pytest.mark.e2e
class TestGlobalFlowWithAuth:
    """Full cycle through the HTTP server WITH API auth (LAN client flow)."""

    def test_full_http_cycle_with_auth(self, tmp_path, mock_registry):
        """api-setup -> analyze (content) -> generate -> fetch -> PUT import -> OCI pull."""
        # -- Setup --
        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)

        store = Store(tmp_path / "store")
        mc = _setup_store_with_manifest(store)

        tokens = {
            "readonly": "ro_" + os.urandom(30).hex(),
            "admin": "ad_" + os.urandom(30).hex(),
        }

        # Start server WITH auth
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            crypto_keys=(aes_key, hmac_key),
            source_id="e2e-auth",
            api_tokens=tokens,
            api_enabled=True,
        )
        srv.start()
        base = f"http://127.0.0.1:{srv.port}"

        try:
            # -- Verify auth enforcement --

            # No token -> 401 on admin endpoints
            with pytest.raises(HTTPError) as exc_info:
                _http_get(base, "/admin/status")
            assert exc_info.value.code == 401

            # RO token -> 200 on status, 403 on analyze
            status = json.loads(
                _http_get(base, "/admin/status", token=tokens["readonly"])
            )
            assert status["blob_count"] == 0

            with pytest.raises(HTTPError) as exc_info:
                _http_post_json(
                    base,
                    "/admin/analyze",
                    {"dockerfile_content": "FROM scratch\n"},
                    token=tokens["readonly"],
                )
            assert exc_info.value.code == 403

            # OCI /v2/ -> always open, no token needed
            v2_resp = json.loads(_http_get(base, "/v2/"))
            assert v2_resp["status"] == "ok"

            # -- Full cycle with admin token --

            # 1. Analyze via content mode (LAN client sends Dockerfile content)
            result = json.loads(
                _http_post_json(
                    base,
                    "/admin/analyze",
                    {"dockerfile_content": "FROM test.registry.io/myapp:v1\n"},
                    token=tokens["admin"],
                )
            )
            assert len(result["missing_blobs"]) == 3
            assert result["images"][0]["registry"] == "test.registry.io"

            # 2. Generate manifest with admin token
            req = urllib.request.Request(
                f"{base}/admin/generate-manifest",
                data=b"",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": "0",
                    "Authorization": f"Bearer {tokens['admin']}",
                },
            )
            enc_request = urllib.request.urlopen(req).read()
            assert len(enc_request) > 0

            # 3. Online side: decrypt + fetch + build response
            req_path = tmp_path / "request.json.enc"
            req_path.write_bytes(enc_request)

            request_data = process_request(
                req_path, aes_key=aes_key, hmac_key=hmac_key
            )
            assert request_data["source_id"] == "e2e-auth"
            assert len(request_data["blobs"]) == 3

            cache = Cache(tmp_path / "online_cache")
            _fetch_blobs_online(mock_registry, request_data["blobs"], cache)

            response_path = build_response(
                cache,
                request_data["blobs"],
                [],
                aes_key=aes_key,
                hmac_key=hmac_key,
                source_id="e2e-auth",
                output_dir=tmp_path / "responses",
            )

            # 4. Import via PUT with checksum (LAN client mode)
            enc_response = response_path.read_bytes()
            checksum = f"sha256:{hashlib.sha256(enc_response).hexdigest()}"

            # PUT without token -> 401
            with pytest.raises(HTTPError) as exc_info:
                _http_put(
                    base,
                    "/admin/import",
                    enc_response,
                    checksum=checksum,
                )
            assert exc_info.value.code == 401

            # PUT with RO token -> 403
            with pytest.raises(HTTPError) as exc_info:
                _http_put(
                    base,
                    "/admin/import",
                    enc_response,
                    token=tokens["readonly"],
                    checksum=checksum,
                )
            assert exc_info.value.code == 403

            # PUT with admin token + valid checksum -> success
            import_result = json.loads(
                _http_put(
                    base,
                    "/admin/import",
                    enc_response,
                    token=tokens["admin"],
                    checksum=checksum,
                )
            )
            assert import_result["imported"] == 3
            assert import_result["skipped"] == 0
            assert len(import_result["errors"]) == 0

            # 5. Verify blobs via OCI /v2/ (no token needed)
            status = json.loads(
                _http_get(base, "/admin/status", token=tokens["readonly"])
            )
            assert status["blob_count"] == 3

            for digest, expected_data in BLOB_REGISTRY.items():
                blob_bytes = _http_get(
                    base, f"/v2/test.registry.io/myapp/blobs/{digest}"
                )
                assert blob_bytes == expected_data

            # 6. Verify PUT with wrong checksum -> 400
            bad_checksum = "sha256:" + "0" * 64
            with pytest.raises(HTTPError) as exc_info:
                _http_put(
                    base,
                    "/admin/import",
                    enc_response,
                    token=tokens["admin"],
                    checksum=bad_checksum,
                )
            assert exc_info.value.code == 400

        finally:
            srv.stop()


@pytest.mark.e2e
class TestGlobalFlowAuthTransition:
    """Test transitioning from no-auth to auth mode."""

    def test_transition_no_auth_to_auth(self, tmp_path, mock_registry):
        """Server without auth -> stops -> restarts with auth -> old OCI data still served."""
        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)

        store = Store(tmp_path / "store")
        mc = _setup_store_with_manifest(store)

        # -- Phase 1: No auth - import blobs via USB flow --
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM test.registry.io/myapp:v1\n")
        result = resolve_dockerfile(dockerfile, {}, store=store, registry_client=mc)

        req_dir = tmp_path / "req"
        req_dir.mkdir()
        from buncker.transfer import generate_request, import_response

        req_path = generate_request(
            result.missing_blobs,
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="transition-test",
            output_dir=req_dir,
        )

        request_data = process_request(req_path, aes_key=aes_key, hmac_key=hmac_key)
        cache = Cache(tmp_path / "online_cache")
        _fetch_blobs_online(mock_registry, request_data["blobs"], cache)

        resp_path = build_response(
            cache,
            request_data["blobs"],
            [],
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="transition-test",
            output_dir=tmp_path / "responses",
        )

        import_result = import_response(
            resp_path, aes_key=aes_key, hmac_key=hmac_key, store=store
        )
        assert import_result["imported"] == 3

        # -- Phase 2: Enable auth and restart --
        tokens = {
            "readonly": "ro_" + os.urandom(30).hex(),
            "admin": "ad_" + os.urandom(30).hex(),
        }

        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            crypto_keys=(aes_key, hmac_key),
            source_id="transition-test",
            api_tokens=tokens,
            api_enabled=True,
        )
        srv.start()
        base = f"http://127.0.0.1:{srv.port}"

        try:
            # OCI still works without auth
            for digest, expected_data in BLOB_REGISTRY.items():
                blob_bytes = _http_get(
                    base, f"/v2/test.registry.io/myapp/blobs/{digest}"
                )
                assert blob_bytes == expected_data

            # Admin requires auth now
            with pytest.raises(HTTPError) as exc_info:
                _http_get(base, "/admin/status")
            assert exc_info.value.code == 401

            # Admin with token works
            status = json.loads(
                _http_get(base, "/admin/status", token=tokens["admin"])
            )
            assert status["blob_count"] == 3

        finally:
            srv.stop()
