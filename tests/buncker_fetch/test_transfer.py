"""Tests for buncker_fetch.transfer - round-trip with real crypto."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest

from buncker_fetch.cache import Cache
from buncker_fetch.transfer import build_response, process_request
from shared.crypto import decrypt, derive_keys, encrypt, generate_mnemonic, sign, verify
from shared.exceptions import CryptoError


@pytest.fixture()
def keys():
    """Generate test keys."""
    mnemonic = generate_mnemonic()
    salt = b"test-salt-16bytes"
    aes_key, hmac_key = derive_keys(mnemonic, salt, iterations=1000)
    return aes_key, hmac_key


@pytest.fixture()
def cache(tmp_path):
    """Create a test cache with some blobs."""
    cache_dir = tmp_path / "cache"
    cache = Cache(cache_dir)
    return cache


def _create_request_file(
    blobs: list[dict],
    aes_key: bytes,
    hmac_key: bytes,
    output_dir,
    source_id: str = "test-source",
    version: str = "0.3.0",
):
    """Create an encrypted request file (simulating buncker offline side)."""
    request_data = {
        "version": "1",
        "buncker_version": version,
        "generated_at": "2026-03-04T12:00:00+00:00",
        "source_id": source_id,
        "blobs": blobs,
    }
    json_bytes = json.dumps(request_data).encode()
    signature = sign(json_bytes, hmac_key)
    signed_data = json_bytes + b"\n" + signature.encode()
    encrypted = encrypt(signed_data, aes_key)

    request_path = output_dir / "request.json.enc"
    request_path.write_bytes(encrypted)
    return request_path


class TestProcessRequest:
    def test_decrypts_and_parses(self, tmp_path, keys):
        aes_key, hmac_key = keys
        blobs = [
            {
                "registry": "docker.io",
                "repository": "library/nginx",
                "digest": "sha256:abc123",
                "size": 1000,
                "media_type": "application/vnd.oci.image.layer.v1.tar+gzip",
            }
        ]
        request_path = _create_request_file(blobs, aes_key, hmac_key, tmp_path)

        result = process_request(request_path, aes_key=aes_key, hmac_key=hmac_key)

        assert result["version"] == "1"
        assert result["source_id"] == "test-source"
        assert len(result["blobs"]) == 1
        assert result["blobs"][0]["digest"] == "sha256:abc123"

    def test_invalid_hmac_raises_crypto_error(self, tmp_path, keys):
        aes_key, hmac_key = keys

        # Create request with correct key, then try with wrong key
        blobs = [
            {
                "digest": "sha256:abc",
                "size": 100,
                "registry": "docker.io",
                "repository": "lib/test",
                "media_type": "test",
            }
        ]
        request_path = _create_request_file(blobs, aes_key, hmac_key, tmp_path)

        # Different HMAC key
        wrong_hmac_key = b"\x00" * 32
        with pytest.raises(CryptoError, match="HMAC verification failed"):
            process_request(request_path, aes_key=aes_key, hmac_key=wrong_hmac_key)

    def test_wrong_aes_key_raises(self, tmp_path, keys):
        aes_key, hmac_key = keys
        blobs = [
            {
                "digest": "sha256:abc",
                "size": 100,
                "registry": "docker.io",
                "repository": "lib/test",
                "media_type": "test",
            }
        ]
        request_path = _create_request_file(blobs, aes_key, hmac_key, tmp_path)

        wrong_aes_key = b"\xff" * 32
        with pytest.raises(CryptoError, match="decrypt"):
            process_request(request_path, aes_key=wrong_aes_key, hmac_key=hmac_key)


class TestBuildResponse:
    def test_creates_encrypted_tar(self, tmp_path, keys, cache):
        aes_key, hmac_key = keys

        # Store a blob in cache
        content = b"blob content for response"
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        cache.store_blob(digest, content)

        blobs = [{"digest": digest, "size": len(content)}]
        response_path = build_response(
            cache,
            blobs,
            [],
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="test-source",
            output_dir=tmp_path / "output",
        )

        assert response_path.exists()
        assert response_path.name.startswith("buncker-response-")
        assert response_path.name.endswith(".tar.enc")

    def test_response_naming_convention(self, tmp_path, keys, cache):
        aes_key, hmac_key = keys
        response_path = build_response(
            cache,
            [],
            [],
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="my-buncker",
            output_dir=tmp_path,
        )
        assert "my-buncker" in response_path.name

    def test_errors_json_included(self, tmp_path, keys, cache):
        aes_key, hmac_key = keys
        errors = [{"digest": "sha256:failed", "error": "HTTP 404"}]

        response_path = build_response(
            cache,
            [],
            errors,
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="test-source",
            output_dir=tmp_path,
        )

        # Decrypt and check ERRORS.json is in the tar
        encrypted = response_path.read_bytes()
        decrypted = decrypt(encrypted, aes_key)
        last_newline = decrypted.rfind(b"\n")
        tar_bytes = decrypted[:last_newline]

        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            members = tar.getnames()
            assert "ERRORS.json" in members
            errors_data = json.loads(tar.extractfile("ERRORS.json").read())
            assert len(errors_data) == 1
            assert errors_data[0]["digest"] == "sha256:failed"


class TestRoundTrip:
    def test_full_round_trip(self, tmp_path, keys, cache):
        """Test: offline generates request -> online processes -> builds response."""
        aes_key, hmac_key = keys

        # 1. Simulate offline: create request
        content = b"layer data for round trip test"
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        mt = "application/vnd.oci.image.layer.v1.tar+gzip"
        blobs = [
            {
                "registry": "docker.io",
                "repository": "library/nginx",
                "digest": digest,
                "size": len(content),
                "media_type": mt,
            }
        ]
        request_path = _create_request_file(blobs, aes_key, hmac_key, tmp_path)

        # 2. Online: process request
        request_data = process_request(request_path, aes_key=aes_key, hmac_key=hmac_key)
        assert request_data["source_id"] == "test-source"
        assert len(request_data["blobs"]) == 1

        # 3. Online: fetch blobs (simulate by storing in cache)
        cache.store_blob(digest, content)

        # 4. Online: build response
        response_path = build_response(
            cache,
            request_data["blobs"],
            [],
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id=request_data["source_id"],
            output_dir=tmp_path / "responses",
        )

        # 5. Verify response can be decrypted and contains the blob
        encrypted = response_path.read_bytes()
        decrypted = decrypt(encrypted, aes_key)
        last_newline = decrypted.rfind(b"\n")
        tar_bytes = decrypted[:last_newline]
        sig = decrypted[last_newline + 1 :].decode()

        assert verify(tar_bytes, hmac_key, sig)

        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            members = tar.getnames()
            assert "oci-layout" in members
            assert "index.json" in members
            assert "MANIFEST.sig" in members

            hex_digest = digest.removeprefix("sha256:")
            assert f"blobs/sha256/{hex_digest}" in members

            blob_data = tar.extractfile(f"blobs/sha256/{hex_digest}").read()
            assert blob_data == content

    def test_round_trip_with_errors(self, tmp_path, keys, cache):
        """Test: response includes ERRORS.json for failed blobs."""
        aes_key, hmac_key = keys

        # Create request with 2 blobs, only store 1 in cache
        content1 = b"good blob"
        digest1 = f"sha256:{hashlib.sha256(content1).hexdigest()}"
        cache.store_blob(digest1, content1)

        digest2 = "sha256:" + "0" * 61 + "bad"

        blobs = [
            {"digest": digest1, "size": len(content1)},
            {"digest": digest2, "size": 100},
        ]

        response_path = build_response(
            cache,
            blobs,
            [],
            aes_key=aes_key,
            hmac_key=hmac_key,
            source_id="test",
            output_dir=tmp_path,
        )

        # Decrypt and check
        encrypted = response_path.read_bytes()
        decrypted = decrypt(encrypted, aes_key)
        last_newline = decrypted.rfind(b"\n")
        tar_bytes = decrypted[:last_newline]

        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            members = tar.getnames()
            # Good blob should be there
            hex1 = digest1.removeprefix("sha256:")
            assert f"blobs/sha256/{hex1}" in members
            # ERRORS.json should be present
            assert "ERRORS.json" in members
