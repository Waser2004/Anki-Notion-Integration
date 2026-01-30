"""Tests for settings storage and defaults."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import keyring
from keyring.backend import KeyringBackend

from anki_notion_integration.db import Database
from anki_notion_integration.settings import (
    SettingsStore,
    create_default_settings,
    load_settings_schema,
)


class InMemoryKeyring(KeyringBackend):
    """Simple in-memory keyring for tests."""

    priority = 1

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


class SettingsTests(unittest.TestCase):
    """Exercise default settings and secret handling."""

    def setUp(self) -> None:
        self._keyring = InMemoryKeyring()
        keyring.set_keyring(self._keyring)
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        db_path = Path(self._temp_dir.name) / "settings.db"
        self._db = Database(db_path)
        self._db.initialize()
        self._schema = load_settings_schema()

    def test_default_settings_inserted(self) -> None:
        create_default_settings(self._db, self._schema)
        self.assertEqual(self._db.get_setting("notion_to_anki_auto_sync"), "1")
        self.assertEqual(self._db.get_setting("anki_to_notion_sync"), "0")
        self.assertIsNone(self._db.get_setting("notion_api_key"))

    def test_defaults_do_not_override_existing(self) -> None:
        self._db.set_setting("notion_to_anki_auto_sync", "0")
        create_default_settings(self._db, self._schema)
        self.assertEqual(self._db.get_setting("notion_to_anki_auto_sync"), "0")

    def test_secret_round_trip(self) -> None:
        store = SettingsStore(self._db, profile_name="test", schema=self._schema)
        store.set_value("notion_api_key", "secret-value")
        self.assertEqual(store.get_value("notion_api_key"), "secret-value")
        store.set_value("notion_api_key", "")
        self.assertEqual(store.get_value("notion_api_key"), "")
