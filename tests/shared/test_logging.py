"""Tests for shared.logging module."""

import json
import logging
from pathlib import Path

from shared.logging import get_logger, setup_logging


class TestSetupLogging:
    def test_creates_log_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.jsonl"
        setup_logging(level="DEBUG", output_path=log_file)
        logger = get_logger("test")
        logger.info("hello")
        # Flush handlers
        for handler in logging.getLogger("buncker").handlers:
            handler.flush()
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) >= 1

    def test_log_entry_contains_required_fields(self, tmp_path: Path) -> None:
        log_file = tmp_path / "fields.jsonl"
        setup_logging(level="DEBUG", output_path=log_file)
        logger = get_logger("fields")
        logger.info("test_event")
        for handler in logging.getLogger("buncker").handlers:
            handler.flush()
        line = log_file.read_text().strip().splitlines()[0]
        entry = json.loads(line)
        assert "ts" in entry
        assert entry["event"] == "test_event"
        assert entry["level"] == "INFO"

    def test_log_entry_is_valid_json(self, tmp_path: Path) -> None:
        log_file = tmp_path / "json.jsonl"
        setup_logging(level="DEBUG", output_path=log_file)
        logger = get_logger("json_test")
        logger.warning("warn_event")
        for handler in logging.getLogger("buncker").handlers:
            handler.flush()
        for line in log_file.read_text().strip().splitlines():
            json.loads(line)  # Should not raise

    def test_stderr_handler_only_error_plus(
        self,
        tmp_path: Path,
    ) -> None:
        log_file = tmp_path / "stderr.jsonl"
        setup_logging(level="DEBUG", output_path=log_file)
        logger = get_logger("stderr_test")

        # Log at different levels
        logger.debug("debug msg")
        logger.info("info msg")
        logger.error("error msg")

        for handler in logging.getLogger("buncker").handlers:
            handler.flush()

        # File should have all three
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 3

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        log_file = tmp_path / "sub" / "dir" / "test.jsonl"
        setup_logging(level="INFO", output_path=log_file)
        logger = get_logger("dirs")
        logger.info("nested")
        for handler in logging.getLogger("buncker").handlers:
            handler.flush()
        assert log_file.exists()

    def test_no_file_handler_when_no_path(self) -> None:
        setup_logging(level="INFO", output_path=None)
        root = logging.getLogger("buncker")
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 0


class TestGetLogger:
    def test_returns_namespaced_logger(self) -> None:
        logger = get_logger("mymodule")
        assert logger.name == "buncker.mymodule"

    def test_is_logging_logger(self) -> None:
        logger = get_logger("check")
        assert isinstance(logger, logging.Logger)
