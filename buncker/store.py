"""OCI blob store for Buncker - local filesystem storage."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared.exceptions import StoreError

_log = logging.getLogger("buncker.store")

_OCI_LAYOUT = '{"imageLayoutVersion": "1.0.0"}'
_EMPTY_INDEX = '{"schemaVersion": 2, "manifests": []}'


class Store:
    """Manages an OCI Image Layout blob store on the filesystem.

    The store holds blobs in ``blobs/sha256/`` and metadata sidecars in
    ``meta/sha256/``.  All writes are atomic (temp + SHA-256 verify + rename).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._blobs = path / "blobs" / "sha256"
        self._meta = path / "meta" / "sha256"

        self._blobs.mkdir(parents=True, exist_ok=True)
        self._meta.mkdir(parents=True, exist_ok=True)

        layout_file = path / "oci-layout"
        if not layout_file.exists():
            layout_file.write_text(_OCI_LAYOUT)

        index_file = path / "index.json"
        if not index_file.exists():
            index_file.write_text(_EMPTY_INDEX)

    # ------------------------------------------------------------------
    # Blob operations
    # ------------------------------------------------------------------

    def import_blob(
        self,
        data: bytes,
        expected_digest: str,
        *,
        media_type: str = "application/octet-stream",
        image_ref: str | None = None,
    ) -> Path:
        """Import a blob into the store with SHA-256 verification.

        Args:
            data: Raw blob bytes.
            expected_digest: Expected ``sha256:<hex>`` digest.
            media_type: OCI media type for the sidecar.
            image_ref: Optional image reference to record.

        Returns:
            Path to the stored blob.

        Raises:
            StoreError: If the computed digest does not match.
        """
        digest_hex = _strip_prefix(expected_digest)
        actual = hashlib.sha256(data).hexdigest()

        if actual != digest_hex:
            raise StoreError(
                f"Digest mismatch: expected {digest_hex}, got {actual}",
                context={"expected": digest_hex, "actual": actual},
            )

        blob_path = self._blobs / digest_hex

        if not blob_path.exists():
            fd, tmp = tempfile.mkstemp(dir=self._blobs)
            try:
                os.write(fd, data)
                os.close(fd)
                os.rename(tmp, str(blob_path))
            except BaseException:
                os.close(fd) if not os.get_inheritable(fd) else None
                Path(tmp).unlink(missing_ok=True)
                raise

        self._write_sidecar(
            digest_hex,
            size=len(data),
            media_type=media_type,
            image_ref=image_ref,
        )

        return blob_path

    def has_blob(self, digest: str) -> bool:
        """Check whether a blob exists in the store."""
        return (self._blobs / _strip_prefix(digest)).exists()

    def get_blob(self, digest: str) -> Path:
        """Return the path to a stored blob.

        Raises:
            StoreError: If the blob does not exist.
        """
        path = self._blobs / _strip_prefix(digest)
        if not path.exists():
            raise StoreError(
                f"Blob not found: {digest}",
                context={"digest": digest},
            )
        return path

    def list_missing(self, digests: list[str]) -> list[str]:
        """Return digests that are absent from the store."""
        return [d for d in digests if not self.has_blob(d)]

    # ------------------------------------------------------------------
    # Metadata sidecar management
    # ------------------------------------------------------------------

    def update_metadata(self, digest: str, event: str) -> None:
        """Update a sidecar: bump ``last_requested`` and ``request_count``.

        Args:
            digest: Blob digest (``sha256:<hex>`` or bare hex).
            event: Event name for logging context.

        Raises:
            StoreError: If the sidecar does not exist.
        """
        digest_hex = _strip_prefix(digest)
        sidecar_path = self._meta / f"{digest_hex}.json"

        if not sidecar_path.exists():
            raise StoreError(
                f"Sidecar not found for digest: {digest}",
                context={"digest": digest, "event": event},
            )

        meta = json.loads(sidecar_path.read_text())
        meta["last_requested"] = datetime.now(tz=UTC).isoformat()
        meta["request_count"] = meta.get("request_count", 0) + 1

        self._atomic_write_json(sidecar_path, meta)

    def get_metadata(self, digest: str) -> dict:
        """Read the metadata sidecar for a blob.

        Raises:
            StoreError: If the sidecar does not exist.
        """
        digest_hex = _strip_prefix(digest)
        sidecar_path = self._meta / f"{digest_hex}.json"

        if not sidecar_path.exists():
            raise StoreError(
                f"Sidecar not found for digest: {digest}",
                context={"digest": digest},
            )

        return json.loads(sidecar_path.read_text())

    # ------------------------------------------------------------------
    # Garbage collection
    # ------------------------------------------------------------------

    def gc_report(self, inactive_days: int) -> list[dict]:
        """Scan metadata sidecars and return GC candidates.

        A blob is a candidate if its ``last_requested`` timestamp is
        older than *inactive_days* days ago.

        Args:
            inactive_days: Number of days of inactivity.

        Returns:
            List of candidate dicts with digest, size,
            last_requested, and image_refs.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(days=inactive_days)
        candidates: list[dict] = []

        for sidecar_path in self._meta.glob("*.json"):
            meta = json.loads(sidecar_path.read_text())
            last = datetime.fromisoformat(meta["last_requested"])
            if last < cutoff:
                candidate = {
                    "digest": meta["digest"],
                    "size": meta["size"],
                    "last_requested": meta["last_requested"],
                    "image_refs": meta.get("image_refs", []),
                }
                candidates.append(candidate)
                _log.info(
                    "gc_candidate",
                    extra={
                        "digest": meta["digest"],
                        "size": meta["size"],
                    },
                )

        self._last_gc_report = {c["digest"] for c in candidates}
        return candidates

    def gc_impact_report(self, digests: list[str]) -> list[dict]:
        """Analyze which images become non-pullable if given blobs are deleted.

        Scans all cached manifests and checks if they reference any of the
        given digests (in config or layers).

        Args:
            digests: List of ``sha256:<hex>`` digests planned for deletion.

        Returns:
            List of impact dicts, one per affected image, with image reference,
            missing blobs, and missing size.
        """
        gc_set = set(digests)
        impact: list[dict] = []
        manifests_dir = self._path / "manifests"

        if not manifests_dir.exists():
            return impact

        for manifest_path in manifests_dir.rglob("*.json"):
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            # Extract all blob digests from this manifest
            needed: list[str] = []
            config = manifest.get("config", {})
            if "digest" in config:
                needed.append(config["digest"])
            for layer in manifest.get("layers", []):
                if "digest" in layer:
                    needed.append(layer["digest"])

            affected = [d for d in needed if d in gc_set]
            if not affected:
                continue

            # Parse path: manifests/{registry}/{repo}/{tag}/{platform}.json
            rel = manifest_path.relative_to(manifests_dir)
            parts = rel.parts
            if len(parts) >= 4:
                registry = parts[0]
                repository = "/".join(parts[1:-2])
                tag = parts[-2]
                platform = parts[-1].replace(".json", "").replace("-", "/")
            else:
                registry = ""
                repository = str(rel)
                tag = ""
                platform = ""

            # Calculate affected size from sidecars
            missing_size = 0
            for d in affected:
                try:
                    meta = self.get_metadata(d)
                    missing_size += meta.get("size", 0)
                except StoreError:
                    pass

            impact.append(
                {
                    "image": f"{registry}/{repository}:{tag}" if tag else str(rel),
                    "platform": platform,
                    "missing_blobs": affected,
                    "missing_count": len(affected),
                    "total_blobs": len(needed),
                    "missing_size": missing_size,
                }
            )

        return impact

    def gc_execute(
        self,
        digests: list[str],
        operator: str,
    ) -> dict:
        """Delete blobs and sidecars for the given digests.

        Each digest must be present in the latest ``gc_report()``.

        Args:
            digests: List of ``sha256:<hex>`` digests to delete.
            operator: Name of the operator for audit logging.

        Returns:
            Summary dict with ``count`` and ``bytes_freed``.

        Raises:
            StoreError: If a digest is not in the latest report.
        """
        report = getattr(self, "_last_gc_report", None)
        if report is None:
            raise StoreError(
                "No GC report available - run gc_report() first",
            )

        count = 0
        bytes_freed = 0

        for digest in digests:
            if digest not in report:
                raise StoreError(
                    f"Digest {digest} not in latest GC report",
                    context={"digest": digest},
                )

            digest_hex = _strip_prefix(digest)
            blob_path = self._blobs / digest_hex
            sidecar_path = self._meta / f"{digest_hex}.json"

            size = 0
            if blob_path.exists():
                size = blob_path.stat().st_size
                blob_path.unlink()
            if sidecar_path.exists():
                sidecar_path.unlink()

            bytes_freed += size
            count += 1

            _log.info(
                "gc_executed",
                extra={
                    "digest": digest,
                    "size": size,
                    "operator": operator,
                },
            )

        return {"count": count, "bytes_freed": bytes_freed}

    # ------------------------------------------------------------------
    # Store integrity verification
    # ------------------------------------------------------------------

    def verify(self) -> dict:
        """Re-hash every blob and detect silent corruption (bit-rot).

        Returns:
            Dict with ``total``, ``ok``, ``corrupted`` counts and a
            ``corrupted_digests`` list of affected ``sha256:<hex>`` strings.
        """
        total = 0
        ok = 0
        corrupted: list[str] = []

        for blob_path in self._blobs.iterdir():
            if blob_path.is_file() and not blob_path.name.startswith("."):
                total += 1
                expected_hex = blob_path.name
                h = hashlib.sha256()
                with open(blob_path, "rb") as f:
                    while chunk := f.read(65536):
                        h.update(chunk)
                actual_hex = h.hexdigest()
                if actual_hex == expected_hex:
                    ok += 1
                else:
                    corrupted.append(f"sha256:{expected_hex}")
                    _log.error(
                        "blob_corrupted",
                        extra={
                            "digest": f"sha256:{expected_hex}",
                            "actual": actual_hex,
                        },
                    )

        return {
            "total": total,
            "ok": ok,
            "corrupted": len(corrupted),
            "corrupted_digests": corrupted,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_sidecar(
        self,
        digest_hex: str,
        *,
        size: int,
        media_type: str,
        image_ref: str | None,
    ) -> None:
        """Create or update the metadata sidecar for a blob."""
        sidecar_path = self._meta / f"{digest_hex}.json"
        now = datetime.now(tz=UTC).isoformat()

        if sidecar_path.exists():
            meta = json.loads(sidecar_path.read_text())
            if image_ref and image_ref not in meta.get("image_refs", []):
                meta["image_refs"].append(image_ref)
            meta["last_requested"] = now
            meta["request_count"] = meta.get("request_count", 0) + 1
        else:
            meta = {
                "digest": f"sha256:{digest_hex}",
                "size": size,
                "media_type": media_type,
                "image_refs": [image_ref] if image_ref else [],
                "first_imported": now,
                "last_requested": now,
                "request_count": 0,
                "gc_status": "active",
            }

        self._atomic_write_json(sidecar_path, meta)

    def _atomic_write_json(self, path: Path, data: dict) -> None:
        """Write JSON atomically via temp file + replace."""
        content = json.dumps(data, indent=2).encode()
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        try:
            os.write(fd, content)
            os.close(fd)
            os.replace(tmp, str(path))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @property
    def path(self) -> Path:
        """Root path of the store."""
        return self._path


def _strip_prefix(digest: str) -> str:
    """Remove the ``sha256:`` prefix if present."""
    return digest.removeprefix("sha256:")
