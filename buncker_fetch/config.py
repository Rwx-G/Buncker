"""Configuration loading and validation for buncker-fetch."""

from __future__ import annotations

import json
from pathlib import Path

from shared.exceptions import ConfigError

_DEFAULT_CONFIG_PATH = Path.home() / ".buncker" / "config.json"

_DEFAULTS: dict = {
    "derived_key_check": "",
    "salt": "",
    "registries": {},
    "transfer_path": "",
}


def load_config(path: Path | None = None) -> dict:
    """Load configuration from a JSON file with defaults.

    Args:
        path: Path to config file. Uses ~/.buncker/config.json if None.

    Returns:
        Merged config dict.

    Raises:
        ConfigError: If the file is not valid JSON.
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

    return config


def save_config(config: dict, path: Path | None = None) -> None:
    """Save configuration to a JSON file.

    Args:
        config: Configuration dict to save.
        path: Path to write. Uses ~/.buncker/config.json if None.
    """
    config_path = path or _DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def validate_config(config: dict) -> None:
    """Validate that config has required fields for operations.

    Raises:
        ConfigError: If required fields are missing.
    """
    if not config.get("salt"):
        raise ConfigError(
            "No salt in config. Run 'buncker-fetch pair' first.",
        )
    if not config.get("derived_key_check"):
        raise ConfigError(
            "No derived_key_check in config. Run 'buncker-fetch pair' first.",
        )
