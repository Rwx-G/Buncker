"""Local blob cache for buncker-fetch."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path

from shared.exceptions import StoreError

_log = logging.getLogger("buncker.fetch.cache")


class Cache:
    """Local cache for downloaded blobs.

    Stores blobs in ~/.buncker/cache/blobs/sha256/ with SHA256 verification.

    Args:
        path: Root cache directory (e.g. ~/.buncker/cache).
    """

    def __init__(self, path: Path) -> None:
        self._blobs_dir = path / "blobs" / "sha256"
        self._blobs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def blobs_dir(self) -> Path:
        """Return the blobs directory path."""
        return self._blobs_dir

    def has_blob(self, digest: str) -> bool:
        """Check if a blob exists in the cache.

        Args:
            digest: Blob digest (sha256:...).

        Returns:
            True if the blob is cached.
        """
        hex_digest = digest.removeprefix("sha256:")
        return (self._blobs_dir / hex_digest).exists()

    def get_blob(self, digest: str) -> Path:
        """Get the path to a cached blob.

        Args:
            digest: Blob digest (sha256:...).

        Returns:
            Path to the cached blob file.

        Raises:
            StoreError: If the blob is not in cache.
        """
        hex_digest = digest.removeprefix("sha256:")
        blob_path = self._blobs_dir / hex_digest
        if not blob_path.exists():
            raise StoreError(
                f"Blob not in cache: {digest}",
                {"digest": digest},
            )
        return blob_path

    def store_blob(self, digest: str, data: bytes) -> Path:
        """Store a blob in the cache with SHA256 verification.

        Uses atomic write: temp file + verify + rename.

        Args:
            digest: Expected blob digest (sha256:...).
            data: Blob content bytes.

        Returns:
            Path to the stored blob.

        Raises:
            StoreError: If SHA256 verification fails.
        """
        hex_digest = digest.removeprefix("sha256:")
        actual = hashlib.sha256(data).hexdigest()

        if actual != hex_digest:
            raise StoreError(
                f"SHA256 mismatch for {digest}",
                {"expected": hex_digest, "actual": actual},
            )

        blob_path = self._blobs_dir / hex_digest
        if blob_path.exists():
            return blob_path

        # Atomic write: temp + rename
        fd, tmp_path = tempfile.mkstemp(dir=self._blobs_dir)
        closed = False
        try:
            os.write(fd, data)
            os.close(fd)
            closed = True
            os.rename(tmp_path, blob_path)
        except Exception:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        return blob_path

    def cache_clean(self, older_than_days: int) -> dict:
        """Delete cached blobs older than the specified number of days.

        Args:
            older_than_days: Delete blobs older than this many days.

        Returns:
            Dict with "count" and "bytes_freed".
        """
        cutoff = time.time() - (older_than_days * 86400)
        count = 0
        bytes_freed = 0

        for blob_file in self._blobs_dir.iterdir():
            if not blob_file.is_file():
                continue
            stat = blob_file.stat()
            if stat.st_mtime < cutoff:
                size = stat.st_size
                blob_file.unlink()
                count += 1
                bytes_freed += size
                _log.info(
                    "cache_blob_cleaned",
                    extra={"digest": f"sha256:{blob_file.name}", "size": size},
                )

        return {"count": count, "bytes_freed": bytes_freed}

    def stats(self) -> dict:
        """Return cache statistics.

        Returns:
            Dict with "blob_count", "total_size", "oldest", "newest".
        """
        blob_count = 0
        total_size = 0
        oldest = float("inf")
        newest = 0.0

        for blob_file in self._blobs_dir.iterdir():
            if not blob_file.is_file():
                continue
            stat = blob_file.stat()
            blob_count += 1
            total_size += stat.st_size
            oldest = min(oldest, stat.st_mtime)
            newest = max(newest, stat.st_mtime)

        return {
            "blob_count": blob_count,
            "total_size": total_size,
            "oldest": oldest if blob_count > 0 else None,
            "newest": newest if blob_count > 0 else None,
        }
