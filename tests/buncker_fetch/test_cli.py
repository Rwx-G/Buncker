"""Tests for buncker_fetch.__main__ CLI."""

from __future__ import annotations

import base64
import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from buncker_fetch.__main__ import main
from buncker_fetch.config import save_config
from shared.crypto import (
    derive_keys,
    encrypt,
    generate_mnemonic,
    sign,
    split_mnemonic,
)


@pytest.fixture()
def mnemonic():
    return generate_mnemonic()


@pytest.fixture()
def config_with_keys(tmp_path, mnemonic):
    """Create a config file with valid salt and derived_key_check."""
    mnemonic_12, salt = split_mnemonic(mnemonic)
    aes_key, hmac_key = derive_keys(mnemonic_12, salt)

    marker = b"buncker-pair-check"
    derived_key_check = base64.b64encode(encrypt(marker, aes_key)).decode()

    config = {
        "salt": base64.b64encode(salt).decode(),
        "derived_key_check": derived_key_check,
        "registries": {},
    }
    config_path = tmp_path / "config.json"
    save_config(config, config_path)
    return config_path, mnemonic, aes_key, hmac_key


def _create_request(blobs, aes_key, hmac_key, output_dir, source_id="test"):
    """Create an encrypted request file."""
    request_data = {
        "version": "1",
        "buncker_version": "0.3.0",
        "generated_at": "2026-03-04T12:00:00+00:00",
        "source_id": source_id,
        "blobs": blobs,
    }
    json_bytes = json.dumps(request_data).encode()
    signature = sign(json_bytes, hmac_key)
    signed_data = json_bytes + b"\n" + signature.encode()
    encrypted = encrypt(signed_data, aes_key)

    path = output_dir / "request.json.enc"
    path.write_bytes(encrypted)
    return path


class TestPair:
    def test_pair_valid_mnemonic(self, tmp_path, mnemonic):
        config_path = tmp_path / "config.json"
        with patch("builtins.input", return_value=mnemonic):
            result = main(["--config", str(config_path), "pair"])

        assert result == 0
        config = json.loads(config_path.read_text())
        assert config["salt"] != ""
        assert config["derived_key_check"] != ""

    def test_pair_invalid_word(self, tmp_path):
        config_path = tmp_path / "config.json"
        bad_mnemonic = (
            "invalid word list that has twelve words to test the validation check ok"
        )
        with patch("builtins.input", return_value=bad_mnemonic):
            result = main(["--config", str(config_path), "pair"])

        assert result == 1

    def test_pair_wrong_word_count(self, tmp_path):
        config_path = tmp_path / "config.json"
        with patch("builtins.input", return_value="only three words"):
            result = main(["--config", str(config_path), "pair"])

        assert result == 1

    def test_pair_split_mnemonic_error(self, tmp_path):
        """split_mnemonic failure is handled."""
        config_path = tmp_path / "config.json"
        from shared.wordlist import WORDLIST

        # 16 valid words but trigger an error by mocking split_mnemonic
        valid_16 = " ".join(WORDLIST[i] for i in range(16))
        with (
            patch("builtins.input", return_value=valid_16),
            patch(
                "shared.crypto.split_mnemonic",
                side_effect=Exception("split error"),
            ),
        ):
            result = main(["--config", str(config_path), "pair"])
        assert result == 1


class TestDigestCacheCorrupted:
    def test_corrupted_json_returns_empty(self, tmp_path):
        """Corrupted digest cache file returns empty dict."""
        from buncker_fetch.__main__ import _load_digest_cache

        cache_path = tmp_path / "manifest-digests.json"
        cache_path.write_text("{bad json", encoding="utf-8")

        with patch("buncker_fetch.__main__._DIGEST_CACHE_PATH", cache_path):
            result = _load_digest_cache()
        assert result == {}


