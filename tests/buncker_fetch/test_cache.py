"""Tests for buncker_fetch.cache."""

from __future__ import annotations

import hashlib
import os
import time

import pytest

from buncker_fetch.cache import Cache
from shared.exceptions import StoreError


def _make_blob(content: bytes) -> tuple[str, bytes]:
    """Create a blob with its digest."""
    hex_digest = hashlib.sha256(content).hexdigest()
    return f"sha256:{hex_digest}", content


class TestHasBlob:
    def test_returns_false_for_missing(self, tmp_path):
        cache = Cache(tmp_path)
        assert cache.has_blob("sha256:abc123") is False

    def test_returns_true_for_existing(self, tmp_path):
        cache = Cache(tmp_path)
        digest, data = _make_blob(b"hello world")
        cache.store_blob(digest, data)
        assert cache.has_blob(digest) is True


class TestGetBlob:
    def test_returns_path(self, tmp_path):
        cache = Cache(tmp_path)
        digest, data = _make_blob(b"test data")
        cache.store_blob(digest, data)
        path = cache.get_blob(digest)
        assert path.read_bytes() == data

    def test_raises_for_missing(self, tmp_path):
        cache = Cache(tmp_path)
        with pytest.raises(StoreError, match="not in cache"):
            cache.get_blob("sha256:nonexistent")


class TestStoreBlob:
    def test_stores_valid_blob(self, tmp_path):
        cache = Cache(tmp_path)
        digest, data = _make_blob(b"valid content")
        path = cache.store_blob(digest, data)
        assert path.exists()
        assert path.read_bytes() == data

    def test_sha256_mismatch_raises(self, tmp_path):
        cache = Cache(tmp_path)
        bad_digest = "sha256:" + "0" * 64
        with pytest.raises(StoreError, match="SHA256 mismatch"):
            cache.store_blob(bad_digest, b"bad data")

    def test_idempotent_store(self, tmp_path):
        cache = Cache(tmp_path)
        digest, data = _make_blob(b"same content")
        cache.store_blob(digest, data)
        cache.store_blob(digest, data)  # should not fail
        assert cache.has_blob(digest)

    def test_creates_directory_structure(self, tmp_path):
        Cache(tmp_path)
        assert (tmp_path / "blobs" / "sha256").is_dir()


class TestCacheClean:
    def test_removes_old_blobs(self, tmp_path):
        cache = Cache(tmp_path)
        digest, data = _make_blob(b"old blob")
        path = cache.store_blob(digest, data)

        # Backdate the file modification time to 60 days ago
        old_time = time.time() - (60 * 86400)
        os.utime(path, (old_time, old_time))

        result = cache.cache_clean(older_than_days=30)
        assert result["count"] == 1
        assert result["bytes_freed"] == len(data)
        assert not cache.has_blob(digest)

    def test_keeps_recent_blobs(self, tmp_path):
        cache = Cache(tmp_path)
        digest, data = _make_blob(b"recent blob")
        cache.store_blob(digest, data)

        result = cache.cache_clean(older_than_days=30)
        assert result["count"] == 0
        assert cache.has_blob(digest)


class TestStats:
    def test_empty_cache(self, tmp_path):
        cache = Cache(tmp_path)
        stats = cache.stats()
        assert stats["blob_count"] == 0
        assert stats["total_size"] == 0
        assert stats["oldest"] is None
        assert stats["newest"] is None

    def test_with_blobs(self, tmp_path):
        cache = Cache(tmp_path)
        d1, data1 = _make_blob(b"blob one")
        d2, data2 = _make_blob(b"blob two")
        cache.store_blob(d1, data1)
        cache.store_blob(d2, data2)

        stats = cache.stats()
        assert stats["blob_count"] == 2
        assert stats["total_size"] == len(data1) + len(data2)
        assert stats["oldest"] is not None
        assert stats["newest"] is not None
