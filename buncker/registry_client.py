"""Offline registry client - cached OCI manifest management."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


class ManifestCache:
    """Reads and writes cached OCI manifests within a store path.

    Manifests are stored at:
        ``manifests/{registry}/{repository}/{tag}/{platform}.json``

    Each cached manifest has a ``_buncker`` extension with
    ``cached_at`` and ``source_digest``.
    """

    def __init__(self, store_path: Path) -> None:
        self._manifests = store_path / "manifests"

    def cache_manifest(
        self,
        registry: str,
        repository: str,
        tag: str,
        platform: str,
        manifest: dict,
    ) -> Path:
        """Store a manifest in the cache.

        Args:
            registry: Registry hostname (e.g. ``docker.io``).
            repository: Repository path (e.g. ``library/nginx``).
            tag: Image tag (e.g. ``1.25``).
            platform: OCI platform string (e.g. ``linux/amd64``).
            manifest: Manifest dict to cache.

        Returns:
            Path to the cached manifest file.
        """
        platform_safe = platform.replace("/", "-")
        cache_dir = self._manifests / registry / repository / tag
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{platform_safe}.json"

        raw = json.dumps(
            {k: v for k, v in manifest.items() if k != "_buncker"},
            sort_keys=True,
        ).encode()
        source_digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"

        cached = dict(manifest)
        cached["_buncker"] = {
            "cached_at": datetime.now(tz=UTC).isoformat(),
            "source_digest": source_digest,
        }

        content = json.dumps(cached, indent=2).encode()
        fd, tmp = tempfile.mkstemp(dir=cache_dir)
        try:
            os.write(fd, content)
            os.close(fd)
            os.replace(tmp, str(cache_path))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

        return cache_path

    def get_manifest(
        self,
        registry: str,
        repository: str,
        reference: str,
        platform: str,
    ) -> dict | None:
        """Retrieve a cached manifest.

        Args:
            registry: Registry hostname.
            repository: Repository path.
            reference: Tag or digest (``sha256:...``).
            platform: OCI platform string (e.g. ``linux/amd64``).

        Returns:
            Parsed manifest dict or ``None`` if not found.
        """
        if reference.startswith("sha256:"):
            return self._lookup_by_digest(reference)

        platform_safe = platform.replace("/", "-")
        path = (
            self._manifests
            / registry
            / repository
            / reference
            / f"{platform_safe}.json"
        )

        if not path.exists():
            return None

        return json.loads(path.read_text())

    def is_stale(
        self,
        registry: str,
        repository: str,
        tag: str,
        platform: str,
        ttl_days: int,
    ) -> bool | None:
        """Check if a cached manifest exceeds the TTL.

        Args:
            registry: Registry hostname.
            repository: Repository path.
            tag: Image tag.
            platform: OCI platform string.
            ttl_days: Maximum age in days before manifest is stale.

        Returns:
            None if manifest not cached, True if stale, False if fresh.
        """
        manifest = self.get_manifest(registry, repository, tag, platform)
        if manifest is None:
            return None

        buncker = manifest.get("_buncker", {})
        cached_at_str = buncker.get("cached_at")
        if not cached_at_str:
            return True  # No timestamp means stale

        cached_at = datetime.fromisoformat(cached_at_str)
        age = datetime.now(tz=UTC) - cached_at
        return age.days >= ttl_days

    def count_stale(self, ttl_days: int) -> int:
        """Count all cached manifests that exceed the TTL.

        Args:
            ttl_days: Maximum age in days before manifest is stale.

        Returns:
            Number of stale manifests.
        """
        if not self._manifests.exists():
            return 0

        count = 0
        now = datetime.now(tz=UTC)
        for path in self._manifests.rglob("*.json"):
            try:
                data = json.loads(path.read_text())
                cached_at_str = data.get("_buncker", {}).get("cached_at")
                if not cached_at_str:
                    count += 1
                    continue
                cached_at = datetime.fromisoformat(cached_at_str)
                if (now - cached_at).days >= ttl_days:
                    count += 1
            except (json.JSONDecodeError, ValueError):
                continue
        return count

    def _lookup_by_digest(self, digest: str) -> dict | None:
        """Scan all cached manifests for one matching source_digest."""
        if not self._manifests.exists():
            return None

        for path in self._manifests.rglob("*.json"):
            data = json.loads(path.read_text())
            buncker = data.get("_buncker", {})
            if buncker.get("source_digest") == digest:
                return data

        return None
