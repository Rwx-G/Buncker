"""Tests for buncker.auth - API token generation, persistence, and TLS."""

from __future__ import annotations

import json
import os
import sys
from unittest import mock

import pytest

from buncker.auth import (
    generate_api_tokens,
    generate_self_signed_cert,
    load_api_tokens,
    save_api_tokens,
)


class TestGenerateApiTokens:
    """Tests for generate_api_tokens()."""

    def test_returns_two_tokens(self):
        tokens = generate_api_tokens()
        assert "readonly" in tokens
        assert "admin" in tokens

    def test_tokens_are_256_bit_hex(self):
        tokens = generate_api_tokens()
        for key in ("readonly", "admin"):
            token = tokens[key]
            assert len(token) == 64  # 32 bytes = 64 hex chars
            int(token, 16)  # Validates it's valid hex

    def test_tokens_are_unique(self):
        tokens = generate_api_tokens()
        assert tokens["readonly"] != tokens["admin"]

    def test_tokens_differ_between_calls(self):
        t1 = generate_api_tokens()
        t2 = generate_api_tokens()
        assert t1["readonly"] != t2["readonly"]
        assert t1["admin"] != t2["admin"]


class TestSaveAndLoadApiTokens:
    """Tests for save_api_tokens() and load_api_tokens()."""

    def test_save_creates_file(self, tmp_path):
        tokens = {"readonly": "a" * 64, "admin": "b" * 64}
        path = tmp_path / "api-tokens.json"
        save_api_tokens(tokens, path)
        assert path.exists()

    def test_save_content_is_valid_json(self, tmp_path):
        tokens = {"readonly": "a" * 64, "admin": "b" * 64}
        path = tmp_path / "api-tokens.json"
        save_api_tokens(tokens, path)
        loaded = json.loads(path.read_text())
        assert loaded == tokens

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="File permissions not enforced on Windows",
    )
    def test_save_sets_permissions_0600(self, tmp_path):
        tokens = {"readonly": "a" * 64, "admin": "b" * 64}
        path = tmp_path / "api-tokens.json"
        save_api_tokens(tokens, path)
        mode = oct(path.stat().st_mode & 0o777)
        assert mode == "0o600"

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "subdir" / "nested" / "api-tokens.json"
        tokens = {"readonly": "a" * 64, "admin": "b" * 64}
        save_api_tokens(tokens, path)
        assert path.exists()

    def test_load_returns_tokens(self, tmp_path):
        tokens = {"readonly": "abc123" * 10 + "abcd", "admin": "def456" * 10 + "defg"}
        path = tmp_path / "api-tokens.json"
        path.write_text(json.dumps(tokens))
        loaded = load_api_tokens(path)
        assert loaded == tokens

    def test_load_returns_none_when_missing(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        assert load_api_tokens(path) is None

    def test_roundtrip(self, tmp_path):
        tokens = generate_api_tokens()
        path = tmp_path / "api-tokens.json"
        save_api_tokens(tokens, path)
        loaded = load_api_tokens(path)
        assert loaded == tokens


class TestGenerateSelfSignedCert:
    """Tests for generate_self_signed_cert()."""

    def test_creates_all_files(self, tmp_path):
        tls_dir = tmp_path / "tls"
        cert_path, key_path, ca_path = generate_self_signed_cert(tls_dir)
        assert cert_path.exists()
        assert key_path.exists()
        assert ca_path.exists()
        assert (tls_dir / "ca-key.pem").exists()

    def test_cert_is_valid_pem(self, tmp_path):
        tls_dir = tmp_path / "tls"
        cert_path, key_path, ca_path = generate_self_signed_cert(tls_dir)
        for p in (cert_path, key_path, ca_path):
            content = p.read_text()
            assert "-----BEGIN" in content
            assert "-----END" in content

    def test_ca_is_ca_certificate(self, tmp_path):
        from cryptography import x509

        tls_dir = tmp_path / "tls"
        _, _, ca_path = generate_self_signed_cert(tls_dir)
        ca_cert = x509.load_pem_x509_certificate(ca_path.read_bytes())
        bc = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert bc.value.ca is True

    def test_server_cert_has_san(self, tmp_path):
        from cryptography import x509

        tls_dir = tmp_path / "tls"
        cert_path, _, _ = generate_self_signed_cert(tls_dir)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        dns_names = san.value.get_values_for_type(x509.DNSName)
        assert "localhost" in dns_names
        assert "buncker" in dns_names

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="File permissions not enforced on Windows",
    )
    def test_key_files_permissions(self, tmp_path):
        tls_dir = tmp_path / "tls"
        generate_self_signed_cert(tls_dir)
        for name in ("ca-key.pem", "server-key.pem"):
            mode = oct((tls_dir / name).stat().st_mode & 0o777)
            assert mode == "0o600"


