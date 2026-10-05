import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from atlas.config import Settings
from atlas.services.logging_setup import AtlasJsonFormatter, configure_logging, shutdown_logging


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    shutdown_logging()
    yield
    shutdown_logging()


def test_json_formatter_includes_extra_fields() -> None:
    record = logging.LogRecord(
        name="atlas.ask",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ask_complete",
        args=(),
        exc_info=None,
    )
    record.event = "ask_complete"
    record.request_id = "req-1"

    line = AtlasJsonFormatter().format(record)
    payload = json.loads(line)

    assert payload["message"] == "ask_complete"
    assert payload["event"] == "ask_complete"
    assert payload["request_id"] == "req-1"
    assert payload["level"] == "INFO"


def test_configure_logging_writes_json_lines_to_file(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "atlas.jsonl"
    settings = Settings(
        log_json=True,
        log_file_enabled=True,
        log_file_path=str(log_file),
        log_level="INFO",
    )
    configure_logging(settings)

    logger = logging.getLogger("atlas.test")
    logger.info("hello", extra={"event": "test_event", "count": 2})

    shutdown_logging()

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "test_event"
    assert payload["count"] == 2


def test_configure_logging_is_idempotent() -> None:
    settings = Settings(log_file_enabled=False, log_json=False)
    configure_logging(settings)
    atlas_logger = logging.getLogger("atlas")
    handler_count = len(atlas_logger.handlers)

    configure_logging(settings)

    assert len(atlas_logger.handlers) == handler_count


def test_text_formatter_when_log_json_disabled(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(log_json=False, log_file_enabled=False)
    configure_logging(settings)

    logging.getLogger("atlas.demo").info("plain line")
    shutdown_logging()

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "plain line" in combined
    assert "[atlas.demo]" in combined
