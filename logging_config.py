"""
Logging configuration with console and PostgreSQL database handlers.

Usage:
    from logging_config import get_logger
    logger = get_logger("api")
    logger.info("Server started", extra={"user_id": "123"})
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DBSession

# ANSI color codes for console output
COLORS = {
    "DEBUG": "\033[36m",  # Cyan
    "INFO": "\033[32m",  # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "CRITICAL": "\033[35m",  # Magenta
    "RESET": "\033[0m",
}

# Module-specific colors for tags
TAG_COLORS = {
    "api": "\033[94m",  # Blue
    "mem0": "\033[95m",  # Magenta
    "thread": "\033[96m",  # Cyan
    "discord": "\033[93m",  # Yellow
    "db": "\033[92m",  # Green
    "llm": "\033[91m",  # Red
    "email": "\033[97m",  # White
}


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ColoredConsoleFormatter(logging.Formatter):
    """Formatter that adds colors and matches existing tag-based style."""

    def format(self, record: logging.LogRecord) -> str:
        level_color = COLORS.get(record.levelname, "")
        reset = COLORS["RESET"]

        tag = record.name
        tag_color = TAG_COLORS.get(tag, "\033[37m")

        timestamp = datetime.now().strftime("%H:%M:%S")
        level_str = f"{level_color}{record.levelname:8}{reset}"
        tag_str = f"{tag_color}[{tag}]{reset}"

        extra_parts = []
        if hasattr(record, "user_id") and record.user_id:
            extra_parts.append(f"user={record.user_id}")
        if hasattr(record, "session_id") and record.session_id:
            extra_parts.append(f"session={record.session_id[:8]}")

        extra_str = f" ({', '.join(extra_parts)})" if extra_parts else ""
        msg = f"{timestamp} {level_str} {tag_str} {record.getMessage()}{extra_str}"

        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)

        return msg


class DatabaseHandler(logging.Handler):
    """Async logging handler that writes to PostgreSQL via background thread."""

    def __init__(self, level: int = logging.INFO):
        super().__init__(level)
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=1000)
        self._db_session_factory = None
        self._shutdown = False
        self._thread: threading.Thread | None = None

    def set_session_factory(self, session_factory):
        """Set the SQLAlchemy session factory and start the background thread."""
        self._db_session_factory = session_factory
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        """Background worker that writes logs to the database."""
        from models import LogEntry

        batch: list[dict[str, Any]] = []
        batch_size = 10
        flush_interval = 2.0

        while not self._shutdown:
            try:
                try:
                    record_dict = self._queue.get(timeout=flush_interval)
                    batch.append(record_dict)
                except Empty:
                    pass

                while len(batch) < batch_size:
                    try:
                        record_dict = self._queue.get_nowait()
                        batch.append(record_dict)
                    except Empty:
                        break

                if batch and self._db_session_factory:
                    self._flush_batch(batch, LogEntry)
                    batch = []

            except Exception as e:
                print(f"[logging] Database handler error: {e}", file=sys.stderr)
                batch = []

    def _flush_batch(self, batch: list[dict[str, Any]], LogEntry):
        """Write a batch of logs to the database."""
        session: DBSession | None = None
        try:
            session = self._db_session_factory()
            for record_dict in batch:
                entry = LogEntry(**record_dict)
                session.add(entry)
            session.commit()
        except Exception as e:
            if session:
                session.rollback()
            print(f"[logging] Failed to write logs: {e}", file=sys.stderr)
        finally:
            if session:
                session.close()

    def emit(self, record: logging.LogRecord):
        """Queue a log record for async database insertion."""
        if self._shutdown or self._db_session_factory is None:
            return

        try:
            extra_data = {}
            for key in ["request_id", "duration_ms", "status_code", "method", "path"]:
                if hasattr(record, key):
                    extra_data[key] = getattr(record, key)

            record_dict = {
                "timestamp": utcnow(),
                "level": record.levelname,
                "logger_name": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line_number": record.lineno,
                "exception": (
                    "".join(traceback.format_exception(*record.exc_info))
                    if record.exc_info
                    else None
                ),
                "extra_data": json.dumps(extra_data) if extra_data else None,
                "user_id": getattr(record, "user_id", None),
                "session_id": getattr(record, "session_id", None),
            }

            try:
                self._queue.put_nowait(record_dict)
            except Exception:
                pass  # Drop log if queue is full

        except Exception:
            self.handleError(record)

    def shutdown(self):
        """Gracefully shutdown the handler."""
        self._shutdown = True
        if self._thread:
            self._thread.join(timeout=5.0)


# Global state
_db_handler: DatabaseHandler | None = None
_initialized = False


def _get_console_level() -> int:
    """Get console log level from environment variable."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_name, logging.INFO)


def init_logging(session_factory=None, console_level: int | None = None):
    """Initialize the logging system with console and optional database handlers."""
    global _db_handler, _initialized

    if _initialized:
        return

    if console_level is None:
        console_level = _get_console_level()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(ColoredConsoleFormatter())

    _db_handler = DatabaseHandler(level=logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(_db_handler)

    if session_factory:
        _db_handler.set_session_factory(session_factory)

    _initialized = True


def set_db_session_factory(session_factory):
    """Set the database session factory after init."""
    global _db_handler
    if _db_handler:
        _db_handler.set_session_factory(session_factory)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    if not _initialized:
        init_logging()
    return logging.getLogger(name)


def shutdown_logging():
    """Gracefully shutdown logging."""
    global _db_handler
    if _db_handler:
        _db_handler.shutdown()
