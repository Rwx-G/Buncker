"""Tests for buncker.transfer - generate request and import response."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile

import pytest

from buncker.store import Store
from buncker.transfer import generate_request, import_response
from shared.crypto import decrypt, derive_keys, encrypt, generate_mnemonic, sign, verify
from shared.exceptions import TransferError


@pytest.fixture()
def crypto_keys():
    """Generate crypto keys for testing."""
    mnemonic = generate_mnemonic()
    salt = os.urandom(32)
    return derive_keys(mnemonic, salt, iterations=1000)


@pytest.fixture()
def store(tmp_path):
    """Create a temporary store."""
    return Store(tmp_path / "store")


@pytest.fixture()
def missing_blobs():
    """Sample missing blobs list."""
    return [
        {
            "registry": "docker.io",
            "repository": "library/nginx",
            "digest": "sha256:" + "a" * 64,
            "size": 12345,
            "media_type": "application/vnd.oci.image.layer.v1.tar+gzip",
        },
        {
            "registry": "docker.io",
            "repository": "library/nginx",
            "digest": "sha256:" + "b" * 64,
            "size": 67890,
            "media_type": "application/vnd.oci.image.layer.v1.tar+gzip",
        },
    ]


class TestGenerateRequest:
    """Tests for generate_request()."""

    def test_produces_encrypted_file(self, crypto_keys, missing_blobs, tmp_path):
        aes_key, hmac_key = crypto_keys
        path = generate_request(
            missing_blobs,
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="test-buncker",
            output_dir=tmp_path,
        )
        assert path.exists()
        assert path.name.startswith("buncker-request-")
        assert path.name.endswith(".json.enc")
        assert path.stat().st_size > 0

    def test_encrypted_file_decrypts_to_valid_json(
        self, crypto_keys, missing_blobs, tmp_path
    ):
        aes_key, hmac_key = crypto_keys
        path = generate_request(
            missing_blobs,
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="test-buncker",
            output_dir=tmp_path,
        )

        encrypted = path.read_bytes()
        decrypted = decrypt(encrypted, aes_key)
        parts = decrypted.rsplit(b"\n", 1)
        json_bytes = parts[0]
        sig = parts[1].decode()

        # Verify signature
        assert verify(json_bytes, hmac_key, sig)

        # Verify JSON content
        data = json.loads(json_bytes)
        assert data["version"] == "1"
        assert data["source_id"] == "test-buncker"
        assert len(data["blobs"]) == 2
        assert "generated_at" in data
        assert "buncker_version" in data

    def test_filename_contains_source_id(self, crypto_keys, missing_blobs, tmp_path):
        aes_key, hmac_key = crypto_keys
        path = generate_request(
            missing_blobs,
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="my-server",
            output_dir=tmp_path,
        )
        assert "my-server" in path.name


def _build_response_tar(
    blobs: dict[str, bytes], errors: list[dict] | None = None
) -> bytes:
    """Build a tar archive matching the transfer response format.

    Args:
        blobs: Mapping of digest_hex -> blob content.
        errors: Optional ERRORS.json content.

    Returns:
        Tar bytes.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # oci-layout
        oci_layout = b'{"imageLayoutVersion":"1.0.0"}\n'
        info = tarfile.TarInfo(name="oci-layout")
        info.size = len(oci_layout)
        tar.addfile(info, io.BytesIO(oci_layout))

        # blobs
        for digest_hex, content in blobs.items():
            blob_path = f"blobs/sha256/{digest_hex}"
            info = tarfile.TarInfo(name=blob_path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        # ERRORS.json
        if errors is not None:
            errors_bytes = json.dumps(errors).encode()
            info = tarfile.TarInfo(name="ERRORS.json")
            info.size = len(errors_bytes)
            tar.addfile(info, io.BytesIO(errors_bytes))

    return buf.getvalue()


def _encrypt_response(tar_bytes: bytes, aes_key: bytes, hmac_key: bytes) -> bytes:
    """Encrypt and sign a tar response."""
    signature = sign(tar_bytes, hmac_key)
    signed_data = tar_bytes + b"\n" + signature.encode()
    return encrypt(signed_data, aes_key)


class TestImportResponse:
    """Tests for import_response()."""

    def test_round_trip(self, crypto_keys, store, tmp_path):
        aes_key, hmac_key = crypto_keys

        # Create blobs
        blob1 = b"blob content one"
        blob1_hex = hashlib.sha256(blob1).hexdigest()
        blob2 = b"blob content two"
        blob2_hex = hashlib.sha256(blob2).hexdigest()

        tar_bytes = _build_response_tar({blob1_hex: blob1, blob2_hex: blob2})
        encrypted = _encrypt_response(tar_bytes, aes_key, hmac_key)

        response_path = tmp_path / "response.tar.enc"
        response_path.write_bytes(encrypted)

        result = import_response(
            response_path,
            aes_key=aes_key,
            hmac_key=hmac_key,
            store=store,
        )

        assert result["imported"] == 2
        assert result["skipped"] == 0
        assert result["errors"] == []

        # Verify blobs in store
        assert store.has_blob(f"sha256:{blob1_hex}")
        assert store.has_blob(f"sha256:{blob2_hex}")

    def test_invalid_hmac_raises(self, crypto_keys, store, tmp_path):
        aes_key, hmac_key = crypto_keys
        wrong_hmac_key = os.urandom(32)

        blob = b"some blob"
        blob_hex = hashlib.sha256(blob).hexdigest()
        tar_bytes = _build_response_tar({blob_hex: blob})

        # Sign with wrong key
        encrypted = _encrypt_response(tar_bytes, aes_key, wrong_hmac_key)
        response_path = tmp_path / "response.tar.enc"
        response_path.write_bytes(encrypted)

        with pytest.raises(TransferError, match="HMAC verification failed"):
            import_response(
                response_path,
                aes_key=aes_key,
                hmac_key=hmac_key,
                store=store,
            )

    def test_corrupt_blob_skipped(self, crypto_keys, store, tmp_path):
        aes_key, hmac_key = crypto_keys

        good_blob = b"good content"
        good_hex = hashlib.sha256(good_blob).hexdigest()

        # Corrupt blob: filename says one hash, content is different
        bad_hex = "c" * 64
        bad_blob = b"this does not match the hash"

        tar_bytes = _build_response_tar({good_hex: good_blob, bad_hex: bad_blob})
        encrypted = _encrypt_response(tar_bytes, aes_key, hmac_key)

        response_path = tmp_path / "response.tar.enc"
        response_path.write_bytes(encrypted)

        result = import_response(
            response_path,
            aes_key=aes_key,
            hmac_key=hmac_key,
            store=store,
        )

        assert result["imported"] == 1
        assert result["skipped"] == 1
        assert store.has_blob(f"sha256:{good_hex}")
        assert not store.has_blob(f"sha256:{bad_hex}")

    def test_errors_json_handling(self, crypto_keys, store, tmp_path):
        aes_key, hmac_key = crypto_keys

        blob = b"partial blob"
        blob_hex = hashlib.sha256(blob).hexdigest()

        errors = [{"digest": "sha256:" + "f" * 64, "error": "network timeout"}]
        tar_bytes = _build_response_tar({blob_hex: blob}, errors=errors)
        encrypted = _encrypt_response(tar_bytes, aes_key, hmac_key)

        response_path = tmp_path / "response.tar.enc"
        response_path.write_bytes(encrypted)

        result = import_response(
            response_path,
            aes_key=aes_key,
            hmac_key=hmac_key,
            store=store,
        )

        # Good blob still imported despite ERRORS.json
        assert result["imported"] == 1
        assert store.has_blob(f"sha256:{blob_hex}")

    def test_decryption_failure_raises(self, crypto_keys, store, tmp_path):
        aes_key, _ = crypto_keys
        wrong_aes_key = os.urandom(32)

        response_path = tmp_path / "response.tar.enc"
        # Encrypt with wrong key
        response_path.write_bytes(encrypt(b"garbage", wrong_aes_key))

        with pytest.raises(TransferError, match="Failed to decrypt"):
            import_response(
                response_path,
                aes_key=aes_key,
                hmac_key=crypto_keys[1],
                store=store,
            )

    def test_logging_events(self, crypto_keys, store, tmp_path, caplog):
        aes_key, hmac_key = crypto_keys

        blob = b"log test"
        blob_hex = hashlib.sha256(blob).hexdigest()

        tar_bytes = _build_response_tar({blob_hex: blob})
        encrypted = _encrypt_response(tar_bytes, aes_key, hmac_key)

        response_path = tmp_path / "response.tar.enc"
        response_path.write_bytes(encrypted)

        import logging

        with caplog.at_level(logging.INFO, logger="buncker.transfer"):
            import_response(
                response_path,
                aes_key=aes_key,
                hmac_key=hmac_key,
                store=store,
            )

        assert any("transfer_imported" in r.message for r in caplog.records)

    def test_no_newline_in_decrypted_raises(self, crypto_keys, store, tmp_path):
        """Decrypted data without newline separator raises TransferError."""
        aes_key, hmac_key = crypto_keys
        from shared.crypto import encrypt

        # Encrypt data with no newline at all
        encrypted = encrypt(b"no-newline-here", aes_key)
        response_path = tmp_path / "bad.tar.enc"
        response_path.write_bytes(encrypted)

        with pytest.raises(TransferError, match="no HMAC signature"):
            import_response(
                response_path,
                aes_key=aes_key,
                hmac_key=hmac_key,
                store=store,
            )

    def test_errors_json_invalid_logs_warning(
        self, crypto_keys, store, tmp_path, caplog
    ):
        """Invalid JSON in ERRORS.json logs a warning but doesn't crash."""
        aes_key, hmac_key = crypto_keys
        import logging

        blob = b"valid blob"
        blob_hex = hashlib.sha256(blob).hexdigest()

        # Build tar with invalid ERRORS.json
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            oci_layout = b'{"imageLayoutVersion":"1.0.0"}\n'
            info = tarfile.TarInfo(name="oci-layout")
            info.size = len(oci_layout)
            tar.addfile(info, io.BytesIO(oci_layout))

            blob_path = f"blobs/sha256/{blob_hex}"
            info = tarfile.TarInfo(name=blob_path)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))

            errors_bytes = b"{invalid json"
            info = tarfile.TarInfo(name="ERRORS.json")
            info.size = len(errors_bytes)
            tar.addfile(info, io.BytesIO(errors_bytes))

        tar_bytes = buf.getvalue()
        encrypted = _encrypt_response(tar_bytes, aes_key, hmac_key)
        response_path = tmp_path / "resp.tar.enc"
        response_path.write_bytes(encrypted)

        with caplog.at_level(logging.WARNING, logger="buncker.transfer"):
            result = import_response(
                response_path,
                aes_key=aes_key,
                hmac_key=hmac_key,
                store=store,
            )

        assert result["imported"] == 1
        assert any("transfer_errors_json_invalid" in r.message for r in caplog.records)

    def test_import_blob_exception_skipped(self, crypto_keys, tmp_path, caplog):
        """Exception from store.import_blob is caught and skipped."""
        aes_key, hmac_key = crypto_keys
        import logging
        from unittest import mock

        blob = b"good blob"
        blob_hex = hashlib.sha256(blob).hexdigest()
        tar_bytes = _build_response_tar({blob_hex: blob})
        encrypted = _encrypt_response(tar_bytes, aes_key, hmac_key)

        response_path = tmp_path / "resp.tar.enc"
        response_path.write_bytes(encrypted)

        # Mock store that raises on import_blob
        mock_store = mock.MagicMock()
        mock_store.import_blob.side_effect = RuntimeError("disk full")

        with caplog.at_level(logging.ERROR, logger="buncker.transfer"):
            result = import_response(
                response_path,
                aes_key=aes_key,
                hmac_key=hmac_key,
                store=mock_store,
            )

        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert any("transfer_blob_import_failed" in r.message for r in caplog.records)

    def test_manifest_cache_from_response(self, crypto_keys, store, tmp_path):
        """Manifests in response are cached when manifest_cache is provided."""
        aes_key, hmac_key = crypto_keys
        from unittest import mock

        # Build tar with a manifest file
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            oci_layout = b'{"imageLayoutVersion":"1.0.0"}\n'
            info = tarfile.TarInfo(name="oci-layout")
            info.size = len(oci_layout)
            tar.addfile(info, io.BytesIO(oci_layout))

            manifest_data = json.dumps({"schemaVersion": 2}).encode()
            manifest_path = "manifests/docker.io/library/nginx/1.25/linux-amd64.json"
            info = tarfile.TarInfo(name=manifest_path)
            info.size = len(manifest_data)
            tar.addfile(info, io.BytesIO(manifest_data))

        tar_bytes = buf.getvalue()
        encrypted = _encrypt_response(tar_bytes, aes_key, hmac_key)
        response_path = tmp_path / "resp.tar.enc"
        response_path.write_bytes(encrypted)

        mock_cache = mock.MagicMock()

        import_response(
            response_path,
            aes_key=aes_key,
            hmac_key=hmac_key,
            store=store,
            manifest_cache=mock_cache,
        )

        mock_cache.cache_manifest.assert_called_once_with(
            "docker.io", "library/nginx", "1.25", "linux/amd64", {"schemaVersion": 2}
        )

    def test_manifest_cache_malformed_path(self, crypto_keys, store, tmp_path, caplog):
        """Manifest with too few path parts logs a warning."""
        aes_key, hmac_key = crypto_keys
        import logging
        from unittest import mock

        # Build tar with a manifest with too few path parts (only 2)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            oci_layout = b'{"imageLayoutVersion":"1.0.0"}\n'
            info = tarfile.TarInfo(name="oci-layout")
            info.size = len(oci_layout)
            tar.addfile(info, io.BytesIO(oci_layout))

            manifest_data = json.dumps({"schemaVersion": 2}).encode()
            # Only 2 parts: "short/file.json" - needs >= 4
            info = tarfile.TarInfo(name="manifests/short/file.json")
            info.size = len(manifest_data)
            tar.addfile(info, io.BytesIO(manifest_data))

        tar_bytes = buf.getvalue()
        encrypted = _encrypt_response(tar_bytes, aes_key, hmac_key)
        response_path = tmp_path / "resp.tar.enc"
        response_path.write_bytes(encrypted)

        mock_cache = mock.MagicMock()

        with caplog.at_level(logging.DEBUG, logger="buncker.transfer"):
            import_response(
                response_path,
                aes_key=aes_key,
                hmac_key=hmac_key,
                store=store,
                manifest_cache=mock_cache,
            )

        # cache_manifest should not have been called (path too short)
        mock_cache.cache_manifest.assert_not_called()
