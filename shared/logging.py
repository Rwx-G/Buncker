"""JSON Lines logging setup for Buncker."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

_EXTRA_DEFAULTS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__,
)


class JsonFormatter(logging.Formatter):
    """Formats log records as JSON Lines."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "event": record.getMessage(),
            "level": record.levelname,
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key not in _EXTRA_DEFAULTS and key not in (
                "message",
                "msg",
                "args",
            ):
                entry[key] = value
        return json.dumps(entry)


def setup_logging(
    level: str | int = "INFO",
    output_path: Path | None = None,
) -> None:
    """Configure Buncker logging with JSON Lines format.

    Args:
        level: Log level as string or int.
        output_path: Path for JSONL log file. If None, only stderr is used.
    """
    root = logging.getLogger("buncker")
    root.setLevel(level)

    # Clear existing handlers to avoid duplicates on repeated calls
    root.handlers.clear()

    formatter = JsonFormatter()

    # File handler (all levels)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(output_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)

    # Stderr handler (ERROR+ only)
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(formatter)
    stderr_handler.setLevel(logging.ERROR)
    root.addHandler(stderr_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the buncker namespace.

    Args:
        name: Logger name (will be prefixed with 'buncker.').

    Returns:
        Configured logger instance.
    """
    return logging.getLogger(f"buncker.{name}")
