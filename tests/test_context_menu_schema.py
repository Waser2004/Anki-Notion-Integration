"""Tests for context menu schema parsing."""

from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from anki_notion_integration.ui.context_menu_schema import ContextMenuSchemaError, load_context_menu_schema


class ContextMenuSchemaTests(unittest.TestCase):
    """Validate context_menus.json loader behavior and validation guards."""

    def _write_schema(self, payload: dict) -> Path:
        """Write one temporary context menu schema payload to disk."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "context_menus.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_load_default_schema(self) -> None:
        schema = load_context_menu_schema()
        self.assertGreaterEqual(len(schema.pages.on_item), 1)
        self.assertGreaterEqual(len(schema.cards.on_background), 1)

    def test_missing_group_raises(self) -> None:
        path = self._write_schema({"pages": {"on_item": [], "on_background": []}})
        with self.assertRaises(ContextMenuSchemaError):
            load_context_menu_schema(path)

    def test_invalid_entry_type_raises(self) -> None:
        path = self._write_schema(
            {
                "pages": {
                    "on_item": [
                        {
                            "type": "unsupported",
                            "key": "x",
                            "label": "X",
                            "target": "page",
                        }
                    ],
                    "on_background": [],
                },
                "cards": {"on_item": [], "on_background": []},
            }
        )
        with self.assertRaises(ContextMenuSchemaError):
            load_context_menu_schema(path)

    def test_duplicate_keys_in_same_section_raise(self) -> None:
        path = self._write_schema(
            {
                "pages": {
                    "on_item": [
                        {
                            "type": "action",
                            "key": "dup",
                            "label": "A",
                            "target": "page",
                        },
                        {
                            "type": "action",
                            "key": "dup",
                            "label": "B",
                            "target": "page",
                        },
                    ],
                    "on_background": [],
                },
                "cards": {"on_item": [], "on_background": []},
            }
        )
        with self.assertRaises(ContextMenuSchemaError):
            load_context_menu_schema(path)
