"""Tests for buncker.registry_client - Manifest Cache (Story 2.3)."""

import json
from datetime import UTC, datetime, timedelta

from buncker.registry_client import ManifestCache

SAMPLE_MANIFEST = {
    "schemaVersion": 2,
    "mediaType": "application/vnd.oci.image.manifest.v1+json",
    "config": {
        "mediaType": "application/vnd.oci.image.config.v1+json",
        "digest": "sha256:" + "a" * 64,
        "size": 1234,
    },
    "layers": [
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": "sha256:" + "b" * 64,
            "size": 56789,
        }
    ],
}


class TestCacheManifest:
    """Tests for ManifestCache.cache_manifest."""

    def test_writes_to_correct_path(self, tmp_path):
        cache = ManifestCache(tmp_path)

        path = cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )

        expected = (
            tmp_path
            / "manifests"
            / "docker.io"
            / "library/nginx"
            / "1.25"
            / "linux-amd64.json"
        )
        assert path == expected
        assert path.exists()

    def test_buncker_extension_present(self, tmp_path):
        cache = ManifestCache(tmp_path)

        path = cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )

        data = json.loads(path.read_text())
        assert "_buncker" in data
        assert "cached_at" in data["_buncker"]
        assert "source_digest" in data["_buncker"]
        assert data["_buncker"]["source_digest"].startswith("sha256:")

    def test_preserves_manifest_content(self, tmp_path):
        cache = ManifestCache(tmp_path)

        path = cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )

        data = json.loads(path.read_text())
        assert data["schemaVersion"] == 2
        assert data["config"] == SAMPLE_MANIFEST["config"]
        assert data["layers"] == SAMPLE_MANIFEST["layers"]


class TestGetManifest:
    """Tests for ManifestCache.get_manifest."""

    def test_retrieves_cached_manifest_by_tag(self, tmp_path):
        cache = ManifestCache(tmp_path)
        cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )

        result = cache.get_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
        )

        assert result is not None
        assert result["schemaVersion"] == 2
        assert "_buncker" in result

    def test_returns_none_for_miss(self, tmp_path):
        cache = ManifestCache(tmp_path)

        result = cache.get_manifest(
            "docker.io",
            "library/nginx",
            "9.99",
            "linux/amd64",
        )

        assert result is None

    def test_returns_none_for_empty_store(self, tmp_path):
        cache = ManifestCache(tmp_path)

        result = cache.get_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
        )

        assert result is None


class TestDigestLookup:
    """Tests for digest reference lookup."""

    def test_finds_manifest_by_source_digest(self, tmp_path):
        cache = ManifestCache(tmp_path)
        path = cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )

        data = json.loads(path.read_text())
        digest = data["_buncker"]["source_digest"]

        result = cache.get_manifest(
            "docker.io",
            "library/nginx",
            digest,
            "linux/amd64",
        )

        assert result is not None
        assert result["_buncker"]["source_digest"] == digest

    def test_digest_lookup_returns_none_on_miss(self, tmp_path):
        cache = ManifestCache(tmp_path)

        result = cache.get_manifest(
            "docker.io",
            "library/nginx",
            "sha256:" + "f" * 64,
            "linux/amd64",
        )

        assert result is None

    def test_different_platforms_distinct(self, tmp_path):
        cache = ManifestCache(tmp_path)
        cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )

        result = cache.get_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/arm64",
        )

        assert result is None


class TestIsStale:
    """Tests for ManifestCache.is_stale (Story 7.5)."""

    def test_returns_none_for_uncached_manifest(self, tmp_path):
        cache = ManifestCache(tmp_path)
        result = cache.is_stale("docker.io", "library/nginx", "1.25", "linux/amd64", 30)
        assert result is None

    def test_fresh_manifest_is_not_stale(self, tmp_path):
        cache = ManifestCache(tmp_path)
        cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )
        result = cache.is_stale("docker.io", "library/nginx", "1.25", "linux/amd64", 30)
        assert result is False

    def test_old_manifest_is_stale(self, tmp_path):
        cache = ManifestCache(tmp_path)
        path = cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )
        # Backdate the cached_at timestamp by 45 days
        data = json.loads(path.read_text())
        old_date = datetime.now(tz=UTC) - timedelta(days=45)
        data["_buncker"]["cached_at"] = old_date.isoformat()
        path.write_text(json.dumps(data, indent=2))

        result = cache.is_stale("docker.io", "library/nginx", "1.25", "linux/amd64", 30)
        assert result is True

    def test_manifest_without_cached_at_is_stale(self, tmp_path):
        cache = ManifestCache(tmp_path)
        path = cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )
        # Remove cached_at
        data = json.loads(path.read_text())
        del data["_buncker"]["cached_at"]
        path.write_text(json.dumps(data, indent=2))

        result = cache.is_stale("docker.io", "library/nginx", "1.25", "linux/amd64", 30)
        assert result is True


class TestCountStale:
    """Tests for ManifestCache.count_stale (Story 7.5)."""

    def test_empty_cache_returns_zero(self, tmp_path):
        cache = ManifestCache(tmp_path)
        assert cache.count_stale(30) == 0

    def test_counts_stale_manifests(self, tmp_path):
        cache = ManifestCache(tmp_path)
        # Fresh manifest
        cache.cache_manifest(
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )
        # Stale manifest (backdate to 45 days ago)
        path = cache.cache_manifest(
            "docker.io",
            "library/alpine",
            "3.19",
            "linux/amd64",
            SAMPLE_MANIFEST,
        )
        data = json.loads(path.read_text())
        old_date = datetime.now(tz=UTC) - timedelta(days=45)
        data["_buncker"]["cached_at"] = old_date.isoformat()
        path.write_text(json.dumps(data, indent=2))

        assert cache.count_stale(30) == 1

    def test_status_includes_stale_count(self, tmp_path):
        """GET /admin/status includes stale_manifests count."""
        import urllib.request

        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            manifest_ttl=30,
        )
        srv.start()
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/status"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            assert "stale_manifests" in data
            assert data["stale_manifests"] == 0
        finally:
            srv.stop()
