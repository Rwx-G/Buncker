"""Tests for buncker.config - configuration loading and validation."""

from __future__ import annotations

import json

import pytest

from buncker.config import load_config, save_config, validate_config
from shared.exceptions import ConfigError


class TestLoadConfig:
    """Tests for load_config()."""

    def test_loads_with_defaults(self, tmp_path):
        config_path = tmp_path / "config.json"
        # No file -> defaults
        config = load_config(config_path)
        assert config["bind"] == "127.0.0.1"
        assert config["port"] == 5000
        assert config["max_workers"] == 16
        assert config["tls"] is False
        assert config["log_level"] == "INFO"

    def test_loads_from_file(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "port": 8080,
                    "source_id": "my-buncker",
                }
            )
        )
        config = load_config(config_path)
        assert config["port"] == 8080
        assert config["source_id"] == "my-buncker"
        # Defaults still present for unset keys
        assert config["bind"] == "127.0.0.1"
        assert config["max_workers"] == 16

    def test_invalid_json_raises(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text("not valid json")
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_config(config_path)

    def test_file_overrides_defaults(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"max_workers": 4}))
        config = load_config(config_path)
        assert config["max_workers"] == 4


class TestValidateConfig:
    """Tests for validate_config()."""

    def test_valid_config(self):
        config = {
            "port": 5000,
            "max_workers": 16,
            "store_path": "/var/lib/buncker",
            "log_level": "INFO",
        }
        validate_config(config)  # Should not raise

    def test_invalid_port_negative(self):
        config = {
            "port": -1,
            "max_workers": 1,
            "store_path": "/tmp",
            "log_level": "INFO",
        }
        with pytest.raises(ConfigError, match="Invalid port"):
            validate_config(config)

    def test_invalid_port_too_high(self):
        config = {
            "port": 70000,
            "max_workers": 1,
            "store_path": "/tmp",
            "log_level": "INFO",
        }
        with pytest.raises(ConfigError, match="Invalid port"):
            validate_config(config)

    def test_invalid_port_type(self):
        config = {
            "port": "5000",
            "max_workers": 1,
            "store_path": "/tmp",
            "log_level": "INFO",
        }
        with pytest.raises(ConfigError, match="Invalid port"):
            validate_config(config)

    def test_invalid_max_workers(self):
        config = {
            "port": 5000,
            "max_workers": 0,
            "store_path": "/tmp",
            "log_level": "INFO",
        }
        with pytest.raises(ConfigError, match="Invalid max_workers"):
            validate_config(config)

    def test_empty_store_path(self):
        config = {"port": 5000, "max_workers": 1, "store_path": "", "log_level": "INFO"}
        with pytest.raises(ConfigError, match="store_path"):
            validate_config(config)

    def test_invalid_log_level(self):
        config = {
            "port": 5000,
            "max_workers": 1,
            "store_path": "/tmp",
            "log_level": "VERBOSE",
        }
        with pytest.raises(ConfigError, match="Invalid log_level"):
            validate_config(config)

    def test_invalid_gc_threshold_zero(self):
        config = {
            "port": 5000,
            "max_workers": 1,
            "store_path": "/tmp",
            "log_level": "INFO",
            "gc": {"inactive_days_threshold": 0},
        }
        with pytest.raises(ConfigError, match="inactive_days_threshold"):
            validate_config(config)

    def test_invalid_gc_threshold_negative(self):
        config = {
            "port": 5000,
            "max_workers": 1,
            "store_path": "/tmp",
            "log_level": "INFO",
            "gc": {"inactive_days_threshold": -5},
        }
        with pytest.raises(ConfigError, match="inactive_days_threshold"):
            validate_config(config)

    def test_valid_gc_threshold(self):
        config = {
            "port": 5000,
            "max_workers": 1,
            "store_path": "/tmp",
            "log_level": "INFO",
            "gc": {"inactive_days_threshold": 30},
        }
        validate_config(config)  # Should not raise

    def test_invalid_transfer_path_type(self):
        config = {
            "port": 5000,
            "max_workers": 1,
            "store_path": "/tmp",
            "log_level": "INFO",
            "transfer_path": 123,
        }
        with pytest.raises(ConfigError, match="transfer_path"):
            validate_config(config)

    def test_unknown_key_warns(self, tmp_path, caplog):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"portt": 8080}))
        import logging

        with caplog.at_level(logging.WARNING, logger="buncker.config"):
            load_config(config_path)
        assert any(
            "config_unknown_key" in r.message for r in caplog.records
        )


class TestSaveConfig:
    """Tests for save_config()."""

    def test_saves_and_loads(self, tmp_path):
        config = {
            "port": 8080,
            "source_id": "test",
            "max_workers": 8,
            "store_path": "/tmp/test",
            "log_level": "DEBUG",
        }
        config_path = tmp_path / "subdir" / "config.json"
        save_config(config, config_path)
        assert config_path.exists()
        loaded = json.loads(config_path.read_text())
        assert loaded["port"] == 8080
        assert loaded["source_id"] == "test"
