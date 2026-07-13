"""File logging helpers for diagnostics that survive Anki restarts."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "noteck"
LOG_FILENAME = "noteck.log"
MAX_LOG_BYTES = 1_000_000
BACKUP_LOG_COUNT = 4


def configure_file_logging(db_path: str | Path) -> logging.Logger:
    """Configure Noteck's rotating per-profile diagnostic log and return its logger.

    Logs live beside the profile database, rather than in Anki's installation directory,
    so each Anki profile keeps only its own sync history.
    """
    log_path = Path(db_path).parent.parent / "logs" / LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    resolved_path = log_path.resolve()
    for handler in logger.handlers[:]:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == resolved_path:
            return logger
        if isinstance(handler, RotatingFileHandler):
            # Only one profile can be active in Anki. Close the previous profile's
            # handler so diagnostics never continue writing to its log file.
            logger.removeHandler(handler)
            handler.close()

    # Keep five one-megabyte files at most: the live file plus four numbered backups.
    handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_LOG_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    return logger


def log_file_path(db_path: str | Path) -> Path:
    """Return the profile-specific log location used by :func:`configure_file_logging`."""
    return Path(db_path).parent.parent / "logs" / LOG_FILENAME
