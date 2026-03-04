"""E2E test: full USB transfer cycle.

Tests the complete flow:
  setup -> pair -> analyze -> generate-manifest -> fetch -> import -> pull
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from buncker.registry_client import ManifestCache
from buncker.resolver import resolve_dockerfile
from buncker.store import Store
from buncker.transfer import generate_request, import_response
from buncker_fetch.cache import Cache
from buncker_fetch.fetcher import Fetcher
from buncker_fetch.transfer import build_response, process_request
from shared.crypto import derive_keys, generate_mnemonic

from .conftest import (
    BLOB_REGISTRY,
    SAMPLE_MANIFEST,
)


@pytest.mark.e2e
class TestFullCycle:
    """Test the complete USB transfer cycle end-to-end."""

    def test_full_usb_cycle(self, tmp_path, mock_registry):
        """Full cycle: setup -> pair -> analyze -> generate -> fetch -> import."""
        # ---------------------------------------------------------------
        # Step 1: SETUP (offline side) - generate mnemonic, init store
        # ---------------------------------------------------------------
        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)

        store_path = tmp_path / "offline_store"
        store_path.mkdir()
        store = Store(store_path)

        # ---------------------------------------------------------------
        # Step 2: PAIR (online side) - derive same keys
        # ---------------------------------------------------------------
        aes_key_online, hmac_key_online = derive_keys(mnemonic, salt, iterations=1000)
        assert aes_key == aes_key_online
        assert hmac_key == hmac_key_online

        # ---------------------------------------------------------------
        # Step 3: ANALYZE - cache manifest, parse Dockerfile, find missing
        # ---------------------------------------------------------------
        # Cache the manifest in the offline store so resolver can find it
        manifest_cache = ManifestCache(store.path)
        manifest_cache.cache_manifest(
            "test.registry.io",
            "myapp",
            "v1",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )

        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM test.registry.io/myapp:v1\n")

        result = resolve_dockerfile(
            dockerfile,
            {},
            store=store,
            registry_client=manifest_cache,
        )

        assert len(result.missing_blobs) == 3  # config + 2 layers
        assert result.total_missing_size > 0

        # ---------------------------------------------------------------
        # Step 4: GENERATE-MANIFEST (offline) - encrypted request
        # ---------------------------------------------------------------
        req_dir = tmp_path / "requests"
        req_dir.mkdir()
        request_path = generate_request(
            result.missing_blobs,
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="test-buncker",
            output_dir=req_dir,
        )
        assert request_path.exists()
        assert request_path.suffix == ".enc"

        # ---------------------------------------------------------------
        # Step 5: FETCH (online) - decrypt request, fetch blobs, build response
        # ---------------------------------------------------------------
        # Process the encrypted request on the online side
        request_data = process_request(
            request_path,
            aes_key=aes_key_online,
            hmac_key=hmac_key_online,
        )
        assert request_data["source_id"] == "test-buncker"
        blobs = request_data["blobs"]
        assert len(blobs) == 3

        # Fetch blobs using mock registry
        cache_path = tmp_path / "online_cache"
        cache_path.mkdir()
        cache = Cache(cache_path)

        # Use a mock RegistryClient that fetches from mock HTTP server
        _MockRC = _make_mock_registry_client(mock_registry)
        client = _MockRC("test.registry.io")
        fetcher = Fetcher(client, cache, parallelism=2)
        fetch_result = fetcher.fetch(blobs)

        assert len(fetch_result.downloaded) == 3
        assert len(fetch_result.errors) == 0

        # Build encrypted response
        response_path = build_response(
            cache,
            blobs,
            [],
            aes_key=aes_key_online,
            hmac_key=hmac_key_online,
            source_id="test-buncker",
            output_dir=tmp_path / "responses",
        )
        assert response_path.exists()

        # ---------------------------------------------------------------
        # Step 6: IMPORT (offline) - decrypt response, import blobs
        # ---------------------------------------------------------------
        import_result = import_response(
            response_path,
            aes_key=aes_key,
            hmac_key=hmac_key,
            store=store,
        )
        assert import_result["imported"] == 3
        assert import_result["skipped"] == 0
        assert len(import_result["errors"]) == 0

        # ---------------------------------------------------------------
        # Step 7: VERIFY - blobs are accessible in the store
        # ---------------------------------------------------------------
        for digest, expected_data in BLOB_REGISTRY.items():
            assert store.has_blob(digest)
            blob_path = store.get_blob(digest)
            actual_data = blob_path.read_bytes()
            actual_hash = hashlib.sha256(actual_data).hexdigest()
            expected_hash = digest.removeprefix("sha256:")
            assert actual_hash == expected_hash
            assert actual_data == expected_data


def _make_mock_registry_client(mock_registry_addr: str):
    """Create a RegistryClient subclass that talks to mock HTTP server."""
    import urllib.request

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
