"""Integration tests for buncker HTTP server - OCI Distribution API."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

from buncker.registry_client import ManifestCache
from buncker.server import BunckerServer
from buncker.store import Store


@pytest.fixture()
def store(tmp_path):
    """Create a temporary store."""
    return Store(tmp_path / "store")


@pytest.fixture()
def server(store):
    """Start a server on a random port and yield it."""
    srv = BunckerServer(bind="127.0.0.1", port=0, store=store)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture()
def base_url(server):
    """Return base URL for the running server."""
    return f"http://127.0.0.1:{server.port}"


def _get(url: str) -> tuple[int, bytes, dict]:
    """Make a GET request, return (status, body, headers)."""
    req = urllib.request.Request(url)
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, resp.read(), dict(resp.headers)
    except HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _head(url: str) -> tuple[int, dict]:
    """Make a HEAD request, return (status, headers)."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, dict(resp.headers)
    except HTTPError as e:
        return e.code, dict(e.headers)


class TestV2Root:
    """Tests for GET /v2/."""

    def test_v2_returns_200(self, base_url):
        status, body, headers = _get(f"{base_url}/v2/")
        assert status == 200
        data = json.loads(body)
        assert data == {"status": "ok"}

    def test_v2_has_api_version_header(self, base_url):
        status, body, headers = _get(f"{base_url}/v2/")
        assert headers.get("Docker-Distribution-API-Version") == "registry/2.0"

    def test_v2_without_trailing_slash(self, base_url):
        status, body, headers = _get(f"{base_url}/v2")
        assert status == 200


class TestManifests:
    """Tests for manifest endpoints."""

    def _setup_manifest(self, store, registry, repo, tag, platform, manifest):
        cache = ManifestCache(store.path)
        cache.cache_manifest(registry, repo, tag, platform, manifest)

    def test_manifest_get_existing(self, base_url, store):
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + "a" * 64,
                "size": 100,
            },
            "layers": [],
        }
        self._setup_manifest(store, "docker.io", "library/nginx", "1.25", "linux/amd64", manifest)

        status, body, headers = _get(f"{base_url}/v2/library/nginx/manifests/1.25")
        assert status == 200
        data = json.loads(body)
        assert data["schemaVersion"] == 2
        assert "_buncker" not in data
        assert "Docker-Content-Digest" in headers
        assert headers["Docker-Content-Digest"].startswith("sha256:")
        assert headers["Content-Type"] == "application/vnd.oci.image.manifest.v1+json"

    def test_manifest_get_missing(self, base_url):
        status, body, headers = _get(f"{base_url}/v2/library/nginx/manifests/nonexistent")
        assert status == 404

    def test_manifest_head_existing(self, base_url, store):
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + "b" * 64,
                "size": 50,
            },
            "layers": [],
        }
        self._setup_manifest(store, "docker.io", "library/alpine", "3.18", "linux/amd64", manifest)

        status, headers = _head(f"{base_url}/v2/library/alpine/manifests/3.18")
        assert status == 200
        assert "Docker-Content-Digest" in headers
        assert "Content-Length" in headers

    def test_manifest_head_missing(self, base_url):
        status, headers = _head(f"{base_url}/v2/library/missing/manifests/latest")
        assert status == 404

    def test_manifest_with_digest_reference(self, base_url, store):
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + "c" * 64,
                "size": 75,
            },
            "layers": [],
        }
        self._setup_manifest(store, "docker.io", "library/nginx", "1.25", "linux/amd64", manifest)

        # Compute the digest of the cached manifest
        raw = json.dumps(manifest, sort_keys=True).encode()
        digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"

        status, body, headers = _get(
            f"{base_url}/v2/library/nginx/manifests/{digest}"
        )
        assert status == 200


