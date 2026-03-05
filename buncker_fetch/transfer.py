"""Transfer module - process requests and build responses for buncker-fetch."""

from __future__ import annotations

import io
import json
import logging
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from buncker_fetch.cache import Cache
from shared.crypto import decrypt, encrypt, sign, verify
from shared.exceptions import CryptoError, TransferError

_log = logging.getLogger("buncker.fetch.transfer")

_OCI_LAYOUT = '{"imageLayoutVersion":"1.0.0"}\n'


def process_request(
    request_path: Path,
    *,
    aes_key: bytes,
    hmac_key: bytes,
) -> dict:
    """Decrypt and verify a transfer request file.

    Args:
        request_path: Path to the .json.enc file.
        aes_key: 32-byte AES key.
        hmac_key: 32-byte HMAC key.

    Returns:
        Parsed request dict with version, source_id, blobs list.

    Raises:
        CryptoError: If decryption fails or HMAC is invalid.
    """
    encrypted_data = request_path.read_bytes()

    # Decrypt
    try:
        decrypted = decrypt(encrypted_data, aes_key)
    except Exception as exc:
        raise CryptoError(
            "Failed to decrypt transfer request. Wrong key or corrupted file.",
            {"path": str(request_path)},
        ) from exc

    # Split data and HMAC: json_bytes + "\n" + hmac_hex
    last_newline = decrypted.rfind(b"\n")
    if last_newline == -1:
        raise CryptoError(
            "Invalid transfer request format - no HMAC signature found",
            {"path": str(request_path)},
        )

    json_bytes = decrypted[:last_newline]
    sig = decrypted[last_newline + 1 :].decode()

    # Verify HMAC
    if not verify(json_bytes, hmac_key, sig):
        raise CryptoError(
            "HMAC verification failed - transfer request is tampered or wrong key",
            {"path": str(request_path)},
        )

    # Parse JSON
    try:
        request_data = json.loads(json_bytes)
    except json.JSONDecodeError as exc:
        raise TransferError(
            "Invalid JSON in transfer request",
            {"path": str(request_path)},
        ) from exc

    _log.info(
        "transfer_request_processed",
        extra={
            "source_id": request_data.get("source_id"),
            "blob_count": len(request_data.get("blobs", [])),
        },
    )

    return request_data


def build_response(
    cache: Cache,
    blobs: list[dict],
    errors: list[dict],
    *,
    aes_key: bytes,
    hmac_key: bytes,
    source_id: str,
    output_dir: Path | None = None,
    deb_path: Path | None = None,
    manifests: list[dict] | None = None,
) -> Path:
    """Build an encrypted transfer response (.tar.enc).

    Args:
        cache: Local blob cache.
        blobs: List of blob descriptors (with digest key).
        errors: List of error dicts for failed blobs.
        aes_key: 32-byte AES key.
        hmac_key: 32-byte HMAC key.
        source_id: Source buncker identifier.
        output_dir: Directory to write the response. Uses cwd if None.
        deb_path: Optional .deb file to include for auto-update.
        manifests: Optional list of manifest dicts to include for offline caching.

    Returns:
        Path to the generated .tar.enc file.
    """
    tar_buffer = io.BytesIO()

    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        # oci-layout file
        _add_string_to_tar(tar, "oci-layout", _OCI_LAYOUT)

        # Collect digests for index.json
        blob_digests = []

        # Add blobs
        for blob in blobs:
            digest = blob["digest"]
            hex_digest = digest.removeprefix("sha256:")
            try:
                blob_path = cache.get_blob(digest)
                blob_data = blob_path.read_bytes()
                _add_bytes_to_tar(tar, f"blobs/sha256/{hex_digest}", blob_data)
                blob_digests.append(digest)
            except Exception as exc:
                _log.warning(
                    "build_response_blob_missing",
                    extra={"digest": digest, "error": str(exc)},
                )
                errors.append({"digest": digest, "error": str(exc)})

        # index.json - simple listing of all blobs
        index_data = json.dumps(
            {
                "imageLayoutVersion": "1.0.0",
                "blobs": blob_digests,
            }
        )
        _add_string_to_tar(tar, "index.json", index_data)

        # MANIFEST.sig - HMAC of blob list
        manifest_data = json.dumps(blob_digests).encode()
        manifest_sig = sign(manifest_data, hmac_key)
        _add_string_to_tar(tar, "MANIFEST.sig", manifest_sig)

        # ERRORS.json if any
        if errors:
            _add_string_to_tar(tar, "ERRORS.json", json.dumps(errors))

        # Include manifests for offline caching
        if manifests:
            for m in manifests:
                registry = m["registry"]
                repository = m["repository"]
                tag = m["tag"]
                platform = m["platform"]
                manifest_json = json.dumps(m["manifest"], indent=2)
                manifest_path = f"manifests/{registry}/{repository}/{tag}/{platform}.json"
                _add_string_to_tar(tar, manifest_path, manifest_json)

        # Include .deb for auto-update if available
        if deb_path and deb_path.exists():
            deb_data = deb_path.read_bytes()
            _add_bytes_to_tar(tar, deb_path.name, deb_data)

    tar_bytes = tar_buffer.getvalue()

    # Sign then encrypt
    signature = sign(tar_bytes, hmac_key)
    signed_data = tar_bytes + b"\n" + signature.encode()
    encrypted = encrypt(signed_data, aes_key)

    # Write output file
    now = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"buncker-response-{now}-{source_id}.tar.enc"
    dest = output_dir or Path.cwd()
    dest.mkdir(parents=True, exist_ok=True)
    output_path = dest / filename
    output_path.write_bytes(encrypted)

    _log.info(
        "transfer_response_built",
        extra={
            "source_id": source_id,
            "blob_count": len(blob_digests),
            "error_count": len(errors),
            "file": str(output_path),
        },
    )

    return output_path


def _add_string_to_tar(tar: tarfile.TarFile, name: str, content: str) -> None:
    """Add a string as a file to a tar archive."""
    data = content.encode()
    _add_bytes_to_tar(tar, name, data)


def _add_bytes_to_tar(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    """Add bytes as a file to a tar archive."""
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))