class TestApiSetupCommand:
    """Tests for the api-setup CLI command."""

    def test_api_setup_generates_tokens_and_config(self, tmp_path):
        # First create a valid config via setup
        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"
        with mock.patch(
            "sys.argv",
            [
                "buncker",
                "--config",
                str(config_path),
                "setup",
                "--store-path",
                str(store_path),
            ],
        ):
            from buncker.__main__ import main

            main()

        # Now run api-setup
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-setup"],
        ):
            main()

        # Verify tokens file
        tokens_path = config_path.parent / "api-tokens.json"
        assert tokens_path.exists()
        tokens = json.loads(tokens_path.read_text())
        assert len(tokens["readonly"]) == 64
        assert len(tokens["admin"]) == 64

        # Verify config updated
        config = json.loads(config_path.read_text())
        assert config["api"]["enabled"] is True
        assert config["tls"] is True

        # Verify TLS files created
        tls_dir = store_path / "tls"
        assert (tls_dir / "server.pem").exists()
        assert (tls_dir / "server-key.pem").exists()
        assert (tls_dir / "ca.pem").exists()

    def test_api_setup_with_provided_cert(self, tmp_path):
        # Create config
        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"
        with mock.patch(
            "sys.argv",
            [
                "buncker",
                "--config",
                str(config_path),
                "setup",
                "--store-path",
                str(store_path),
            ],
        ):
            from buncker.__main__ import main

            main()

        # Create dummy cert/key files
        cert_file = tmp_path / "my-cert.pem"
        key_file = tmp_path / "my-key.pem"
        cert_file.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n")
        key_file.write_text("-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n")

        with mock.patch(
            "sys.argv",
            [
                "buncker",
                "--config",
                str(config_path),
                "api-setup",
                "--cert",
                str(cert_file),
                "--key",
                str(key_file),
            ],
        ):
            main()

        # Verify cert was copied
        tls_dir = store_path / "tls"
        assert (tls_dir / "server.pem").read_text() == cert_file.read_text()
        assert (tls_dir / "server-key.pem").read_text() == key_file.read_text()

    def test_api_setup_fails_without_config(self, tmp_path):
        config_path = tmp_path / "nonexistent.json"
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-setup"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                from buncker.__main__ import main

                main()
            assert exc_info.value.code == 1

    def test_api_setup_fails_if_already_run(self, tmp_path):
        # Create config
        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"
        with mock.patch(
            "sys.argv",
            [
                "buncker",
                "--config",
                str(config_path),
                "setup",
                "--store-path",
                str(store_path),
            ],
        ):
            from buncker.__main__ import main

            main()

        # Create tokens file to simulate prior api-setup
        tokens_path = config_path.parent / "api-tokens.json"
        tokens_path.write_text('{"readonly": "x", "admin": "y"}')

        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-setup"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


class TestStartupValidation:
    """Tests for daemon startup checks."""

    def test_serve_refuses_api_enabled_without_tls(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": 5555,
                    "max_workers": 1,
                    "store_path": str(tmp_path / "store"),
                    "log_level": "INFO",
                    "api": {"enabled": True},
                    "tls": False,
                }
            )
        )

        with (
            mock.patch.dict(os.environ, {"BUNCKER_MNEMONIC": "test words"}),
            mock.patch(
                "sys.argv",
                ["buncker", "--config", str(config_path), "serve"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            from buncker.__main__ import main

            main()
        assert exc_info.value.code == 1
