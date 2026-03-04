"""Integration tests for buncker HTTP server - OCI + Admin API."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

from buncker.registry_client import ManifestCache
from buncker.server import BunckerServer
from buncker.store import Store
from shared.crypto import derive_keys, generate_mnemonic


@pytest.fixture()
def store(tmp_path):
    """Create a temporary store."""
    return Store(tmp_path / "store")


@pytest.fixture()
def crypto_keys():
    """Generate crypto keys for testing."""
    mnemonic = generate_mnemonic()
    salt = os.urandom(32)
    return derive_keys(mnemonic, salt, iterations=1000)


@pytest.fixture()
def server(store, tmp_path):
    """Start a server on a random port and yield it."""
    log_path = tmp_path / "buncker.log"
    srv = BunckerServer(
        bind="127.0.0.1",
        port=0,
        store=store,
        source_id="test-buncker",
        log_path=log_path,
    )
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture()
def server_with_crypto(store, crypto_keys, tmp_path):
    """Start a server with crypto keys."""
    log_path = tmp_path / "buncker.log"
    srv = BunckerServer(
        bind="127.0.0.1",
        port=0,
        store=store,
        crypto_keys=crypto_keys,
        source_id="test-buncker",
        log_path=log_path,
    )
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


def _post(url: str, data: dict | None = None) -> tuple[int, bytes, dict]:
    """Make a POST request with JSON body."""
    body = json.dumps(data).encode() if data is not None else b""
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
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


# ==================================================================
# Admin API tests (Story 3.2)
# ==================================================================


class TestAdminStatus:
    """Tests for GET /admin/status."""

    def test_status_returns_expected_fields(self, base_url):
        status, body, _ = _get(f"{base_url}/admin/status")
        assert status == 200
        data = json.loads(body)
        assert "version" in data
        assert "source_id" in data
        assert "store_path" in data
        assert "blob_count" in data
        assert "total_size" in data
        assert "uptime" in data

    def test_status_blob_count(self, base_url, store):
        content = b"status test blob"
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        store.import_blob(content, digest)

        status, body, _ = _get(f"{base_url}/admin/status")
        data = json.loads(body)
        assert data["blob_count"] >= 1
        assert data["total_size"] >= len(content)

    def test_status_source_id(self, base_url):
        status, body, _ = _get(f"{base_url}/admin/status")
        data = json.loads(body)
        assert data["source_id"] == "test-buncker"


class TestAdminAnalyze:
    """Tests for POST /admin/analyze."""

    def test_analyze_valid_dockerfile(self, base_url, store, tmp_path):
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM nginx:1.25\n")

        # Cache a manifest
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + "a" * 64,
                "size": 100,
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": "sha256:" + "b" * 64,
                    "size": 5000,
                }
            ],
        }
        cache = ManifestCache(store.path)
        cache.cache_manifest("docker.io", "library/nginx", "1.25", "linux/amd64", manifest)

        status, body, _ = _post(
            f"{base_url}/admin/analyze",
            {"dockerfile": str(dockerfile)},
        )
        assert status == 200
        data = json.loads(body)
        assert "images" in data
        assert "missing_blobs" in data
        assert "warnings" in data
        assert len(data["images"]) == 1

    def test_analyze_missing_dockerfile_field(self, base_url):
        status, body, _ = _post(f"{base_url}/admin/analyze", {})
        assert status == 400

    def test_analyze_path_traversal(self, base_url):
        status, body, _ = _post(
            f"{base_url}/admin/analyze",
            {"dockerfile": "../../../etc/passwd"},
        )
        assert status == 400

    def test_analyze_nonexistent_dockerfile(self, base_url):
        status, body, _ = _post(
            f"{base_url}/admin/analyze",
            {"dockerfile": "/nonexistent/Dockerfile"},
        )
        assert status == 400


class TestAdminGenerateManifest:
    """Tests for POST /admin/generate-manifest."""

    def test_generate_without_analysis_returns_409(self, base_url):
        status, body, _ = _post(f"{base_url}/admin/generate-manifest", {})
        assert status == 409

    def test_analyze_then_generate(self, tmp_path, store, crypto_keys):
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            crypto_keys=crypto_keys,
            source_id="test-gen",
        )
        srv.start()
        url = f"http://127.0.0.1:{srv.port}"

        try:
            # Create Dockerfile
            dockerfile = tmp_path / "Dockerfile"
            dockerfile.write_text("FROM nginx:1.25\n")

            # Cache manifest with missing blob
            manifest = {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": "sha256:" + "a" * 64,
                    "size": 100,
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                        "digest": "sha256:" + "b" * 64,
                        "size": 5000,
                    }
                ],
            }
            cache = ManifestCache(store.path)
            cache.cache_manifest("docker.io", "library/nginx", "1.25", "linux/amd64", manifest)

            # Analyze
            status, body, _ = _post(
                f"{url}/admin/analyze",
                {"dockerfile": str(dockerfile)},
            )
            assert status == 200

            # Generate
            status, body, headers = _post(f"{url}/admin/generate-manifest", {})
            assert status == 200
            assert "Content-Disposition" in headers
            assert "buncker-request-" in headers["Content-Disposition"]
            assert len(body) > 0

            # Decrypt and verify
            from shared.crypto import decrypt, verify

            aes_key, hmac_key = crypto_keys
            decrypted = decrypt(body, aes_key)
            parts = decrypted.rsplit(b"\n", 1)
            json_bytes = parts[0]
            sig = parts[1].decode()
            assert verify(json_bytes, hmac_key, sig)
            request_data = json.loads(json_bytes)
            assert request_data["version"] == "1"
            assert request_data["source_id"] == "test-gen"
            assert len(request_data["blobs"]) > 0
        finally:
            srv.stop()


class TestAdminImport:
    """Tests for POST /admin/import."""

    def test_import_returns_501(self, base_url):
        status, body, _ = _post(f"{base_url}/admin/import", {})
        assert status == 501


class TestAdminGc:
    """Tests for GC endpoints."""

    def test_gc_report_empty(self, base_url):
        status, body, _ = _get(f"{base_url}/admin/gc/report?inactive_days=1")
        assert status == 200
        data = json.loads(body)
        assert data["count"] == 0

    def test_gc_report_with_candidates(self, base_url, store):
        # Import a blob and backdate it
        content = b"gc test blob"
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        store.import_blob(content, digest)

        # Backdate the sidecar
        meta = store.get_metadata(digest)
        meta["last_requested"] = "2020-01-01T00:00:00+00:00"
        import tempfile
        digest_hex = digest.removeprefix("sha256:")
        sidecar = store.path / "meta" / "sha256" / f"{digest_hex}.json"
        sidecar.write_text(json.dumps(meta))

        status, body, _ = _get(f"{base_url}/admin/gc/report?inactive_days=1")
        data = json.loads(body)
        assert data["count"] >= 1

    def test_gc_execute_valid(self, base_url, store):
        content = b"gc exec blob"
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        store.import_blob(content, digest)

        # Backdate
        meta = store.get_metadata(digest)
        meta["last_requested"] = "2020-01-01T00:00:00+00:00"
        digest_hex = digest.removeprefix("sha256:")
        sidecar = store.path / "meta" / "sha256" / f"{digest_hex}.json"
        sidecar.write_text(json.dumps(meta))

        # Run report first
        _get(f"{base_url}/admin/gc/report?inactive_days=1")

        # Execute
        status, body, _ = _post(
            f"{base_url}/admin/gc/execute",
            {"digests": [digest], "operator": "test-op"},
        )
        assert status == 200
        data = json.loads(body)
        assert data["count"] == 1

    def test_gc_execute_missing_fields(self, base_url):
        status, body, _ = _post(f"{base_url}/admin/gc/execute", {})
        assert status == 400

    def test_gc_execute_invalid_operator(self, base_url):
        status, body, _ = _post(
            f"{base_url}/admin/gc/execute",
            {"digests": ["sha256:" + "a" * 64], "operator": "bad operator!"},
        )
        assert status == 400

    def test_gc_execute_invalid_digest(self, base_url):
        status, body, _ = _post(
            f"{base_url}/admin/gc/execute",
            {"digests": ["invalid"], "operator": "test-op"},
        )
        assert status == 400


class TestAdminLogs:
    """Tests for GET /admin/logs."""

    def test_logs_empty(self, base_url):
        status, body, _ = _get(f"{base_url}/admin/logs")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)

    def test_logs_with_filter(self, base_url, server, tmp_path):
        # Write some fake log entries
        log_path = server.log_path
        entries = [
            json.dumps({"ts": "2026-03-04T10:00:00+00:00", "event": "server_started", "level": "INFO"}),
            json.dumps({"ts": "2026-03-04T10:01:00+00:00", "event": "gc_executed", "level": "INFO"}),
            json.dumps({"ts": "2026-03-04T10:02:00+00:00", "event": "server_started", "level": "INFO"}),
        ]
        log_path.write_text("\n".join(entries) + "\n")

        # Filter by event
        status, body, _ = _get(f"{base_url}/admin/logs?event=gc_executed")
        data = json.loads(body)
        assert len(data) == 1
        assert data[0]["event"] == "gc_executed"

    def test_logs_with_limit(self, base_url, server):
        log_path = server.log_path
        entries = [
            json.dumps({"ts": f"2026-03-04T10:0{i}:00+00:00", "event": "test", "level": "INFO"})
            for i in range(5)
        ]
        log_path.write_text("\n".join(entries) + "\n")

        status, body, _ = _get(f"{base_url}/admin/logs?limit=2")
        data = json.loads(body)
        assert len(data) == 2

    def test_logs_with_since(self, base_url, server):
        log_path = server.log_path
        entries = [
            json.dumps({"ts": "2026-03-04T10:00:00+00:00", "event": "old", "level": "INFO"}),
            json.dumps({"ts": "2026-03-04T12:00:00+00:00", "event": "new", "level": "INFO"}),
        ]
        log_path.write_text("\n".join(entries) + "\n")

        status, body, _ = _get(f"{base_url}/admin/logs?since=2026-03-04T11:00:00%2B00:00")
        data = json.loads(body)
        assert len(data) == 1
        assert data[0]["event"] == "new"


class TestAdminInputValidation:
    """Tests for admin API input validation."""

    def test_analyze_empty_body(self, base_url):
        req = urllib.request.Request(
            f"{base_url}/admin/analyze",
            data=b"",
            headers={"Content-Type": "application/json", "Content-Length": "0"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req)
            status = resp.status
        except HTTPError as e:
            status = e.code
        assert status == 400

    def test_analyze_invalid_json(self, base_url):
        req = urllib.request.Request(
            f"{base_url}/admin/analyze",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req)
            status = resp.status
        except HTTPError as e:
            status = e.code
        assert status == 400
