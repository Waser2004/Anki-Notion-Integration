"""Tests for settings storage and defaults."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import keyring
from keyring.backend import KeyringBackend

from Noteck.modules.db import Database
from Noteck.modules.settings import (
    KeyringSecretStore,
    SettingsError,
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
        self.assertEqual(self._db.get_setting("default_card_type"), "basic")
        self.assertEqual(self._db.get_setting("enable_cloze_parsing"), "1")
        self.assertEqual(self._db.get_setting("enable_gray_toggle_cloze_parsing"), "1")
        self.assertEqual(self._db.get_setting("enable_image_occlusion_parsing"), "0")
        self.assertIsNone(self._db.get_setting("restore_default_card_templates"))
        self.assertEqual(self._db.get_setting("sync_with_anki_sync_button"), "0")
        self.assertEqual(self._db.get_setting("notion_to_anki_auto_sync"), "1")
        self.assertEqual(self._db.get_setting("page_selection_behavior"), "existing_descendants")
        self.assertIsNone(self._db.get_setting("sync_notion_now"))
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

    def test_button_settings_are_actions_only(self) -> None:
        store = SettingsStore(self._db, profile_name="test", schema=self._schema)
        self.assertIsNone(store.get_value("sync_notion_now"))
        self.assertIsNone(store.get_value("restore_default_card_templates"))
        with self.assertRaises(SettingsError):
            store.set_value("sync_notion_now", "clicked")
        with self.assertRaises(SettingsError):
            store.set_value("restore_default_card_templates", "clicked")

    def test_keyring_secret_store_operations(self) -> None:
        secret_store = KeyringSecretStore("Noteck", "profile-a")
        self.assertIsNone(secret_store.get_secret("notion_api_key"))
        secret_store.set_secret("notion_api_key", "value-1")
        self.assertEqual(secret_store.get_secret("notion_api_key"), "value-1")
        secret_store.delete_secret("notion_api_key")
        self.assertIsNone(secret_store.get_secret("notion_api_key"))
        secret_store.delete_secret("notion_api_key")

    def test_dropdown_setting_rejects_invalid_value(self) -> None:
        store = SettingsStore(self._db, profile_name="test", schema=self._schema)
        with self.assertRaises(SettingsError):
            store.set_value("default_card_type", "unsupported")
        with self.assertRaises(SettingsError):
            store.set_value("page_selection_behavior", "unsupported")

    def test_settings_schema_provides_custom_tooltips(self) -> None:
        for setting in self._schema.settings():
            self.assertIsNotNone(setting.tooltip, setting.key)
            self.assertNotEqual(setting.tooltip, setting.description, setting.key)

    def test_grouped_settings_include_custom_tooltips(self) -> None:
        store = SettingsStore(self._db, profile_name="test", schema=self._schema)
        grouped_settings = store.get_grouped_settings()
        setting_payloads = {
            setting["key"]: setting
            for category in grouped_settings
            for setting in category["settings"]
        }
        self.assertEqual(
            setting_payloads["page_selection_behavior"]["tooltip"],
            "Choose how parent and child page checkboxes behave.",
        )
