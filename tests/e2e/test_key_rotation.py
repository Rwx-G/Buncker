"""E2E test: key rotation scenario.

Tests: complete cycle -> rotate keys -> new cycle with new keys succeeds.
"""

from __future__ import annotations

import json
import os
import urllib.request

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


def _mock_registry_client(mock_addr: str):
    """Create a mock RegistryClient for the mock HTTP server."""

    class MockRC:
        def __init__(self, registry, credentials=None):
            self.registry = registry
            self._base = f"http://{mock_addr}"

        def fetch_manifest(self, repository, reference):
            url = f"{self._base}/v2/{repository}/manifests/{reference}"
            resp = urllib.request.urlopen(url)
            return json.loads(resp.read())

        def fetch_blob(self, repository, digest):
            url = f"{self._base}/v2/{repository}/blobs/{digest}"
            resp = urllib.request.urlopen(url)
            data = resp.read()
            for offset in range(0, len(data), 65536):
                yield data[offset : offset + 65536]

    return MockRC


def _run_cycle(
    mnemonic: str,
    salt: bytes,
    store: Store,
    cache: Cache,
    mock_registry: str,
    tmp_path,
    cycle_name: str,
):
    """Run a full fetch-import cycle and return import result."""
    aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)

    # Cache manifest if not already present
    mc = ManifestCache(store.path)
    mc.cache_manifest("test.registry.io", "myapp", "v1", "linux/amd64", SAMPLE_MANIFEST)

    # Analyze
    dockerfile = tmp_path / f"Dockerfile-{cycle_name}"
    dockerfile.write_text("FROM test.registry.io/myapp:v1\n")
    result = resolve_dockerfile(dockerfile, {}, store=store, registry_client=mc)

    if not result.missing_blobs:
        return {"imported": 0, "skipped": 0, "errors": []}

    # Generate request
    req_dir = tmp_path / f"req-{cycle_name}"
    req_dir.mkdir(exist_ok=True)
    req_path = generate_request(
        result.missing_blobs,
        aes_key=aes_key,
        hmac_key=hmac_key,
        source_id="rotation-test",
        output_dir=req_dir,
    )

    # Online side: process request with same keys
    aes_key_o, hmac_key_o = derive_keys(mnemonic, salt, iterations=1000)
    request_data = process_request(req_path, aes_key=aes_key_o, hmac_key=hmac_key_o)

    # Fetch blobs
    MockRC = _mock_registry_client(mock_registry)
    client = MockRC("test.registry.io")
    fetcher = Fetcher(client, cache, parallelism=2)
    fetcher.fetch(request_data["blobs"])

    # Build response
    resp_path = build_response(
        cache,
        request_data["blobs"],
        [],
        aes_key=aes_key_o,
        hmac_key=hmac_key_o,
        source_id="rotation-test",
        output_dir=tmp_path / f"resp-{cycle_name}",
    )

    # Import
    return import_response(resp_path, aes_key=aes_key, hmac_key=hmac_key, store=store)


@pytest.mark.e2e
class TestKeyRotation:
    """Test key rotation: old keys -> new keys -> cycle succeeds."""

    def test_key_rotation_new_cycle(self, tmp_path, mock_registry):
        """After key rotation, a new cycle with new keys works."""
        store_path = tmp_path / "store"
        store_path.mkdir()
        store = Store(store_path)

        cache_path = tmp_path / "cache"
        cache_path.mkdir()
        cache = Cache(cache_path)

        # Initial cycle with original keys
        mnemonic_1 = generate_mnemonic()
        salt_1 = os.urandom(32)

        result_1 = _run_cycle(
            mnemonic_1, salt_1, store, cache, mock_registry, tmp_path, "cycle1"
        )
        assert result_1["imported"] == 3

        # Verify blobs are in store
        for digest in BLOB_REGISTRY:
            assert store.has_blob(digest)

        # Rotate keys: generate new mnemonic and salt
        mnemonic_2 = generate_mnemonic()
        salt_2 = os.urandom(32)

        # Verify new keys are different
        aes_1, hmac_1 = derive_keys(mnemonic_1, salt_1, iterations=1000)
        aes_2, hmac_2 = derive_keys(mnemonic_2, salt_2, iterations=1000)
        assert aes_1 != aes_2
        assert hmac_1 != hmac_2

        # Delete blobs from store to force re-import
        for digest in BLOB_REGISTRY:
            hex_d = digest.removeprefix("sha256:")
            blob_path = store_path / "blobs" / "sha256" / hex_d
            if blob_path.exists():
                blob_path.unlink()
            meta_path = store_path / "meta" / "sha256" / f"{hex_d}.json"
            if meta_path.exists():
                meta_path.unlink()

        # New cycle with rotated keys
        result_2 = _run_cycle(
            mnemonic_2, salt_2, store, cache, mock_registry, tmp_path, "cycle2"
        )
        assert result_2["imported"] == 3

        # Blobs accessible with store
        for digest, data in BLOB_REGISTRY.items():
            assert store.has_blob(digest)
            actual = store.get_blob(digest).read_bytes()
            assert actual == data