class TestLoadConfigInvalid:
    def test_invalid_json_raises_config_error(self, tmp_path):
        """Invalid JSON in config file raises ConfigError."""
        from buncker_fetch.config import load_config
        from shared.exceptions import ConfigError

        config_path = tmp_path / "bad_config.json"
        config_path.write_text("{invalid json")

        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_config(config_path)


class TestInspect:
    def test_inspect_displays_summary(self, tmp_path, config_with_keys, capsys):
        config_path, mnemonic, aes_key, hmac_key = config_with_keys
        blobs = [
            {
                "registry": "docker.io",
                "repository": "library/nginx",
                "digest": "sha256:abc",
                "size": 5000,
                "media_type": "test",
            },
            {
                "registry": "ghcr.io",
                "repository": "org/app",
                "digest": "sha256:def",
                "size": 3000,
                "media_type": "test",
            },
        ]
        request_path = _create_request(
            blobs,
            aes_key,
            hmac_key,
            tmp_path,
        )

        with patch("builtins.input", return_value=mnemonic):
            result = main(
                [
                    "--config",
                    str(config_path),
                    "inspect",
                    str(request_path),
                ]
            )

        assert result == 0
        output = capsys.readouterr().out
        assert "test" in output  # source_id
        assert "2" in output  # blob_count

    def test_inspect_json_output(self, tmp_path, config_with_keys, capsys):
        config_path, mnemonic, aes_key, hmac_key = config_with_keys
        blobs = [
            {
                "registry": "docker.io",
                "repository": "lib/test",
                "digest": "sha256:abc",
                "size": 100,
                "media_type": "test",
            }
        ]
        request_path = _create_request(
            blobs,
            aes_key,
            hmac_key,
            tmp_path,
        )

        with patch("builtins.input", return_value=mnemonic):
            result = main(
                [
                    "--json",
                    "--config",
                    str(config_path),
                    "inspect",
                    str(request_path),
                ]
            )

        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["blob_count"] == 1
        assert "docker.io" in data["registries"]


class TestFetch:
    def test_fetch_orchestration(self, tmp_path, config_with_keys, capsys):
        config_path, mnemonic, aes_key, hmac_key = config_with_keys

        content = b"blob data for fetch test"
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        blobs = [
            {
                "registry": "docker.io",
                "repository": "library/test",
                "digest": digest,
                "size": len(content),
                "media_type": "test",
            }
        ]
        request_path = _create_request(
            blobs,
            aes_key,
            hmac_key,
            tmp_path,
        )

        # Use tmp_path for cache to avoid writing to real ~/.buncker/cache
        cache_path = tmp_path / "cache"

        # Mock the RegistryClient and Fetcher
        with (
            patch("builtins.input", return_value=mnemonic),
            patch("buncker_fetch.__main__.RegistryClient"),
            patch("buncker_fetch.__main__.Fetcher") as MockFetcher,
            patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", cache_path),
        ):
            from buncker_fetch.fetcher import FetchResult

            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = FetchResult(
                downloaded=[digest],
                skipped=[],
                errors=[],
            )
            MockFetcher.return_value = mock_fetcher

            # Store the blob in the temp cache for build_response
            from buncker_fetch.cache import Cache

            cache = Cache(cache_path)
            cache.store_blob(digest, content)

            output_dir = tmp_path / "output"
            result = main(
                [
                    "--config",
                    str(config_path),
                    "fetch",
                    str(request_path),
                    "--output",
                    str(output_dir),
                    "--parallelism",
                    "2",
                ]
            )

        assert result == 0
        output = capsys.readouterr().out
        assert "success" in output


class TestStatus:
    def test_status_output(self, tmp_path, capsys):
        # Create a cache with some blobs
        from buncker_fetch.cache import Cache

        cache_path = tmp_path / "cache"
        cache = Cache(cache_path)
        content = b"status test blob"
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        cache.store_blob(digest, content)

        with patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", cache_path):
            result = main(["status"])

        assert result == 0
        output = capsys.readouterr().out
        assert "blob_count" in output
        assert "1" in output

    def test_status_json(self, tmp_path, capsys):
        from buncker_fetch.cache import Cache

        cache_path = tmp_path / "cache"
        Cache(cache_path)  # init empty cache

        with patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", cache_path):
            result = main(["--json", "status"])

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        assert data["blob_count"] == 0


