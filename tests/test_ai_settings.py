"""Tests for AI settings parsing and defaults."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.ai_settings import (
    AI_GENERATE_CLOZE_VARIANTS_DIFFICULTY_KEY,
    AI_GENERATE_CLOZE_VARIANTS_ENABLED_KEY,
    AI_GENERATE_CLOZE_VARIANTS_KEEP_LENGTH_KEY,
    AI_GENERATE_CLOZE_VARIANTS_NO_TRICK_KEY,
    AI_GENERATE_CLOZE_VARIANTS_NUMBER_KEY,
    AI_GENERATE_CLOZE_VARIANTS_STYLE_KEY,
    AI_VARIANT_STYLE_OPTIONS,
    AiSettingsStore,
)
from Noteck.modules.db import Database


class AiSettingsStoreTests(unittest.TestCase):
    """Exercise cloze-specific settings defaults and normalization behavior."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        db_path = Path(self._temp_dir.name) / "ai_settings.db"
        self._db = Database(db_path)
        self._db.initialize()

    def test_cloze_variant_defaults(self) -> None:
        """Default cloze settings should be stable when keys are missing."""
        settings = AiSettingsStore(self._db, profile_name="test").get_settings()
        self.assertFalse(settings.generate_cloze_variants_enabled)
        self.assertEqual(settings.generate_cloze_number_variations, 3)
        self.assertEqual(settings.generate_cloze_style, "exam")
        self.assertEqual(settings.generate_cloze_difficulty, "medium")
        self.assertTrue(settings.generate_cloze_no_trick_questions)
        self.assertTrue(settings.generate_cloze_keep_length_similar)

    def test_cloze_variant_normalization_and_clamping(self) -> None:
        """Stored cloze settings should be normalized to valid runtime values."""
        self._db.set_setting(AI_GENERATE_CLOZE_VARIANTS_ENABLED_KEY, "yes")
        self._db.set_setting(AI_GENERATE_CLOZE_VARIANTS_NUMBER_KEY, "999")
        self._db.set_setting(AI_GENERATE_CLOZE_VARIANTS_STYLE_KEY, "not-a-style")
        self._db.set_setting(AI_GENERATE_CLOZE_VARIANTS_DIFFICULTY_KEY, "HARD")
        self._db.set_setting(AI_GENERATE_CLOZE_VARIANTS_NO_TRICK_KEY, "off")
        self._db.set_setting(AI_GENERATE_CLOZE_VARIANTS_KEEP_LENGTH_KEY, "false")

        settings = AiSettingsStore(self._db, profile_name="test").get_settings()
        self.assertTrue(settings.generate_cloze_variants_enabled)
        self.assertEqual(settings.generate_cloze_number_variations, 20)
        self.assertIn(settings.generate_cloze_style, AI_VARIANT_STYLE_OPTIONS)
        self.assertEqual(settings.generate_cloze_style, "exam")
        self.assertEqual(settings.generate_cloze_difficulty, "hard")
        self.assertFalse(settings.generate_cloze_no_trick_questions)
        self.assertFalse(settings.generate_cloze_keep_length_similar)


if __name__ == "__main__":
    unittest.main()
