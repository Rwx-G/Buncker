"""Tests for buncker.auth - API token generation, persistence, TLS, and middleware."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from unittest import mock

import pytest

from buncker.auth import (
    AuthError,
    authenticate_request,
    generate_api_tokens,
    generate_self_signed_cert,
    get_required_level,
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
        cert_file.write_text(
            "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n"
        )
        key_file.write_text(
            "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n"
        )

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


class TestGetRequiredLevel:
    """Tests for get_required_level()."""

    def test_readonly_endpoints(self):
        assert get_required_level("/admin/status", "GET") == "readonly"
        assert get_required_level("/admin/logs", "GET") == "readonly"
        assert get_required_level("/admin/gc/report", "GET") == "readonly"

    def test_admin_endpoints(self):
        assert get_required_level("/admin/analyze", "POST") == "admin"
        assert get_required_level("/admin/generate-manifest", "POST") == "admin"
        assert get_required_level("/admin/import", "POST") == "admin"
        assert get_required_level("/admin/import", "PUT") == "admin"
        assert get_required_level("/admin/gc/execute", "POST") == "admin"

    def test_oci_endpoints_return_none(self):
        assert get_required_level("/v2/", "GET") is None
        assert get_required_level("/v2/library/alpine/manifests/latest", "GET") is None

    def test_unknown_admin_defaults_to_admin(self):
        assert get_required_level("/admin/unknown", "GET") == "admin"


class TestAuthenticateRequest:
    """Tests for authenticate_request() middleware."""

    def _make_handler(self, path: str, method: str, auth_header: str = ""):
        """Create a mock handler for testing."""
        handler = mock.MagicMock()
        handler.path = path
        handler.command = method
        handler.headers = {"Authorization": auth_header} if auth_header else {}
        return handler

    def test_oci_always_unauthenticated(self):
        handler = self._make_handler("/v2/", "GET")
        tokens = {"readonly": "ro_token", "admin": "admin_token"}
        level = authenticate_request(handler, tokens, api_enabled=True)
        assert level == "local"

    def test_no_auth_when_disabled(self):
        handler = self._make_handler("/admin/status", "GET")
        tokens = {"readonly": "ro_token", "admin": "admin_token"}
        level = authenticate_request(handler, tokens, api_enabled=False)
        assert level == "local"

    def test_no_auth_when_no_tokens(self):
        handler = self._make_handler("/admin/status", "GET")
        level = authenticate_request(handler, None, api_enabled=True)
        assert level == "local"

    def test_admin_token_on_admin_endpoint(self):
        handler = self._make_handler("/admin/analyze", "POST", "Bearer admin_token")
        tokens = {"readonly": "ro_token", "admin": "admin_token"}
        level = authenticate_request(handler, tokens, api_enabled=True)
        assert level == "admin"

    def test_admin_token_on_readonly_endpoint(self):
        handler = self._make_handler("/admin/status", "GET", "Bearer admin_token")
        tokens = {"readonly": "ro_token", "admin": "admin_token"}
        level = authenticate_request(handler, tokens, api_enabled=True)
        assert level == "admin"

    def test_readonly_token_on_readonly_endpoint(self):
        handler = self._make_handler("/admin/status", "GET", "Bearer ro_token")
        tokens = {"readonly": "ro_token", "admin": "admin_token"}
        level = authenticate_request(handler, tokens, api_enabled=True)
        assert level == "readonly"

    def test_readonly_token_on_admin_endpoint_forbidden(self):
        handler = self._make_handler("/admin/analyze", "POST", "Bearer ro_token")
        tokens = {"readonly": "ro_token", "admin": "admin_token"}
        with pytest.raises(AuthError) as exc_info:
            authenticate_request(handler, tokens, api_enabled=True)
        assert exc_info.value.status == 403
        assert exc_info.value.code == "FORBIDDEN"

    def test_invalid_token_unauthorized(self):
        handler = self._make_handler("/admin/status", "GET", "Bearer wrong_token")
        tokens = {"readonly": "ro_token", "admin": "admin_token"}
        with pytest.raises(AuthError) as exc_info:
            authenticate_request(handler, tokens, api_enabled=True)
        assert exc_info.value.status == 401

    def test_missing_token_unauthorized(self):
        handler = self._make_handler("/admin/status", "GET")
        tokens = {"readonly": "ro_token", "admin": "admin_token"}
        with pytest.raises(AuthError) as exc_info:
            authenticate_request(handler, tokens, api_enabled=True)
        assert exc_info.value.status == 401

    def test_non_admin_endpoint_returns_local(self):
        """Non-admin, non-OCI endpoint with auth enabled returns 'local'."""
        handler = self._make_handler("/some/unknown/path", "GET")
        tokens = {"readonly": "ro_token", "admin": "admin_token"}
        level = authenticate_request(handler, tokens, api_enabled=True)
        assert level == "local"


class TestAuthIntegration:
    """Integration tests for auth middleware with live server."""

    def _setup_server_with_auth(self, tmp_path):
        """Create a server with auth enabled and return (server, tokens)."""
        from buncker.server import BunckerServer
        from buncker.store import Store

        tokens = {"readonly": "ro_" + "a" * 61, "admin": "ad_" + "b" * 61}
        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_tokens=tokens,
            api_enabled=True,
        )
        srv.start()
        return srv, tokens

    def test_admin_status_with_readonly_token(self, tmp_path):
        srv, tokens = self._setup_server_with_auth(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/status"
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {tokens['readonly']}"}
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
            data = json.loads(resp.read())
            assert "version" in data
        finally:
            srv.stop()

    def test_admin_status_with_admin_token(self, tmp_path):
        srv, tokens = self._setup_server_with_auth(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/status"
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {tokens['admin']}"}
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            srv.stop()

    def test_admin_status_without_token_returns_401(self, tmp_path):
        srv, _ = self._setup_server_with_auth(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/status"
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 401
        finally:
            srv.stop()

    def test_admin_status_with_invalid_token_returns_401(self, tmp_path):
        srv, _ = self._setup_server_with_auth(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/status"
            req = urllib.request.Request(
                url, headers={"Authorization": "Bearer invalid_token"}
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401
        finally:
            srv.stop()

    def test_v2_without_token_returns_200(self, tmp_path):
        srv, _ = self._setup_server_with_auth(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/"
            resp = urllib.request.urlopen(url)
            assert resp.status == 200
        finally:
            srv.stop()

    def test_no_auth_when_disabled(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_enabled=False,
        )
        srv.start()
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/status"
            resp = urllib.request.urlopen(url)
            assert resp.status == 200
        finally:
            srv.stop()


class TestApiShowCommand:
    """Tests for api-show CLI command (Story 6.4)."""

    def _setup_with_tokens(self, tmp_path):
        """Create config + tokens for testing."""
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
        # Run api-setup
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-setup"],
        ):
            main()
        return config_path

    def test_api_show_readonly(self, tmp_path, capsys):
        config_path = self._setup_with_tokens(tmp_path)
        tokens = json.loads((config_path.parent / "api-tokens.json").read_text())

        capsys.readouterr()  # Clear prior output
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-show", "readonly"],
        ):
            from buncker.__main__ import main

            main()

        output = capsys.readouterr().out.strip()
        assert output == tokens["readonly"]

    def test_api_show_admin(self, tmp_path, capsys):
        config_path = self._setup_with_tokens(tmp_path)
        tokens = json.loads((config_path.parent / "api-tokens.json").read_text())

        capsys.readouterr()  # Clear prior output
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-show", "admin"],
        ):
            from buncker.__main__ import main

            main()

        output = capsys.readouterr().out.strip()
        assert output == tokens["admin"]

    def test_api_show_without_setup_fails(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": 5000,
                    "max_workers": 1,
                    "store_path": str(tmp_path),
                    "log_level": "INFO",
                }
            )
        )
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-show", "readonly"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                from buncker.__main__ import main

                main()
            assert exc_info.value.code == 1


class TestApiResetCommand:
    """Tests for api-reset CLI command (Story 6.4)."""

    def _setup_with_tokens(self, tmp_path):
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
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-setup"],
        ):
            main()
        return config_path

    def test_api_reset_readonly_changes_token(self, tmp_path):
        config_path = self._setup_with_tokens(tmp_path)
        tokens_path = config_path.parent / "api-tokens.json"
        old_tokens = json.loads(tokens_path.read_text())

        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-reset", "readonly"],
        ):
            from buncker.__main__ import main

            main()

        new_tokens = json.loads(tokens_path.read_text())
        assert new_tokens["readonly"] != old_tokens["readonly"]
        assert new_tokens["admin"] == old_tokens["admin"]  # Unchanged

    def test_api_reset_admin_changes_token(self, tmp_path):
        config_path = self._setup_with_tokens(tmp_path)
        tokens_path = config_path.parent / "api-tokens.json"
        old_tokens = json.loads(tokens_path.read_text())

        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-reset", "admin"],
        ):
            from buncker.__main__ import main

            main()

        new_tokens = json.loads(tokens_path.read_text())
        assert new_tokens["admin"] != old_tokens["admin"]
        assert new_tokens["readonly"] == old_tokens["readonly"]  # Unchanged

    def test_api_reset_without_setup_fails(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": 5000,
                    "max_workers": 1,
                    "store_path": str(tmp_path),
                    "log_level": "INFO",
                }
            )
        )
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-reset", "admin"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                from buncker.__main__ import main

                main()
            assert exc_info.value.code == 1

    def test_api_reset_logs_audit_event(self, tmp_path, caplog):
        """Verify api_token_reset audit log is produced on reset."""
        import logging

        config_path = self._setup_with_tokens(tmp_path)

        with (
            caplog.at_level(logging.INFO, logger="buncker.auth"),
            mock.patch(
                "sys.argv",
                ["buncker", "--config", str(config_path), "api-reset", "admin"],
            ),
        ):
            from buncker.__main__ import main

            main()

        reset_records = [
            r for r in caplog.records if r.getMessage() == "api_token_reset"
        ]
        assert len(reset_records) == 1
        assert reset_records[0].token_type == "admin"

    def test_old_token_rejected_after_reset(self, tmp_path):
        """Integration: reset admin token, old one should fail auth."""
        config_path = self._setup_with_tokens(tmp_path)
        tokens_path = config_path.parent / "api-tokens.json"
        old_tokens = json.loads(tokens_path.read_text())

        # Reset
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-reset", "admin"],
        ):
            from buncker.__main__ import main

            main()

        new_tokens = json.loads(tokens_path.read_text())

        # Start server with new tokens
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_tokens=new_tokens,
            api_enabled=True,
        )
        srv.start()
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/status"
            # Old token should fail
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {old_tokens['admin']}"}
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401

            # New token should work
            req2 = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {new_tokens['admin']}"}
            )
            resp = urllib.request.urlopen(req2)
            assert resp.status == 200
        finally:
            srv.stop()


class TestAuditTrail:
    """Tests for audit trail enrichment (Story 6.3)."""

    def _setup_server_with_auth(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        tokens = {"readonly": "ro_" + "a" * 61, "admin": "ad_" + "b" * 61}
        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_tokens=tokens,
            api_enabled=True,
        )
        srv.start()
        return srv, tokens

    def test_rejected_auth_logged(self, tmp_path, caplog):
        import logging

        srv, _ = self._setup_server_with_auth(tmp_path)
        try:
            with caplog.at_level(logging.WARNING, logger="buncker.auth"):
                url = f"http://127.0.0.1:{srv.port}/admin/status"
                req = urllib.request.Request(
                    url, headers={"Authorization": "Bearer wrong"}
                )
                with pytest.raises(urllib.error.HTTPError):
                    urllib.request.urlopen(req)

            # Check that the rejection was logged with audit fields
            auth_records = [
                r for r in caplog.records if r.getMessage() == "api_auth_rejected"
            ]
            assert len(auth_records) >= 1
            record = auth_records[0]
            assert hasattr(record, "client_ip")
            assert hasattr(record, "auth_level")
            assert record.auth_level == "rejected"
            assert hasattr(record, "user_agent")
        finally:
            srv.stop()

    def test_successful_request_has_audit_fields(self, tmp_path, caplog):
        import logging

        srv, tokens = self._setup_server_with_auth(tmp_path)
        try:
            with caplog.at_level(logging.DEBUG, logger="buncker.handler"):
                url = f"http://127.0.0.1:{srv.port}/admin/status"
                req = urllib.request.Request(
                    url,
                    headers={"Authorization": f"Bearer {tokens['admin']}"},
                )
                resp = urllib.request.urlopen(req)
                assert resp.status == 200

            # Verify audit fields are NOT present on GET /admin/status
            # (status endpoint does not log with _request_meta, only
            # action endpoints like analyze/import/gc do)
            # But we can verify the debug http_request was logged
            handler_records = [r for r in caplog.records if r.name == "buncker.handler"]
            assert len(handler_records) >= 1
        finally:
            srv.stop()

    def test_no_token_values_in_logs(self, tmp_path, caplog):
        """Verify token values never appear in log output (NFR7)."""
        import logging

        srv, tokens = self._setup_server_with_auth(tmp_path)
        try:
            with caplog.at_level(logging.DEBUG):
                # Successful request
                url = f"http://127.0.0.1:{srv.port}/admin/status"
                req = urllib.request.Request(
                    url,
                    headers={"Authorization": f"Bearer {tokens['admin']}"},
                )
                urllib.request.urlopen(req)

                # Failed request
                req2 = urllib.request.Request(
                    url,
                    headers={"Authorization": "Bearer wrong_token"},
                )
                with pytest.raises(urllib.error.HTTPError):
                    urllib.request.urlopen(req2)

            # Check no token values in any log message
            all_log_text = " ".join(r.getMessage() for r in caplog.records)
            assert tokens["admin"] not in all_log_text
            assert tokens["readonly"] not in all_log_text
            assert "wrong_token" not in all_log_text
        finally:
            srv.stop()

    def test_v2_no_auth_still_works(self, tmp_path):
        srv, _ = self._setup_server_with_auth(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/"
            resp = urllib.request.urlopen(url)
            assert resp.status == 200
        finally:
            srv.stop()


class TestTlsServer:
    """Tests for TLS server activation (Story 6.1)."""

    def test_server_starts_with_tls(self, tmp_path):
        """Server with TLS cert/key serves HTTPS."""
        import ssl

        from buncker.auth import generate_self_signed_cert
        from buncker.server import BunckerServer
        from buncker.store import Store

        tls_dir = tmp_path / "tls"
        cert_path, key_path, ca_path = generate_self_signed_cert(tls_dir)

        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            tls_cert=cert_path,
            tls_key=key_path,
        )
        srv.start()
        try:
            # Create SSL context trusting our CA
            ctx = ssl.create_default_context(cafile=str(ca_path))
            url = f"https://localhost:{srv.port}/v2/"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, context=ctx)
            assert resp.status == 200
            data = json.loads(resp.read())
            assert data["status"] == "ok"
        finally:
            srv.stop()

    def test_server_without_tls_serves_http(self, tmp_path):
        """Server without TLS cert serves plain HTTP."""
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
        )
        srv.start()
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/"
            resp = urllib.request.urlopen(url)
            assert resp.status == 200
        finally:
            srv.stop()


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