class TestCacheClean:
    def test_cache_clean(self, tmp_path, capsys):
        from buncker_fetch.cache import Cache

        cache_path = tmp_path / "cache"
        Cache(cache_path)

        with patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", cache_path):
            result = main(["cache", "clean", "--older-than", "30d"])

        assert result == 0
        output = capsys.readouterr().out
        assert "count" in output

    def test_cache_clean_invalid_format(self, tmp_path, capsys):
        with patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", tmp_path):
            result = main(["cache", "clean", "--older-than", "invalid"])

        assert result == 1


class TestManifestAutoRefresh:
    """Test manifest digest tracking and upstream change detection."""

    def test_first_fetch_stores_digest(self, tmp_path):
        from buncker_fetch.__main__ import _check_manifest_changed

        cache_path = tmp_path / "cache" / "manifest-digests.json"
        img = "docker.io/library/nginx:latest/linux/amd64"
        with patch("buncker_fetch.__main__._DIGEST_CACHE_PATH", cache_path):
            log = MagicMock()
            _check_manifest_changed(img, "sha256:abc123", log)

            # Should not warn on first fetch
            log.warning.assert_not_called()

            # Should store the digest
            assert cache_path.exists()
            data = json.loads(cache_path.read_text())
            assert data[img] == "sha256:abc123"

    def test_same_digest_no_warning(self, tmp_path):
        from buncker_fetch.__main__ import _check_manifest_changed

        cache_path = tmp_path / "cache" / "manifest-digests.json"
        img = "docker.io/library/nginx:latest/linux/amd64"
        with patch("buncker_fetch.__main__._DIGEST_CACHE_PATH", cache_path):
            log = MagicMock()
            _check_manifest_changed(img, "sha256:abc123", log)
            _check_manifest_changed(img, "sha256:abc123", log)

            log.warning.assert_not_called()

    def test_changed_digest_warns(self, tmp_path):
        from buncker_fetch.__main__ import _check_manifest_changed

        cache_path = tmp_path / "cache" / "manifest-digests.json"
        img = "docker.io/library/nginx:latest/linux/amd64"
        with patch("buncker_fetch.__main__._DIGEST_CACHE_PATH", cache_path):
            log = MagicMock()
            _check_manifest_changed(img, "sha256:old", log)
            _check_manifest_changed(img, "sha256:new", log)

            log.warning.assert_called_once()
            call_args = log.warning.call_args
            assert call_args[0][0] == "manifest_upstream_changed"
            extra = call_args[1]["extra"]
            assert extra["previous_digest"] == "sha256:old"
            assert extra["new_digest"] == "sha256:new"

    def test_digest_cache_persists(self, tmp_path):
        from buncker_fetch.__main__ import (
            _load_digest_cache,
            _save_digest_cache,
        )

        cache_path = tmp_path / "cache" / "manifest-digests.json"
        with patch("buncker_fetch.__main__._DIGEST_CACHE_PATH", cache_path):
            _save_digest_cache({"key1": "sha256:aaa"})
            loaded = _load_digest_cache()
            assert loaded == {"key1": "sha256:aaa"}


class TestErrorHandling:
    def test_no_command_shows_help(self, capsys):
        result = main([])
        assert result == 1

    def test_error_messages_are_actionable(self, tmp_path, capsys):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"salt": "", "derived_key_check": ""}))

        result = main(["--config", str(config_path), "inspect", "nonexistent.enc"])
        assert result == 1
        stderr = capsys.readouterr().err
        assert "pair" in stderr.lower() or "Error" in stderr


