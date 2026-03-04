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
                data = json.loads(output)
                assert "version" in data
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

        from shared.crypto import generate_mnemonic

        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        mnemonic_hash = f"sha256:{hashlib.sha256(mnemonic.encode()).hexdigest()}"

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

        from shared.crypto import generate_mnemonic

        mnemonic = generate_mnemonic()
        salt = os.urandom(32)
        mnemonic_hash = f"sha256:{hashlib.sha256(mnemonic.encode()).hexdigest()}"

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
