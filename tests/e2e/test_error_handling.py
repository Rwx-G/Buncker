"""E2E tests: error handling scenarios.

Tests:
  - Corrupt blob in response -> partial import, valid blobs kept
  - Invalid HMAC -> total refusal
"""

from __future__ import annotations

import io
import json
import os
import tarfile

import pytest

from buncker.store import Store
from buncker.transfer import import_response
from buncker_fetch.cache import Cache
from shared.crypto import derive_keys, encrypt, generate_mnemonic, sign
from shared.exceptions import CryptoError, TransferError

from .conftest import (
    BLOB_A_DATA,
    BLOB_A_DIGEST,
    BLOB_B_DIGEST,
    CONFIG_DATA,
    CONFIG_DIGEST,
)


def _build_raw_response_with_corrupt_blob(
    cache: Cache,
    aes_key: bytes,
    hmac_key: bytes,
    output_path,
) -> None:
    """Build a response .tar.enc with one corrupt blob (BLOB_B)."""
    tar_buffer = io.BytesIO()

    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        # oci-layout
        oci_layout = b'{"imageLayoutVersion":"1.0.0"}\n'
        info = tarfile.TarInfo(name="oci-layout")
        info.size = len(oci_layout)
        tar.addfile(info, io.BytesIO(oci_layout))

        # Add config blob (valid)
        hex_config = CONFIG_DIGEST.removeprefix("sha256:")
        info = tarfile.TarInfo(name=f"blobs/sha256/{hex_config}")
        info.size = len(CONFIG_DATA)
        tar.addfile(info, io.BytesIO(CONFIG_DATA))

        # Add blob A (valid)
        hex_a = BLOB_A_DIGEST.removeprefix("sha256:")
        info = tarfile.TarInfo(name=f"blobs/sha256/{hex_a}")
        info.size = len(BLOB_A_DATA)
        tar.addfile(info, io.BytesIO(BLOB_A_DATA))

        # Add blob B (CORRUPT - wrong data under correct filename)
        hex_b = BLOB_B_DIGEST.removeprefix("sha256:")
        corrupt_data = b"THIS-IS-CORRUPTED-DATA"
        info = tarfile.TarInfo(name=f"blobs/sha256/{hex_b}")
        info.size = len(corrupt_data)
        tar.addfile(info, io.BytesIO(corrupt_data))

        # index.json
        index = json.dumps(
            {
                "imageLayoutVersion": "1.0.0",
                "blobs": [CONFIG_DIGEST, BLOB_A_DIGEST, BLOB_B_DIGEST],
            }
        )
        info = tarfile.TarInfo(name="index.json")
        info.size = len(index.encode())
        tar.addfile(info, io.BytesIO(index.encode()))

    tar_bytes = tar_buffer.getvalue()
    signature = sign(tar_bytes, hmac_key)
    signed_data = tar_bytes + b"\n" + signature.encode()
    encrypted = encrypt(signed_data, aes_key)

    output_path.write_bytes(encrypted)


@pytest.mark.e2e
class TestCorruptBlob:
    """Test: corrupt blob in response -> partial import."""

    def test_corrupt_blob_partial_import(self, tmp_path):
        """Valid blobs are kept, corrupt blob is rejected."""
        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)

        store_path = tmp_path / "store"
        store_path.mkdir()
        store = Store(store_path)

        # Build response with one corrupt blob
        response_path = tmp_path / "response.tar.enc"
        _build_raw_response_with_corrupt_blob(
            cache=None,
            aes_key=aes_key,
            hmac_key=hmac_key,
            output_path=response_path,
        )

        # Import
        result = import_response(
            response_path,
            aes_key=aes_key,
            hmac_key=hmac_key,
            store=store,
        )

        # 2 valid blobs imported (config + blob A), 1 skipped (blob B corrupt)
        assert result["imported"] == 2
        assert result["skipped"] == 1

        # Verify valid blobs are accessible
        assert store.has_blob(CONFIG_DIGEST)
        assert store.has_blob(BLOB_A_DIGEST)

        # Corrupt blob is NOT in store
        assert not store.has_blob(BLOB_B_DIGEST)

        # Error mentions the corrupt digest
        error_digests = [e["digest"] for e in result["errors"]]
        assert BLOB_B_DIGEST in error_digests


@pytest.mark.e2e
class TestInvalidHMAC:
    """Test: tampered HMAC -> total refusal."""

    def test_tampered_request_hmac_refused(self, tmp_path):
        """Tampered transfer request is refused entirely."""
        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)

        # Build a valid request, then tamper with it
        request_data = {
            "version": "1",
            "source_id": "test",
            "blobs": [
                {
                    "digest": BLOB_A_DIGEST,
                    "registry": "r",
                    "repository": "r",
                    "size": 10,
                }
            ],
        }
        json_bytes = json.dumps(request_data).encode()
        # Use WRONG hmac key to sign
        wrong_key = os.urandom(32)
        bad_sig = sign(json_bytes, wrong_key)
        signed_data = json_bytes + b"\n" + bad_sig.encode()
        encrypted = encrypt(signed_data, aes_key)

        request_path = tmp_path / "tampered.json.enc"
        request_path.write_bytes(encrypted)

        # Attempt to process - should raise CryptoError
        from buncker_fetch.transfer import process_request

        with pytest.raises(CryptoError, match="HMAC verification failed"):
            process_request(request_path, aes_key=aes_key, hmac_key=hmac_key)

    def test_tampered_response_hmac_refused(self, tmp_path):
        """Tampered transfer response is refused entirely."""
        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)

        store_path = tmp_path / "store"
        store_path.mkdir()
        store = Store(store_path)

        # Build a tar with valid content but wrong HMAC
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            data = b'{"imageLayoutVersion":"1.0.0"}\n'
            info = tarfile.TarInfo(name="oci-layout")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        tar_bytes = tar_buffer.getvalue()

        # Sign with WRONG key
        wrong_key = os.urandom(32)
        bad_sig = sign(tar_bytes, wrong_key)
        signed_data = tar_bytes + b"\n" + bad_sig.encode()
        encrypted = encrypt(signed_data, aes_key)

        response_path = tmp_path / "tampered.tar.enc"
        response_path.write_bytes(encrypted)

        # Import should refuse entirely
        with pytest.raises(TransferError, match="HMAC verification failed"):
            import_response(
                response_path,
                aes_key=aes_key,
                hmac_key=hmac_key,
                store=store,
            )

        # No blobs imported
        blobs_dir = store_path / "blobs" / "sha256"
        blob_files = [f for f in blobs_dir.iterdir() if f.is_file()]
        assert len(blob_files) == 0

    def test_wrong_decryption_key_refused(self, tmp_path):
        """Using wrong AES key -> decryption fails entirely."""
        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)

        # Build valid encrypted request
        request_data = json.dumps({"version": "1", "blobs": []}).encode()
        sig = sign(request_data, hmac_key)
        signed = request_data + b"\n" + sig.encode()
        encrypted = encrypt(signed, aes_key)

        request_path = tmp_path / "valid.json.enc"
        request_path.write_bytes(encrypted)

        # Try with wrong key
        wrong_aes = os.urandom(32)
        from buncker_fetch.transfer import process_request

        with pytest.raises(CryptoError):
            process_request(request_path, aes_key=wrong_aes, hmac_key=hmac_key)
