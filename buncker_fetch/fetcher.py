"""Parallel blob fetcher with cache integration."""

from __future__ import annotations

import hashlib
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from buncker_fetch.cache import Cache
from shared.exceptions import RegistryError, StoreError

if TYPE_CHECKING:
    from buncker_fetch.registry_client import RegistryClient

_log = logging.getLogger("buncker.fetch.fetcher")

_MAX_SHA_RETRIES = 3


@dataclass
class FetchResult:
    """Result of a fetch operation."""

    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


class Fetcher:
    """Downloads blobs in parallel, skipping cached ones.

    Args:
        registry_client: Authenticated registry client.
        cache: Local blob cache.
        parallelism: Number of concurrent download workers.
        progress_json: If True, emit JSON progress events to stderr.
    """

    def __init__(
        self,
        registry_client: RegistryClient,
        cache: Cache,
        parallelism: int = 4,
        progress_json: bool = False,
    ) -> None:
        self._client = registry_client
        self._cache = cache
        self._parallelism = parallelism
        self._progress_json = progress_json

    def fetch(self, blobs: list[dict]) -> FetchResult:
        """Fetch a list of blobs, skipping those already cached.

        Args:
            blobs: List of blob descriptors with keys:
                registry, repository, digest, size, media_type.

        Returns:
            FetchResult with downloaded, skipped, and errors lists.
        """
        result = FetchResult()
        to_fetch: list[dict] = []

        # Check cache first
        for blob in blobs:
            digest = blob["digest"]
            if self._cache.has_blob(digest):
                result.skipped.append(digest)
                self._report_progress(
                    len(result.downloaded) + len(result.skipped),
                    len(blobs),
                    digest,
                    skipped=True,
                )
            else:
                to_fetch.append(blob)

        if not to_fetch:
            return result

        # Parallel download
        with ThreadPoolExecutor(max_workers=self._parallelism) as pool:
            futures = {pool.submit(self._fetch_one, blob): blob for blob in to_fetch}

            for future in as_completed(futures):
                blob = futures[future]
                digest = blob["digest"]
                try:
                    future.result()
                    result.downloaded.append(digest)
                except (RegistryError, StoreError) as exc:
                    result.errors.append(
                        {
                            "digest": digest,
                            "error": str(exc),
                        }
                    )
                    _log.error(
                        "fetch_blob_failed",
                        extra={"digest": digest, "error": str(exc)},
                    )

                self._report_progress(
                    len(result.downloaded) + len(result.skipped) + len(result.errors),
                    len(blobs),
                    digest,
                )

        return result

    def _fetch_one(self, blob: dict) -> None:
        """Fetch a single blob with SHA256 retry."""
        digest = blob["digest"]
        repository = blob["repository"]

        for attempt in range(_MAX_SHA_RETRIES):
            chunks = list(self._client.fetch_blob(repository, digest))
            data = b"".join(chunks)

            # Verify SHA256
            actual = hashlib.sha256(data).hexdigest()
            expected = digest.removeprefix("sha256:")

            if actual == expected:
                self._cache.store_blob(digest, data)
                return

            _log.warning(
                "fetch_sha256_mismatch",
                extra={
                    "digest": digest,
                    "expected": expected,
                    "actual": actual,
                    "attempt": attempt + 1,
                },
            )

        raise StoreError(
            f"SHA256 mismatch after {_MAX_SHA_RETRIES} attempts for {digest}",
            {"digest": digest},
        )

    def _report_progress(
        self,
        current: int,
        total: int,
        digest: str,
        skipped: bool = False,
    ) -> None:
        """Report fetch progress to stderr."""
        if self._progress_json:
            import json

            event = {
                "event": "fetch_progress",
                "current": current,
                "total": total,
                "digest": digest,
                "skipped": skipped,
            }
            print(json.dumps(event), file=sys.stderr)
        else:
            short = digest[:19] if len(digest) > 19 else digest
            status = "cached" if skipped else "fetched"
            print(
                f"\r  [{current}/{total}] {short}... {status}",
                end="",
                file=sys.stderr,
                flush=True,
            )
            if current == total:
                print(file=sys.stderr)
