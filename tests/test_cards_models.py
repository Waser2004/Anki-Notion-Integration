"""Tests for Noteck card template JavaScript behavior."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules import cards as cards_module


class CardTemplateTests(unittest.TestCase):
    """Validate cross-client variant and audio template behavior."""

    def test_front_template_avoids_web_storage(self) -> None:
        """Front template should not depend on local/session storage state."""
        template = cards_module._front_template(  # pylint: disable=protected-access
            question_field="Front",
            variants_field=cards_module.AI_FORWARD_VARIANTS_FIELD,
            audio_field=cards_module.AI_FORWARD_AUDIO_FIELD,
            direction="forward",
            include_typed_input=False,
        )
        self.assertNotIn("localStorage", template)
        self.assertNotIn("sessionStorage", template)

    def test_front_template_uses_deterministic_utc_index(self) -> None:
        """Front template should derive variant index from UTC day and stable hash."""
        template = cards_module._front_template(  # pylint: disable=protected-access
            question_field="Front",
            variants_field=cards_module.AI_FORWARD_VARIANTS_FIELD,
            audio_field=cards_module.AI_FORWARD_AUDIO_FIELD,
            direction="forward",
            include_typed_input=False,
        )
        self.assertIn("getUTCFullYear", template)
        self.assertIn("stableHash32", template)
        self.assertIn("deterministicVariantIndex", template)
        self.assertIn("+'|'+String(direction||'forward')+'|'+utcDateKey()", template)

    def test_front_template_has_audio_autoplay_fallback(self) -> None:
        """Front template should offer a manual play button when autoplay fails."""
        template = cards_module._front_template(  # pylint: disable=protected-access
            question_field="Front",
            variants_field=cards_module.AI_FORWARD_VARIANTS_FIELD,
            audio_field=cards_module.AI_FORWARD_AUDIO_FIELD,
            direction="forward",
            include_typed_input=False,
        )
        self.assertIn("tryAutoplayAudio", template)
        self.assertIn("noteck-ai-audio-fallback", template)
        self.assertIn("Play audio", template)
        self.assertIn("if(isBack){window.noteckIsBack=false;return;}", template)

    def test_cloze_template_uses_shared_deterministic_runtime(self) -> None:
        """Cloze runtime should share deterministic index and no web storage usage."""
        script = cards_module._cloze_audio_meta_script(direction="forward")  # pylint: disable=protected-access
        self.assertIn("deterministicVariantIndex(blockId,direction,variants.length)", script)
        self.assertIn("getUTCFullYear", script)
        self.assertIn("tryAutoplayAudio", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)


if __name__ == "__main__":
    unittest.main()
