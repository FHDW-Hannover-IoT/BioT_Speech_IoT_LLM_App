"""
app/logger.py
-------------
Centralised logging configuration for the BioT Sensor Assistant.

Sets up a single logger used across all modules:
    - Console output (stdout) — always on, coloured by level
    - File output (logs/biot.log) — always on, plain text, rotates at 5MB

Usage in any module:
    from app.logger import get_logger
    log = get_logger(__name__)

    log.info("Server started")
    log.warning("Database is empty")
    log.error("Agent error: %s", exc)

Log levels:
    DEBUG   — detailed internal state (SQL queries, tool calls, raw responses)
    INFO    — normal operational events (requests received, responses sent)
    WARNING — something unexpected but non-fatal (empty DB, unknown tool)
    ERROR   — something failed but the server kept running
    CRITICAL — something failed and the server may not recover
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "biot.log"
_MAX_BYTES = 5 * 1024 * 1024   # 5 MB per log file
_BACKUP_COUNT = 3               # keep biot.log, biot.log.1, biot.log.2, biot.log.3
_LOG_LEVEL = logging.DEBUG

# ── Formatters ────────────────────────────────────────────────────────────────

# File format: timestamp | level | module | message
_FILE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Console format: shorter, no date (already visible in terminal)
_CONSOLE_FORMAT = "%(levelname)-8s | %(name)s | %(message)s"


def _build_console_handler() -> logging.StreamHandler:
    """Stdout handler — all levels, concise format."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(_LOG_LEVEL)
    handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    return handler


def _build_file_handler() -> RotatingFileHandler:
    """
    Rotating file handler — writes to logs/biot.log.
    Rotates automatically when the file exceeds 5 MB.
    Keeps up to 3 old log files.
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        filename=str(_LOG_FILE),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(_LOG_LEVEL)
    handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
    return handler


def _configure_root_logger() -> None:
    """
    Configure the root logger once at import time.
    All child loggers created via get_logger() inherit these handlers.
    Called automatically when this module is first imported.
    """
    root = logging.getLogger("biot")
    if root.handlers:
        return  # already configured — avoid duplicate handlers on reload

    root.setLevel(_LOG_LEVEL)
    root.addHandler(_build_console_handler())
    root.addHandler(_build_file_handler())
    root.propagate = False  # don't pass to the root Python logger


# Run configuration on import
_configure_root_logger()


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger under the 'biot' namespace.

    Args:
        name: Typically __name__ of the calling module.
              e.g. get_logger(__name__) in app/agent.py returns 'biot.app.agent'

    Returns:
        A configured Logger instance that writes to both console and log file.

    Example:
        log = get_logger(__name__)
        log.info("Request received: %s", message)
        log.error("Tool dispatch failed: %s", exc)
    """
    # Prefix all loggers with 'biot.' so they inherit root biot config
    if not name.startswith("biot"):
        name = f"biot.{name}"
    return logging.getLogger(name)