class TestFetchManifests:
    """Tests for _fetch_manifests internal function."""

    def test_no_images_returns_empty(self):
        """Request without images returns empty list."""
        from buncker_fetch.__main__ import _fetch_manifests

        result = _fetch_manifests({"blobs": []}, {})
        assert result == []

    def test_fetch_manifests_direct_manifest(self):
        """Non-index manifest is returned directly."""
        from buncker_fetch.__main__ import _fetch_manifests

        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:cfg", "size": 100, "mediaType": "config"},
            "layers": [{"digest": "sha256:layer1", "size": 200, "mediaType": "layer"}],
        }

        mock_client = MagicMock()
        mock_client.fetch_manifest.return_value = manifest

        request_data = {
            "images": [
                {
                    "registry": "docker.io",
                    "repository": "library/nginx",
                    "tag": "1.25",
                    "platform": "linux/amd64",
                }
            ]
        }

        with (
            patch("buncker_fetch.__main__.RegistryClient", return_value=mock_client),
            patch("buncker_fetch.__main__.load_credentials", return_value=None),
        ):
            result = _fetch_manifests(request_data, {})

        assert len(result) == 1
        assert result[0]["repository"] == "library/nginx"

    def test_fetch_manifests_index_platform_found(self):
        """Index manifest resolves to platform-specific manifest."""
        from buncker_fetch.__main__ import _fetch_manifests

        index_manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": "sha256:amd64",
                    "platform": {"os": "linux", "architecture": "amd64"},
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                },
            ],
        }

        platform_manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:cfg", "size": 100, "mediaType": "config"},
            "layers": [],
        }

        mock_client = MagicMock()
        mock_client.fetch_manifest.side_effect = [index_manifest, platform_manifest]

        request_data = {
            "images": [
                {
                    "registry": "docker.io",
                    "repository": "library/nginx",
                    "tag": "1.25",
                    "platform": "linux/amd64",
                }
            ]
        }

        with (
            patch("buncker_fetch.__main__.RegistryClient", return_value=mock_client),
            patch("buncker_fetch.__main__.load_credentials", return_value=None),
        ):
            result = _fetch_manifests(request_data, {})

        assert len(result) == 1
        assert result[0]["manifest"]["schemaVersion"] == 2

    def test_fetch_manifests_platform_not_found(self):
        """Index without matching platform logs warning."""
        from buncker_fetch.__main__ import _fetch_manifests

        index_manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": "sha256:arm64",
                    "platform": {"os": "linux", "architecture": "arm64"},
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                },
            ],
        }

        mock_client = MagicMock()
        mock_client.fetch_manifest.return_value = index_manifest

        request_data = {
            "images": [
                {
                    "registry": "docker.io",
                    "repository": "library/nginx",
                    "tag": "1.25",
                    "platform": "linux/amd64",  # no match
                }
            ]
        }

        with (
            patch("buncker_fetch.__main__.RegistryClient", return_value=mock_client),
            patch("buncker_fetch.__main__.load_credentials", return_value=None),
        ):
            result = _fetch_manifests(request_data, {})

        assert result == []

    def test_fetch_manifests_index_platform_with_variant(self):
        """Index manifest resolves with os/arch/variant (e.g. arm/v7)."""
        from buncker_fetch.__main__ import _fetch_manifests

        index_manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": "sha256:armv7",
                    "platform": {
                        "os": "linux",
                        "architecture": "arm",
                        "variant": "v7",
                    },
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                },
                {
                    "digest": "sha256:arm64",
                    "platform": {
                        "os": "linux",
                        "architecture": "arm64",
                    },
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                },
            ],
        }

        platform_manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:cfg", "size": 100, "mediaType": "config"},
            "layers": [],
        }

        mock_client = MagicMock()
        mock_client.fetch_manifest.side_effect = [index_manifest, platform_manifest]

        request_data = {
            "images": [
                {
                    "registry": "docker.io",
                    "repository": "library/nginx",
                    "tag": "1.25",
                    "platform": "linux/arm/v7",
                }
            ]
        }

        with (
            patch("buncker_fetch.__main__.RegistryClient", return_value=mock_client),
            patch("buncker_fetch.__main__.load_credentials", return_value=None),
        ):
            result = _fetch_manifests(request_data, {})

        assert len(result) == 1
        # Verify the correct platform digest was fetched
        mock_client.fetch_manifest.assert_called_with("library/nginx", "sha256:armv7")

    def test_fetch_manifests_exception_logged(self):
        """Exception during fetch is caught and logged."""
        from buncker_fetch.__main__ import _fetch_manifests

        mock_client = MagicMock()
        mock_client.fetch_manifest.side_effect = RuntimeError("connection failed")

        request_data = {
            "images": [
                {
                    "registry": "docker.io",
                    "repository": "library/nginx",
                    "tag": "1.25",
                    "platform": "linux/amd64",
                }
            ]
        }

        with (
            patch("buncker_fetch.__main__.RegistryClient", return_value=mock_client),
            patch("buncker_fetch.__main__.load_credentials", return_value=None),
        ):
            result = _fetch_manifests(request_data, {})

        assert result == []

    def test_fetch_manifests_skips_empty_repository(self):
        """Image with empty repository is skipped."""
        from buncker_fetch.__main__ import _fetch_manifests

        request_data = {
            "images": [
                {
                    "registry": "docker.io",
                    "repository": "",
                    "tag": "latest",
                }
            ]
        }

        result = _fetch_manifests(request_data, {})
        assert result == []

    def test_refresh_true_re_fetches_manifest(self):
        """Image with refresh: true is fetched and includes updated cached_at."""
        from buncker_fetch.__main__ import _fetch_manifests

        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:cfg", "size": 100, "mediaType": "config"},
            "layers": [{"digest": "sha256:layer1", "size": 200, "mediaType": "layer"}],
        }

        mock_client = MagicMock()
        mock_client.fetch_manifest.return_value = manifest

        request_data = {
            "images": [
                {
                    "registry": "docker.io",
                    "repository": "library/nginx",
                    "tag": "1.25",
                    "platform": "linux/amd64",
                    "refresh": True,
                }
            ]
        }

        with (
            patch("buncker_fetch.__main__.RegistryClient", return_value=mock_client),
            patch("buncker_fetch.__main__.load_credentials", return_value=None),
        ):
            result = _fetch_manifests(request_data, {})

        assert len(result) == 1
        assert result[0]["repository"] == "library/nginx"
        assert "_buncker" in result[0]["manifest"]
        assert "cached_at" in result[0]["manifest"]["_buncker"]
        mock_client.fetch_manifest.assert_called_once()


