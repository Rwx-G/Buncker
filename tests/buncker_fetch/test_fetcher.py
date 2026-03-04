"""Tests for buncker_fetch.fetcher."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

from buncker_fetch.cache import Cache
from buncker_fetch.fetcher import Fetcher, FetchResult
from shared.exceptions import RegistryError


def _make_blob_descriptor(content: bytes, repository: str = "library/test") -> dict:
    """Create a blob descriptor with valid digest."""
    hex_digest = hashlib.sha256(content).hexdigest()
    return {
        "registry": "docker.io",
        "repository": repository,
        "digest": f"sha256:{hex_digest}",
        "size": len(content),
        "media_type": "application/vnd.oci.image.layer.v1.tar+gzip",
        "_content": content,  # for mock setup
    }


def _setup_mock_client(blobs: list[dict]) -> MagicMock:
    """Create a mock RegistryClient that returns blob content."""
    client = MagicMock()

    def fetch_blob(repository, digest):
        for blob in blobs:
            if blob["digest"] == digest:
                return iter([blob["_content"]])
        raise RegistryError(f"Blob not found: {digest}")

    client.fetch_blob = MagicMock(side_effect=fetch_blob)
    return client


class TestFetcherSkipsCached:
    def test_skips_cached_blobs(self, tmp_path):
        cache = Cache(tmp_path)
        content = b"already cached"
        blob = _make_blob_descriptor(content)
        cache.store_blob(blob["digest"], content)

        client = _setup_mock_client([blob])
        fetcher = Fetcher(client, cache)
        result = fetcher.fetch([blob])

        assert blob["digest"] in result.skipped
        assert len(result.downloaded) == 0
        client.fetch_blob.assert_not_called()


class TestFetcherDownloads:
    def test_downloads_missing_blobs(self, tmp_path):
        cache = Cache(tmp_path)
        content = b"new blob content"
        blob = _make_blob_descriptor(content)

        client = _setup_mock_client([blob])
        fetcher = Fetcher(client, cache)
        result = fetcher.fetch([blob])

        assert blob["digest"] in result.downloaded
        assert cache.has_blob(blob["digest"])

    def test_parallel_downloads(self, tmp_path):
        cache = Cache(tmp_path)
        blobs = []
        for i in range(8):
            content = f"blob-{i}".encode()
            blobs.append(_make_blob_descriptor(content))

        client = _setup_mock_client(blobs)
        fetcher = Fetcher(client, cache, parallelism=4)
        result = fetcher.fetch(blobs)

        assert len(result.downloaded) == 8
        assert len(result.errors) == 0
        for blob in blobs:
            assert cache.has_blob(blob["digest"])


class TestFetcherRetry:
    def test_sha256_mismatch_triggers_retry(self, tmp_path):
        cache = Cache(tmp_path)
        content = b"correct content"
        blob = _make_blob_descriptor(content)

        client = MagicMock()
        call_count = 0

        def fetch_blob_with_corruption(repository, digest):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return iter([b"corrupted data"])
            return iter([content])

        client.fetch_blob = MagicMock(side_effect=fetch_blob_with_corruption)
        fetcher = Fetcher(client, cache, parallelism=1)
        result = fetcher.fetch([blob])

        assert blob["digest"] in result.downloaded
        assert call_count == 3

    def test_errors_collected_for_failed_blobs(self, tmp_path):
        cache = Cache(tmp_path)
        content = b"will always fail"
        blob = _make_blob_descriptor(content)

        client = MagicMock()
        # Always return wrong data
        client.fetch_blob = MagicMock(return_value=iter([b"always wrong content"]))

        fetcher = Fetcher(client, cache, parallelism=1)
        result = fetcher.fetch([blob])

        assert len(result.errors) == 1
        assert result.errors[0]["digest"] == blob["digest"]
        assert "SHA256 mismatch" in result.errors[0]["error"]


class TestFetcherMixed:
    def test_mixed_cached_and_missing(self, tmp_path):
        cache = Cache(tmp_path)
        cached_content = b"cached"
        new_content = b"new"
        cached_blob = _make_blob_descriptor(cached_content)
        new_blob = _make_blob_descriptor(new_content)

        cache.store_blob(cached_blob["digest"], cached_content)

        client = _setup_mock_client([new_blob])
        fetcher = Fetcher(client, cache)
        result = fetcher.fetch([cached_blob, new_blob])

        assert cached_blob["digest"] in result.skipped
        assert new_blob["digest"] in result.downloaded


class TestFetchResult:
    def test_default_values(self):
        result = FetchResult()
        assert result.downloaded == []
        assert result.skipped == []
        assert result.errors == []
