"""Tests for ui.json schema parsing."""

from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.ui.ui import UiSchemaError, load_ui_schema


class UiSchemaTests(unittest.TestCase):
    """Exercise schema validation for the UI pages config."""

    def _write_schema(self, payload: dict) -> Path:
        """Write a payload to a temporary ui.json file."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "ui.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_load_default_schema(self) -> None:
        schema = load_ui_schema()
        self.assertGreaterEqual(len(schema.pages), 1)
        self.assertEqual(schema.pages[0].factory, "build_page")
        page_keys = [page.key for page in schema.pages]
        self.assertEqual(page_keys[:4], ["pages", "cards", "image_occlusion", "settings"])

    def test_missing_pages_key(self) -> None:
        path = self._write_schema({})
        with self.assertRaises(UiSchemaError):
            load_ui_schema(path)

    def test_invalid_page_entry(self) -> None:
        path = self._write_schema({"pages": [{"key": "settings"}]})
        with self.assertRaises(UiSchemaError):
            load_ui_schema(path)

    def test_duplicate_page_keys(self) -> None:
        path = self._write_schema(
            {
                "pages": [
                    {"key": "pages", "name": "Pages", "module": "x"},
                    {"key": "pages", "name": "Pages 2", "module": "y"},
                ]
            }
        )
        with self.assertRaises(UiSchemaError):
            load_ui_schema(path)