class TestFetchEdgeCases:
    """Test fetch command edge cases."""

    def test_fetch_empty_request(self, tmp_path, config_with_keys, capsys):
        """Request with no blobs and no images returns success message."""
        config_path, mnemonic, aes_key, hmac_key = config_with_keys
        request_path = _create_request([], aes_key, hmac_key, tmp_path)

        cache_path = tmp_path / "cache"
        with (
            patch("builtins.input", return_value=mnemonic),
            patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", cache_path),
        ):
            result = main(["--config", str(config_path), "fetch", str(request_path)])

        assert result == 0
        output = capsys.readouterr().out
        assert "No blobs to fetch" in output

    def test_fetch_auto_scan_no_transfer_path(self, tmp_path, config_with_keys, capsys):
        """Fetch without file and no transfer_path configured returns error."""
        config_path, mnemonic, aes_key, hmac_key = config_with_keys

        cache_path = tmp_path / "cache"
        with (
            patch("builtins.input", return_value=mnemonic),
            patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", cache_path),
        ):
            result = main(["--config", str(config_path), "fetch"])

        assert result == 1

    def test_fetch_auto_scan_no_files(self, tmp_path, config_with_keys, capsys):
        """Fetch auto-scan with empty transfer_path returns error."""
        config_path, mnemonic, aes_key, hmac_key = config_with_keys

        # Add transfer_path to config
        config = json.loads(config_path.read_text())
        scan_dir = tmp_path / "transfer"
        scan_dir.mkdir()
        config["transfer_path"] = str(scan_dir)
        config_path.write_text(json.dumps(config))

        cache_path = tmp_path / "cache"
        with (
            patch("builtins.input", return_value=mnemonic),
            patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", cache_path),
        ):
            result = main(["--config", str(config_path), "fetch"])

        assert result == 1

    def test_fetch_auto_scan_finds_file(self, tmp_path, config_with_keys, capsys):
        """Fetch auto-scan finds and uses newest .json.enc file."""
        config_path, mnemonic, aes_key, hmac_key = config_with_keys

        # Create request in transfer dir
        scan_dir = tmp_path / "transfer"
        scan_dir.mkdir()
        _create_request([], aes_key, hmac_key, scan_dir)

        # Add transfer_path to config
        config = json.loads(config_path.read_text())
        config["transfer_path"] = str(scan_dir)
        config_path.write_text(json.dumps(config))

        cache_path = tmp_path / "cache"
        with (
            patch("builtins.input", return_value=mnemonic),
            patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", cache_path),
        ):
            result = main(["--config", str(config_path), "fetch"])

        assert result == 0


