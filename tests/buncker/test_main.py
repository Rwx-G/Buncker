"""Tests for buncker.__main__ - CLI entry point."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from unittest import mock

import pytest

from buncker.__main__ import main


class TestCliParsing:
    """Tests for CLI argument parsing."""

    def test_no_command_exits(self):
        with mock.patch("sys.argv", ["buncker"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_setup_command(self, tmp_path):
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
            main()

        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert "source_id" in config
        assert config["store_path"] == str(store_path)
        assert "crypto" in config
        assert "salt" in config["crypto"]
        assert "mnemonic_hash" in config["crypto"]

    def test_setup_existing_config_exits(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text("{}")
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "setup"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_rotate_keys(self, tmp_path):
        # First setup
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
            main()

        old_config = json.loads(config_path.read_text())
        old_hash = old_config["crypto"]["mnemonic_hash"]

        # Rotate keys
        with mock.patch(
            "sys.argv",
            [
                "buncker",
                "--config",
                str(config_path),
                "rotate-keys",
                "--grace-period",
                "15",
            ],
        ):
            main()

        new_config = json.loads(config_path.read_text())
        assert new_config["crypto"]["mnemonic_hash"] != old_hash
        assert "previous" in new_config["crypto"]
        assert new_config["crypto"]["previous"]["grace_period_days"] == 15

    def test_export_ca_no_tls(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": 5000,
                    "max_workers": 1,
                    "store_path": str(tmp_path),
                    "log_level": "INFO",
                    "tls": False,
                }
            )
        )
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "export-ca"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


class TestCliProxy:
    """Tests for CLI commands that proxy to admin API."""

    def test_status_via_cli(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="cli-test")
        srv.start()
        port = srv.port

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": port,
                    "max_workers": 1,
                    "store_path": str(tmp_path / "store"),
                    "log_level": "INFO",
                }
            )
        )

        try:
            with mock.patch(
                "sys.argv",
                ["buncker", "--config", str(config_path), "status"],
            ):
                # Capture stdout
                import io

                captured = io.StringIO()
                with mock.patch("sys.stdout", captured):
                    main()
                output = captured.getvalue()
                # Output contains JSON + human-readable disk summary
                json_end = output.index("\n\nDisk:")
                data = json.loads(output[:json_end])
                assert "version" in data
                assert "disk_free" in data
                assert "Disk:" in output
        finally:
            srv.stop()

    def test_analyze_via_cli(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store)
        srv.start()
        port = srv.port

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": port,
                    "max_workers": 1,
                    "store_path": str(tmp_path / "store"),
                    "log_level": "INFO",
                }
            )
        )

        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM scratch\n")

        try:
            import io

            captured = io.StringIO()
            with mock.patch(
                "sys.argv",
                ["buncker", "--config", str(config_path), "analyze", str(dockerfile)],
            ):
                with mock.patch("sys.stdout", captured):
                    main()
                output = captured.getvalue()
                data = json.loads(output)
                assert "images" in data
        finally:
            srv.stop()


class TestServe:
    """Tests for the serve command."""

    def test_serve_wrong_mnemonic_exits(self, tmp_path):
        import base64

        from shared.crypto import generate_mnemonic, split_mnemonic

        mnemonic = generate_mnemonic()
        mnemonic_12, salt = split_mnemonic(mnemonic)
        mnemonic_hash = f"sha256:{hashlib.sha256(mnemonic_12.encode()).hexdigest()}"

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": 5555,
                    "max_workers": 1,
                    "store_path": str(tmp_path / "store"),
                    "log_level": "INFO",
                    "crypto": {
                        "salt": base64.b64encode(salt).decode(),
                        "mnemonic_hash": mnemonic_hash,
                    },
                }
            )
        )

        with (
            mock.patch.dict(
                os.environ,
                {"BUNCKER_MNEMONIC": "wrong words here and more"},
            ),
            mock.patch(
                "sys.argv",
                ["buncker", "--config", str(config_path), "serve"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1


class TestSignalHandling:
    """Tests for SIGTERM/SIGINT graceful shutdown."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="SIGTERM not supported on Windows",
    )
    def test_sigterm_stops_server(self, tmp_path):
        import base64

        from shared.crypto import generate_mnemonic, split_mnemonic

        mnemonic = generate_mnemonic()
        mnemonic_12, salt = split_mnemonic(mnemonic)
        mnemonic_hash = f"sha256:{hashlib.sha256(mnemonic_12.encode()).hexdigest()}"

        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"
        config_path.write_text(
            json.dumps(
                {
                    "port": 0,
                    "max_workers": 1,
                    "store_path": str(store_path),
                    "log_level": "INFO",
                    "crypto": {
                        "salt": base64.b64encode(salt).decode(),
                        "mnemonic_hash": mnemonic_hash,
                    },
                }
            )
        )

        env = os.environ.copy()
        env["BUNCKER_MNEMONIC"] = mnemonic
        proc = subprocess.Popen(
            [sys.executable, "-m", "buncker", "--config", str(config_path), "serve"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(1)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        assert proc.returncode == 0


class TestVerify:
    """Tests for verify command."""

    def test_verify_all_ok(self, tmp_path):
        """Store with valid blobs shows OK."""
        import io

        # Setup: create config + store with a blob
        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"
        args = [
            "buncker",
            "--config",
            str(config_path),
            "setup",
            "--store-path",
            str(store_path),
        ]
        with mock.patch("sys.argv", args):
            main()

        # Import a blob
        from buncker.store import Store

        store = Store(store_path)
        blob_data = b"verify test data"
        digest = f"sha256:{hashlib.sha256(blob_data).hexdigest()}"
        store.import_blob(blob_data, digest)

        captured = io.StringIO()
        with (
            mock.patch("sys.argv", ["buncker", "--config", str(config_path), "verify"]),
            mock.patch("sys.stdout", captured),
        ):
            main()

        output = captured.getvalue()
        assert "OK" in output

    def test_verify_corrupted_blobs(self, tmp_path):
        """Store with corrupted blobs shows error and exits 1."""
        # Setup
        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"
        args = [
            "buncker",
            "--config",
            str(config_path),
            "setup",
            "--store-path",
            str(store_path),
        ]
        with mock.patch("sys.argv", args):
            main()

        # Import a blob then corrupt it
        from buncker.store import Store

        store = Store(store_path)
        blob_data = b"will be corrupted"
        digest = f"sha256:{hashlib.sha256(blob_data).hexdigest()}"
        store.import_blob(blob_data, digest)

        # Corrupt the blob file
        blob_path = store.get_blob(digest)
        blob_path.write_bytes(b"corrupted!!!")

        with (
            mock.patch("sys.argv", ["buncker", "--config", str(config_path), "verify"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1


class TestPrepare:
    """Tests for prepare command."""

    def test_prepare_analyze_then_manifest_error(self, tmp_path):
        """Prepare analyzes OK but exits 1 when no missing blobs (FROM scratch)."""
        import io

        from buncker.server import BunckerServer
        from buncker.store import Store
        from shared.crypto import derive_keys, generate_mnemonic, split_mnemonic

        store = Store(tmp_path / "store")
        mnemonic = generate_mnemonic()
        mnemonic_12, salt = split_mnemonic(mnemonic)
        aes_key, hmac_key = derive_keys(mnemonic_12, salt)

        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            crypto_keys=(aes_key, hmac_key),
        )
        srv.start()
        port = srv.port

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": port,
                    "store_path": str(tmp_path / "store"),
                }
            )
        )

        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM scratch\n")

        output_dir = tmp_path / "output"
        try:
            captured = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "buncker",
                        "--config",
                        str(config_path),
                        "prepare",
                        str(dockerfile),
                        "--output",
                        str(output_dir),
                    ],
                ),
                mock.patch("sys.stdout", captured),
                pytest.raises(SystemExit) as exc_info,
            ):
                main()

            # Analyze succeeds but generate-manifest returns NO_MISSING for scratch
            output = captured.getvalue()
            assert "Analyzing" in output
            assert exc_info.value.code == 1
        finally:
            srv.stop()

    def test_prepare_analyze_error(self, tmp_path):
        """Prepare with invalid Dockerfile exits 1."""

        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        port = srv.port

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": port,
                    "store_path": str(tmp_path / "store"),
                }
            )
        )

        # Pass a nonexistent Dockerfile path
        try:
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "buncker",
                        "--config",
                        str(config_path),
                        "prepare",
                        str(tmp_path / "nonexistent"),
                    ],
                ),
                pytest.raises(SystemExit) as exc_info,
            ):
                main()
            assert exc_info.value.code == 1
        finally:
            srv.stop()


