"""Database session helpers for SQLite-based dev persistence."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3


def _sqlite_path_from_url(database_url: str) -> Path:
    """Translate a sqlite URL into a local filesystem path."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Only sqlite URLs are supported in this milestone")

    raw_path = database_url[len(prefix) :]
    if raw_path.startswith("./"):
        return Path(raw_path[2:]).resolve()
    return Path(raw_path).resolve()


class Database:
    """Thin database wrapper around sqlite3 connections."""

    def __init__(self, database_url: str) -> None:
        self._path = _sqlite_path_from_url(database_url)

    @property
    def path(self) -> Path:
        """Return the local sqlite file path."""
        return self._path

    @contextmanager
    def connection(self):
        """Provide a transactional sqlite connection."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()