class TestFetchWithImages:
    """Test fetch with images in request."""

    def test_fetch_with_images_extracts_blobs(self, tmp_path, config_with_keys, capsys):
        """Request with images triggers manifest fetch and blob extraction."""
        config_path, mnemonic, aes_key, hmac_key = config_with_keys

        # Create request with images field
        request_data = {
            "version": "1",
            "buncker_version": "0.9.0",
            "generated_at": "2026-03-04T12:00:00+00:00",
            "source_id": "test",
            "blobs": [],
            "images": [
                {
                    "registry": "docker.io",
                    "repository": "library/nginx",
                    "tag": "1.25",
                    "platform": "linux/amd64",
                }
            ],
        }
        json_bytes = json.dumps(request_data).encode()
        signature = sign(json_bytes, hmac_key)
        signed_data = json_bytes + b"\n" + signature.encode()
        encrypted = encrypt(signed_data, aes_key)
        request_path = tmp_path / "request.json.enc"
        request_path.write_bytes(encrypted)

        config_content = b"config data for test"
        config_hex = hashlib.sha256(config_content).hexdigest()
        layer_content = b"layer data for test"
        layer_hex = hashlib.sha256(layer_content).hexdigest()

        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "digest": f"sha256:{config_hex}",
                "size": 100,
                "mediaType": "config",
            },
            "layers": [
                {
                    "digest": f"sha256:{layer_hex}",
                    "size": 200,
                    "mediaType": "layer",
                }
            ],
        }

        cache_path = tmp_path / "cache"
        from buncker_fetch.cache import Cache

        cache = Cache(cache_path)

        # Store blobs with correct digests
        cache.store_blob(f"sha256:{config_hex}", config_content)
        cache.store_blob(f"sha256:{layer_hex}", layer_content)

        mock_fetcher = MagicMock()
        from buncker_fetch.fetcher import FetchResult

        mock_fetcher.fetch.return_value = FetchResult(
            downloaded=[], skipped=[], errors=[]
        )

        with (
            patch("builtins.input", return_value=mnemonic),
            patch("buncker_fetch.__main__._DEFAULT_CACHE_PATH", cache_path),
            patch("buncker_fetch.__main__.RegistryClient"),
            patch("buncker_fetch.__main__.Fetcher", return_value=mock_fetcher),
            patch(
                "buncker_fetch.__main__._fetch_manifests",
                return_value=[
                    {
                        "registry": "docker.io",
                        "repository": "library/nginx",
                        "tag": "1.25",
                        "platform": "linux-amd64",
                        "manifest": manifest,
                    }
                ],
            ),
        ):
            output_dir = tmp_path / "output"
            result = main(
                [
                    "--config",
                    str(config_path),
                    "fetch",
                    str(request_path),
                    "--output",
                    str(output_dir),
                ]
            )

        assert result == 0