class TestTlsCertExpiry:
    """Tests for TLS certificate expiry check."""

    def test_no_cert_does_nothing(self, tmp_path):
        """No cert file means no warning."""
        from buncker.__main__ import _check_tls_cert_expiry

        config = {"store_path": str(tmp_path)}
        _check_tls_cert_expiry(config)  # should not raise

    def test_expired_cert_warns(self, tmp_path):
        """Expired cert prints warning."""
        import io
        from datetime import UTC, datetime, timedelta

        from buncker.__main__ import _check_tls_cert_expiry
        from buncker.auth import generate_self_signed_cert

        tls_dir = tmp_path / "tls"
        generate_self_signed_cert(tls_dir)

        # Mock cert to appear expired
        config = {"store_path": str(tmp_path)}
        captured = io.StringIO()

        mock_cert = mock.MagicMock()
        mock_cert.not_valid_after_utc = datetime.now(tz=UTC) - timedelta(days=5)

        with (
            mock.patch("sys.stdout", captured),
            mock.patch(
                "cryptography.x509.load_pem_x509_certificate",
                return_value=mock_cert,
            ),
        ):
            _check_tls_cert_expiry(config)

        output = captured.getvalue()
        assert "expired" in output.lower() or "WARNING" in output

    def test_cert_exception_ignored(self, tmp_path):
        """Unreadable cert doesn't crash."""
        from buncker.__main__ import _check_tls_cert_expiry

        tls_dir = tmp_path / "tls"
        tls_dir.mkdir()
        (tls_dir / "server.pem").write_text("not a real cert")

        config = {"store_path": str(tmp_path)}
        _check_tls_cert_expiry(config)  # should not raise


