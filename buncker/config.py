"""Configuration loading and validation for Buncker."""

from __future__ import annotations

import json
from pathlib import Path

from shared.exceptions import ConfigError

_DEFAULT_CONFIG_PATH = Path("/etc/buncker/config.json")

_DEFAULTS = {
    "source_id": "",
    "bind": "0.0.0.0",
    "port": 5000,
    "store_path": "/var/lib/buncker",
    "max_workers": 16,
    "tls": False,
    "crypto": {},
    "private_registries": [],
    "gc": {"inactive_days_threshold": 90},
    "log_level": "INFO",
    "transfer_path": "",
}


def load_config(path: Path | None = None) -> dict:
    """Load configuration from a JSON file with defaults.

    Args:
        path: Path to config file. Uses /etc/buncker/config.json if None.

    Returns:
        Merged config dict (defaults + file values).

    Raises:
        ConfigError: If the file is not valid JSON or validation fails.
    """
    config_path = path or _DEFAULT_CONFIG_PATH

    config = dict(_DEFAULTS)

    if config_path.exists():
        try:
            raw = config_path.read_text(encoding="utf-8")
            file_config = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"Invalid JSON in config file: {config_path}",
                {"path": str(config_path), "error": str(exc)},
            ) from exc

        config.update(file_config)

    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    """Validate configuration values.

    Raises:
        ConfigError: If any value is invalid.
    """
    port = config.get("port")
    if not isinstance(port, int) or port < 0 or port > 65535:
        raise ConfigError(
            f"Invalid port: {port} (must be 0-65535)",
            {"port": port},
        )

    max_workers = config.get("max_workers")
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ConfigError(
            f"Invalid max_workers: {max_workers} (must be >= 1)",
            {"max_workers": max_workers},
        )

    store_path = config.get("store_path")
    if not store_path or not isinstance(store_path, str):
        raise ConfigError(
            "store_path must be a non-empty string",
            {"store_path": store_path},
        )

    log_level = config.get("log_level", "INFO")
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if log_level not in valid_levels:
        raise ConfigError(
            f"Invalid log_level: {log_level}",
            {"log_level": log_level, "valid": list(valid_levels)},
        )


def save_config(config: dict, path: Path) -> None:
    """Save configuration to a JSON file.

    Args:
        config: Configuration dict to save.
        path: Path to write the config file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
