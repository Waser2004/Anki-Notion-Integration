"""Tests for persistent, bounded diagnostic logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.logging_utils import BACKUP_LOG_COUNT, MAX_LOG_BYTES, configure_file_logging, log_file_path


class LoggingUtilsTests(unittest.TestCase):
    """Ensure the add-on configures one bounded log per profile."""

    def test_configures_rotating_log_beside_profile_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "Noteck" / "db" / "notion_integration.db"
            try:
                logger = configure_file_logging(db_path)
                logger.info("sync test event")

                path = log_file_path(db_path)
                self.assertTrue(path.exists())
                self.assertIn("sync test event", path.read_text(encoding="utf-8"))
                handlers = [handler for handler in logger.handlers if isinstance(handler, RotatingFileHandler)]
                self.assertTrue(handlers)
                self.assertEqual(handlers[-1].maxBytes, MAX_LOG_BYTES)
                self.assertEqual(handlers[-1].backupCount, BACKUP_LOG_COUNT)
            finally:
                self._close_noteck_handlers()

    def tearDown(self) -> None:
        # Remove handlers so every test gets an isolated temporary log destination.
        self._close_noteck_handlers()

    @staticmethod
    def _close_noteck_handlers() -> None:
        """Release Windows file locks before the temporary directory is removed."""
        logger = logging.getLogger("noteck")
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    unittest.main()
