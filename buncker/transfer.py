"""Transfer module - generate requests and import responses."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from buncker import __version__
from buncker.registry_client import ManifestCache
from buncker.store import Store
from shared.crypto import decrypt, encrypt, sign, verify
from shared.exceptions import TransferError

_log = logging.getLogger("buncker.transfer")


def generate_request(
    missing_blobs: list[dict],
    *,
    aes_key: bytes,
    hmac_key: bytes,
    source_id: str,
    buncker_version: str = __version__,
    output_dir: Path | None = None,
) -> Path:
    """Generate an encrypted transfer request file.

    Args:
        missing_blobs: List of blob descriptors
            (registry, repository, digest, size, media_type).
        aes_key: 32-byte AES key for encryption.
        hmac_key: 32-byte HMAC key for signing.
        source_id: Identifier for this buncker instance.
        buncker_version: Version string.
        output_dir: Directory to write the file. Uses cwd if None.

    Returns:
        Path to the generated .json.enc file.
    """
    request_data = {
        "version": "1",
        "buncker_version": buncker_version,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "source_id": source_id,
        "blobs": missing_blobs,
    }

    json_bytes = json.dumps(request_data).encode()
    signature = sign(json_bytes, hmac_key)
    signed_data = json_bytes + b"\n" + signature.encode()
    encrypted = encrypt(signed_data, aes_key)

    now = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"buncker-request-{now}-{source_id}.json.enc"
    output_path = (output_dir or Path.cwd()) / filename
    output_path.write_bytes(encrypted)

    _log.info(
        "transfer_manifest_generated",
        extra={
            "source_id": source_id,
            "blob_count": len(missing_blobs),
            "file": str(output_path),
        },
    )

    return output_path


def import_response(
    response_path: Path,
    *,
    aes_key: bytes,
    hmac_key: bytes,
    store: Store,
    manifest_cache: ManifestCache | None = None,
) -> dict:
    """Import an encrypted transfer response (.tar.enc).

    Args:
        response_path: Path to the .tar.enc file.
        aes_key: 32-byte AES key for decryption.
        hmac_key: 32-byte HMAC key for verification.
        store: Store instance for blob import.
        manifest_cache: Optional ManifestCache for caching manifests.

    Returns:
        Summary dict with imported, skipped, errors counts.

    Raises:
        TransferError: If HMAC verification fails.
    """
    encrypted_data = response_path.read_bytes()

    # Decrypt
    try:
        decrypted = decrypt(encrypted_data, aes_key)
    except Exception as exc:
        raise TransferError(
            "Failed to decrypt transfer response",
            {"path": str(response_path)},
        ) from exc

    # Split data and HMAC signature
    # Format: tar_bytes + b"\n" + hmac_hex
    last_newline = decrypted.rfind(b"\n")
    if last_newline == -1:
        raise TransferError(
            "Invalid transfer response format - no HMAC signature found"
        )

    tar_bytes = decrypted[:last_newline]
    try:
        sig = decrypted[last_newline + 1 :].decode("ascii")
    except (UnicodeDecodeError, ValueError) as exc:
        raise TransferError(
            "Invalid HMAC signature encoding - transfer response is corrupted"
        ) from exc

    # Verify HMAC
    if not verify(tar_bytes, hmac_key, sig):
        raise TransferError("HMAC verification failed - transfer response is tampered")

    # Extract tar
    imported = 0
    skipped = 0
    errors_list: list[dict] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        tar_io = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=tar_io, mode="r:*") as tar:
            try:
                if sys.version_info >= (3, 12):
                    tar.extractall(path=tmp_path, filter="data")
                else:
                    for member in tar.getmembers():
                        if (
                            os.path.isabs(member.name)
                            or ".." in member.name
                            or member.issym()
                            or member.islnk()
                        ):
                            raise TransferError(
                                f"Unsafe tar member rejected: {member.name}"
                            )
                    tar.extractall(path=tmp_path)
            except TransferError:
                raise
            except Exception as exc:
                if "outside" in str(exc).lower() or "absolute" in str(exc).lower():
                    raise TransferError(f"Unsafe tar member rejected: {exc}") from exc
                raise

        # Check for ERRORS.json
        errors_file = tmp_path / "ERRORS.json"
        if errors_file.exists():
            try:
                fetch_errors = json.loads(errors_file.read_text())
                for err in fetch_errors:
                    _log.warning(
                        "transfer_blob_fetch_error",
                        extra={"digest": err.get("digest"), "error": err.get("error")},
                    )
                    errors_list.append(err)
            except json.JSONDecodeError:
                _log.warning("transfer_errors_json_invalid")

        # Import blobs
        blobs_dir = tmp_path / "blobs" / "sha256"
        if blobs_dir.exists():
            for blob_file in blobs_dir.iterdir():
                if not blob_file.is_file():
                    continue

                digest_hex = blob_file.name
                digest = f"sha256:{digest_hex}"

                # Verify SHA256 with chunked reading
                h = hashlib.sha256()
                with open(blob_file, "rb") as bf:
                    while chunk := bf.read(65536):
                        h.update(chunk)
                actual = h.hexdigest()
                if actual != digest_hex:
                    _log.error(
                        "transfer_blob_corrupt",
                        extra={
                            "digest": digest,
                            "expected": digest_hex,
                            "actual": actual,
                        },
                    )
                    skipped += 1
                    errors_list.append({"digest": digest, "error": "SHA256 mismatch"})
                    continue

                try:
                    store.import_blob(blob_file.read_bytes(), digest)
                    imported += 1
                except Exception as exc:
                    _log.error(
                        "transfer_blob_import_failed",
                        extra={"digest": digest, "error": str(exc)},
                    )
                    skipped += 1
                    errors_list.append({"digest": digest, "error": str(exc)})

        # Cache manifests if present
        if manifest_cache is not None:
            _cache_manifests_from_response(tmp_path, manifest_cache)

        # Extract .deb for auto-update (FR15)
        deb_file = _extract_deb_update(tmp_path, store.path)

    result = {
        "imported": imported,
        "skipped": skipped,
        "errors": errors_list,
    }
    if deb_file:
        result["update_deb"] = str(deb_file)

    _log.info(
        "transfer_imported",
        extra={
            "imported": imported,
            "skipped": skipped,
            "errors": len(errors_list),
            "source": str(response_path),
            "update_deb": str(deb_file) if deb_file else None,
        },
    )

    return result


def _extract_deb_update(extract_dir: Path, store_path: Path) -> Path | None:
    """Extract .deb file from response for offline auto-update (FR15).

    Copies any .deb file found at the root of the tar to
    ``{store_path}/updates/`` so the operator can install it.

    Returns:
        Path to the extracted .deb, or None if not found.
    """
    for candidate in extract_dir.iterdir():
        if candidate.is_file() and candidate.name.endswith(".deb"):
            updates_dir = store_path / "updates"
            updates_dir.mkdir(parents=True, exist_ok=True)
            dest = updates_dir / candidate.name
            dest.write_bytes(candidate.read_bytes())
            _log.info(
                "transfer_deb_extracted",
                extra={"deb": candidate.name, "dest": str(dest)},
            )
            return dest
    return None


def _cache_manifests_from_response(
    extract_dir: Path,
    manifest_cache: ManifestCache,
) -> None:
    """Cache any manifests found in the response."""
    manifests_dir = extract_dir / "manifests"
    if not manifests_dir.exists():
        return

    for manifest_file in manifests_dir.rglob("*.json"):
        try:
            manifest = json.loads(manifest_file.read_text())
            # Extract registry/repo/tag/platform from path
            rel = manifest_file.relative_to(manifests_dir)
            parts = list(rel.parts)
            if len(parts) >= 4:
                registry = parts[0]
                repository = "/".join(parts[1:-2])
                tag = parts[-2]
                platform_file = parts[-1].replace(".json", "").replace("-", "/")
                manifest_cache.cache_manifest(
                    registry,
                    repository,
                    tag,
                    platform_file,
                    manifest,
                )
        except (json.JSONDecodeError, ValueError, IndexError):
            _log.warning(
                "transfer_manifest_cache_failed",
                extra={"file": str(manifest_file)},
            )