class TestBlobs:
    """Tests for blob endpoints."""

    def _import_blob(self, store, content: bytes) -> str:
        digest_hex = hashlib.sha256(content).hexdigest()
        digest = f"sha256:{digest_hex}"
        store.import_blob(content, digest)
        return digest

    def test_blob_get_existing(self, base_url, store):
        content = b"hello blob content"
        digest = self._import_blob(store, content)

        status, body, headers = _get(f"{base_url}/v2/library/nginx/blobs/{digest}")
        assert status == 200
        assert body == content
        assert headers["Docker-Content-Digest"] == digest
        assert headers["Content-Length"] == str(len(content))
        assert headers["Content-Type"] == "application/octet-stream"

    def test_blob_get_missing(self, base_url):
        digest = "sha256:" + "d" * 64
        status, body, headers = _get(f"{base_url}/v2/library/nginx/blobs/{digest}")
        assert status == 404

    def test_blob_head_existing(self, base_url, store):
        content = b"another blob"
        digest = self._import_blob(store, content)

        status, headers = _head(f"{base_url}/v2/library/nginx/blobs/{digest}")
        assert status == 200
        assert headers["Content-Length"] == str(len(content))

    def test_blob_head_missing(self, base_url):
        digest = "sha256:" + "e" * 64
        status, headers = _head(f"{base_url}/v2/library/nginx/blobs/{digest}")
        assert status == 404

    def test_blob_streaming_large(self, base_url, store):
        content = b"x" * 200_000
        digest = self._import_blob(store, content)

        status, body, headers = _get(f"{base_url}/v2/library/nginx/blobs/{digest}")
        assert status == 200
        assert len(body) == 200_000
        assert body == content

    def test_blob_get_updates_metadata(self, base_url, store):
        content = b"tracked blob"
        digest = self._import_blob(store, content)
        meta_before = store.get_metadata(digest)
        count_before = meta_before.get("request_count", 0)

        _get(f"{base_url}/v2/library/nginx/blobs/{digest}")
        time.sleep(0.1)  # Allow server thread to complete metadata update

        meta_after = store.get_metadata(digest)
        assert meta_after["request_count"] == count_before + 1


class TestInvalidInput:
    """Tests for input validation."""

    def test_path_traversal_rejected(self, base_url):
        status, body, headers = _get(f"{base_url}/v2/../etc/passwd/manifests/latest")
        assert status == 400

    def test_invalid_digest_rejected(self, base_url):
        status, body, headers = _get(f"{base_url}/v2/library/nginx/blobs/sha256:short")
        # Invalid digest won't match the blob route regex, so 404
        assert status == 404

    def test_invalid_tag_rejected(self, base_url):
        bad_tag = "a" * 129
        status, body, headers = _get(f"{base_url}/v2/library/nginx/manifests/{bad_tag}")
        assert status == 400


class TestUnmatchedRoutes:
    """Tests for unmatched routes."""

    def test_unknown_path_returns_404(self, base_url):
        status, body, headers = _get(f"{base_url}/unknown/path")
        assert status == 404

    def test_root_returns_404(self, base_url):
        status, body, headers = _get(f"{base_url}/")
        assert status == 404


class TestDockerPullSequence:
    """Simulate a Docker pull sequence."""

    def test_full_pull_sequence(self, base_url, store):
        # 1. Check /v2/
        status, body, _ = _get(f"{base_url}/v2/")
        assert status == 200

        # 2. Cache a manifest
        layer_content = b"fake layer data for pull test"
        layer_digest_hex = hashlib.sha256(layer_content).hexdigest()
        layer_digest = f"sha256:{layer_digest_hex}"
        store.import_blob(layer_content, layer_digest)

        config_content = b'{"architecture":"amd64"}'
        config_digest_hex = hashlib.sha256(config_content).hexdigest()
        config_digest = f"sha256:{config_digest_hex}"
        store.import_blob(config_content, config_digest)

        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": len(config_content),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": layer_digest,
                    "size": len(layer_content),
                }
            ],
        }
        cache = ManifestCache(store.path)
        cache.cache_manifest("docker.io", "library/nginx", "1.25", "linux/amd64", manifest)

        # 3. Fetch manifest
        status, body, headers = _get(f"{base_url}/v2/library/nginx/manifests/1.25")
        assert status == 200
        data = json.loads(body)

        # 4. Fetch config blob
        status, body, _ = _get(f"{base_url}/v2/library/nginx/blobs/{config_digest}")
        assert status == 200
        assert body == config_content

        # 5. Fetch layer blob
        status, body, _ = _get(f"{base_url}/v2/library/nginx/blobs/{layer_digest}")
        assert status == 200
        assert body == layer_content