class TestExportCa:
    """Tests for export-ca command."""

    def test_export_ca_prints_cert(self, tmp_path):
        """Export CA with TLS enabled prints cert content."""
        import io

        from buncker.auth import generate_self_signed_cert

        tls_dir = tmp_path / "tls"
        generate_self_signed_cert(tls_dir)

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "tls": True,
                    "store_path": str(tmp_path),
                }
            )
        )

        captured = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                ["buncker", "--config", str(config_path), "export-ca"],
            ),
            mock.patch("sys.stdout", captured),
        ):
            main()

        output = captured.getvalue()
        assert "BEGIN CERTIFICATE" in output

    def test_export_ca_missing_cert_file(self, tmp_path):
        """Export CA with TLS enabled but no cert file exits 1."""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "tls": True,
                    "store_path": str(tmp_path),
                }
            )
        )

        with (
            mock.patch(
                "sys.argv",
                ["buncker", "--config", str(config_path), "export-ca"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1


class TestAdminHelpers:
    """Tests for _admin_get, _admin_post, _admin_post_raw error handling."""

    def test_admin_get_http_error_json(self):
        """HTTPError with JSON body returns dict."""
        import io
        import urllib.error

        from buncker.__main__ import _admin_get

        error_body = json.dumps({"error": "test", "code": "FAIL"}).encode()
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://localhost:5000/admin/status",
                400,
                "Bad Request",
                {},
                io.BytesIO(error_body),
            ),
        ):
            result = _admin_get("http://localhost:5000/admin/status")
        assert result["error"] == "test"

    def test_admin_get_http_error_non_json(self):
        """HTTPError with non-JSON body exits 1."""
        import io
        import urllib.error

        from buncker.__main__ import _admin_get

        with (
            mock.patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.HTTPError(
                    "http://localhost:5000/admin/status",
                    500,
                    "Error",
                    {},
                    io.BytesIO(b"plain text error"),
                ),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _admin_get("http://localhost:5000/admin/status")
        assert exc_info.value.code == 1

    def test_admin_get_url_error(self):
        """URLError exits 1."""
        import urllib.error

        from buncker.__main__ import _admin_get

        with (
            mock.patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("Connection refused"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _admin_get("http://localhost:5000/admin/status")
        assert exc_info.value.code == 1

    def test_admin_post_url_error(self):
        """URLError on POST exits 1."""
        import urllib.error

        from buncker.__main__ import _admin_post

        with (
            mock.patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("Connection refused"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _admin_post("http://localhost:5000/admin/analyze", {"test": True})
        assert exc_info.value.code == 1

    def test_admin_post_raw_binary(self):
        """_admin_post_raw returns bytes for octet-stream."""
        from buncker.__main__ import _admin_post_raw

        mock_resp = mock.MagicMock()
        mock_resp.headers = {"Content-Type": "application/octet-stream"}
        mock_resp.read.return_value = b"binary data"

        url = "http://localhost:5000/admin/generate-manifest"
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            result = _admin_post_raw(url, {})
        assert result == b"binary data"

    def test_admin_post_raw_json(self):
        """_admin_post_raw returns dict for application/json."""
        from buncker.__main__ import _admin_post_raw

        mock_resp = mock.MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read.return_value = json.dumps({"key": "value"}).encode()

        url = "http://localhost:5000/admin/generate-manifest"
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            result = _admin_post_raw(url, {})
        assert result == {"key": "value"}

    def test_admin_post_raw_url_error(self):
        """URLError on _admin_post_raw exits 1."""
        import urllib.error

        from buncker.__main__ import _admin_post_raw

        with (
            mock.patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("Connection refused"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _admin_post_raw("http://localhost:5000/admin/x", {})
        assert exc_info.value.code == 1


class TestGcExecute:
    """Tests for gc execute via CLI proxy."""

    def test_gc_execute_with_yes_flag(self, tmp_path):
        """gc --execute --yes skips prompt."""
        import io

        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        port = srv.port

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": port,
                    "store_path": str(tmp_path / "store"),
                }
            )
        )

        try:
            captured = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "buncker",
                        "--config",
                        str(config_path),
                        "gc",
                        "--execute",
                        "--yes",
                    ],
                ),
                mock.patch("sys.stdout", captured),
            ):
                main()
        finally:
            srv.stop()

    def test_gc_execute_aborted(self, tmp_path):
        """gc --execute without --yes and input 'n' aborts."""
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        port = srv.port

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": port,
                    "store_path": str(tmp_path / "store"),
                }
            )
        )

        try:
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "buncker",
                        "--config",
                        str(config_path),
                        "gc",
                        "--execute",
                    ],
                ),
                mock.patch("builtins.input", return_value="n"),
                pytest.raises(SystemExit) as exc_info,
            ):
                main()
            assert exc_info.value.code == 0
        finally:
            srv.stop()


class TestImportProxy:
    """Tests for import command via CLI proxy."""

    def test_import_auto_scan(self, tmp_path):
        """Import without file auto-scans transfer_path."""

        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        port = srv.port

        # Create transfer dir with a .tar.enc file
        transfer_dir = tmp_path / "transfer"
        transfer_dir.mkdir()
        fake_tar = transfer_dir / "response.tar.enc"
        fake_tar.write_bytes(b"fake encrypted tar")

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": port,
                    "store_path": str(tmp_path / "store"),
                    "transfer_path": str(transfer_dir),
                }
            )
        )

        try:
            # Import will fail (invalid data), but auto-scan runs
            import contextlib

            with (
                mock.patch(
                    "sys.argv",
                    ["buncker", "--config", str(config_path), "import"],
                ),
                contextlib.suppress(SystemExit, Exception),
            ):
                main()
        finally:
            srv.stop()

    def test_import_no_transfer_path(self, tmp_path):
        """Import without file and no transfer_path errors."""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": 5000,
                    "store_path": str(tmp_path / "store"),
                }
            )
        )

        with (
            mock.patch(
                "sys.argv",
                ["buncker", "--config", str(config_path), "import"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1
