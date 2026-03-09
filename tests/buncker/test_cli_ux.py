"""Tests for CLI UX improvements: ANSI helpers, setup, prepare."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest import mock

import pytest

from buncker.__main__ import _c, _resolve_transfer_path, main


class TestAnsiHelpers:
    """Tests for _c() colorize helper."""

    def test_colorize_on_tty(self):
        with mock.patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            result = _c("hello", "\033[32m")
            assert result == "\033[32mhello\033[0m"

    def test_no_color_on_pipe(self):
        with mock.patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = _c("hello", "\033[32m")
            assert result == "hello"


class TestResolveTransferPath:
    """Tests for _resolve_transfer_path()."""

    def test_empty_returns_none(self):
        assert _resolve_transfer_path({"transfer_path": ""}) is None

    def test_missing_returns_none(self):
        assert _resolve_transfer_path({}) is None

    def test_set_returns_path(self):
        result = _resolve_transfer_path({"transfer_path": "/media/usb"})
        assert result == Path("/media/usb")


class TestSetupMnemonicStorage:
    """Tests for _cmd_setup() mnemonic storage in env file."""

    def test_setup_creates_env_file(self, tmp_path):
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

        env_path = tmp_path / "env"
        assert env_path.exists()
        content = env_path.read_text()
        # Encrypted format on Linux (machine-id available), cleartext on Windows
        assert content.startswith("BUNCKER_MNEMONIC_ENC=") or content.startswith(
            "BUNCKER_MNEMONIC="
        )
        value = content.strip().split("=", 1)[1]
        assert len(value) > 0

    def test_setup_config_has_expected_fields(self, tmp_path):
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

        config = json.loads(config_path.read_text())
        assert "source_id" in config
        assert config["store_path"] == str(store_path)
        assert "crypto" in config
        assert "salt" in config["crypto"]
        assert "mnemonic_hash" in config["crypto"]

    def test_setup_colored_output(self, tmp_path):
        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"

        captured = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                [
                    "buncker",
                    "--config",
                    str(config_path),
                    "setup",
                    "--store-path",
                    str(store_path),
                ],
            ),
            mock.patch("sys.stdout", captured),
        ):
            main()

        output = captured.getvalue()
        assert "[1/4]" in output
        assert "[2/4]" in output
        assert "[3/4]" in output
        assert "[4/4]" in output
        assert "IMPORTANT" in output
        assert "Config:" in output


class TestSetupSystemctl:
    """Tests for systemctl integration in setup."""

    def test_setup_systemctl_failure_is_non_fatal(self, tmp_path):
        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"

        with (
            mock.patch(
                "sys.argv",
                [
                    "buncker",
                    "--config",
                    str(config_path),
                    "setup",
                    "--store-path",
                    str(store_path),
                ],
            ),
            mock.patch(
                "buncker.__main__.subprocess.run",
                side_effect=FileNotFoundError("systemctl not found"),
            ),
        ):
            # Should not raise
            main()

        assert config_path.exists()


class TestImportAutoScan:
    """Tests for import auto-scan with transfer_path."""

    def test_import_no_file_no_transfer_path_exits(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": 9999,
                    "max_workers": 1,
                    "store_path": str(tmp_path),
                    "log_level": "INFO",
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

    def test_import_auto_scan_finds_newest(self, tmp_path):
        transfer_dir = tmp_path / "transfer"
        transfer_dir.mkdir()

        # Create two .tar.enc files with different mtimes
        old_file = transfer_dir / "old-response.tar.enc"
        old_file.write_bytes(b"old")
        new_file = transfer_dir / "new-response.tar.enc"
        new_file.write_bytes(b"new")

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": 9999,
                    "max_workers": 1,
                    "store_path": str(tmp_path),
                    "log_level": "INFO",
                    "transfer_path": str(transfer_dir),
                }
            )
        )

        # The import will fail because daemon isn't running, but we can check
        # that auto-detection message appears
        captured = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                ["buncker", "--config", str(config_path), "import"],
            ),
            mock.patch("sys.stdout", captured),
            pytest.raises(SystemExit),
        ):
            main()

        output = captured.getvalue()
        assert "Auto-detected:" in output


class TestTransferPathConfig:
    """Tests for transfer_path in config defaults."""

    def test_buncker_config_has_transfer_path_default(self, tmp_path):
        from buncker.config import load_config

        config = load_config(tmp_path / "nonexistent.json")
        assert "transfer_path" in config
        assert config["transfer_path"] == ""

    def test_buncker_fetch_config_has_transfer_path_default(self, tmp_path):
        from buncker_fetch.config import load_config

        config = load_config(tmp_path / "nonexistent.json")
        assert "transfer_path" in config
        assert config["transfer_path"] == ""


class TestPrepareSubparser:
    """Tests for prepare subcommand parsing."""

    def test_prepare_registered_as_subcommand(self):
        # Verify prepare doesn't cause parse error
        with (
            mock.patch(
                "sys.argv",
                ["buncker", "prepare", "--help"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0
