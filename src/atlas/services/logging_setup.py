"""Structured logging for all atlas.* loggers (stdout + durable file on disk)."""

from __future__ import annotations

import json
import logging
import queue
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from typing import Any

from atlas.config import Settings

_LISTENER: QueueListener | None = None
_LOG_QUEUE: queue.Queue[logging.LogRecord] | None = None

# LogRecord built-ins and internal keys — never copy into JSON payload.
_RECORD_BUILTIN_KEYS = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class AtlasJsonFormatter(logging.Formatter):
    """One JSON object per line — safe for docker logs and log tailers."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _RECORD_BUILTIN_KEYS or key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, default=str)


def _text_formatter() -> logging.Formatter:
    return logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")


def _resolve_level(level_name: str) -> int:
    resolved = getattr(logging, level_name.upper(), None)
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def configure_logging(settings: Settings) -> None:
    """Attach handlers to the ``atlas`` logger tree (idempotent)."""
    global _LISTENER, _LOG_QUEUE

    atlas_logger = logging.getLogger("atlas")
    if _LISTENER is not None:
        return

    level = _resolve_level(settings.log_level)
    atlas_logger.setLevel(level)
    atlas_logger.propagate = False

    formatter: logging.Formatter
    if settings.log_json:
        formatter = AtlasJsonFormatter()
    else:
        formatter = _text_formatter()

    target_handlers: list[logging.Handler] = []

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    target_handlers.append(stream_handler)

    if settings.log_file_enabled:
        log_path = Path(settings.log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        target_handlers.append(file_handler)

    _LOG_QUEUE = queue.Queue(-1)
    atlas_logger.addHandler(QueueHandler(_LOG_QUEUE))
    _LISTENER = QueueListener(_LOG_QUEUE, *target_handlers, respect_handler_level=True)
    _LISTENER.start()


def shutdown_logging() -> None:
    """Stop the background log listener (tests and graceful shutdown)."""
    global _LISTENER, _LOG_QUEUE

    if _LISTENER is not None:
        _LISTENER.stop()
        _LISTENER = None
    _LOG_QUEUE = None

    atlas_logger = logging.getLogger("atlas")
    atlas_logger.handlers.clear()
