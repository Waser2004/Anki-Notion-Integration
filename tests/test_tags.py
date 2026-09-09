"""Cover Notion tag ownership, synchronization, and review settings."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from test_parser import _block, _text_item
from test_cards import _FakeModels
from Noteck.modules.parser import parse_page_to_cards
from Noteck.modules.parser.tags import parse_tags
from Noteck.modules.sync import _apply_payload_to_note, _prepare_payload_media
from Noteck.modules.cards import (
    _MODEL_DEFINITIONS, _build_managed_css, _load_model_css, _ensure_model,
    _model_template_status, set_review_tags_visible,
)


def paragraph(text, *, highlighted=False):
    """Build a paragraph with optional cloze formatting."""
    annotations = {"color": "yellow_background"} if highlighted else {}
    return _block(text, "paragraph", {"rich_text": [_text_item(text, annotations=annotations)]})


class TagTests(unittest.TestCase):
    """Verify tag metadata across supported card types."""

    def test_markers_and_validation(self):
        """Accept formatted markers and ignore malformed tag paragraphs."""
        for marker in ("🏷️", "🏷", "[Tags]", "Tags:"):
            with self.subTest(marker=marker):
                self.assertEqual(parse_tags(paragraph(marker + " Analysis::Series Math math")), ("Analysis::Series", "Math"))
        for text in ("Tags:", "Tags: ::bad", "Tags: good bad::", "Tags: bad\x00tag"):
            self.assertEqual(parse_tags(paragraph(text)), ())
        self.assertIsNone(parse_tags(paragraph("ordinary Tags: text")))

    def test_toggle_types_and_nested_content(self):
        """Direct tags disappear from answers while nested tags remain content."""
        nested = _block("nested", "toggle", {"rich_text": [_text_item("Nested")]}, children=(paragraph("Tags: nested"),))
        for card_type in ("basic", "basic_reversed", "input"):
            block = _block("card", "toggle", {"rich_text": [_text_item("Question")]}, children=(paragraph("Answer"), paragraph("Tags: direct"), nested))
            payload = parse_page_to_cards("page", [block], default_card_type=card_type)[0]
            self.assertEqual(payload.tags, ("direct",))
            self.assertNotIn("Tags: direct", payload.fields["Back"])
            self.assertIn("Tags: nested", payload.fields["Back"])
            self.assertNotIn("Tags: direct", payload.fields.get("Expected Answer", ""))

    def test_cloze_metadata_orders_and_limits(self):
        """Attach one Extra and Tags in either order without crossing duplicates."""
        cloze = paragraph("Paris", highlighted=True)
        extra = paragraph("Extra: detail")
        tags  = paragraph("Tags: Geography")
        for metadata in ((extra, tags), (tags, extra)):
            payload = parse_page_to_cards("page", [cloze, *metadata], enable_cloze=True)[0]
            self.assertEqual(payload.tags, ("Geography",))
            self.assertEqual(payload.fields["Extra"], "detail")
        payload = parse_page_to_cards("page", [cloze, tags, tags, extra], enable_cloze=True)[0]
        self.assertEqual(payload.fields["Extra"], "")
        payload = parse_page_to_cards("page", [cloze, extra, extra, tags], enable_cloze=True)[0]
        self.assertEqual(payload.tags, ())
        payload = parse_page_to_cards("page", [cloze, paragraph("gap"), tags], enable_cloze=True)[0]
        self.assertEqual(payload.tags, ())

    def test_advanced_cloze_and_tag_only_hash(self):
        """Advanced tags stay out of cloze text and affect change detection."""
        payloads = []
        for tag in ("one", "two"):
            block = _block("card", "toggle", {"rich_text": [_text_item("[cloze] Title")]}, children=(paragraph("Answer", highlighted=True), paragraph("Tags: " + tag)))
            payload = parse_page_to_cards("page", [block], enable_cloze=True)[0]
            self.assertEqual(payload.tags, (tag,))
            self.assertNotIn("Tags:", payload.fields["Text"])
            payloads.append(payload)
        self.assertNotEqual(payloads[0].content_hash, payloads[1].content_hash)

    def test_sync_adds_tags(self):
        """Applying a payload adds its tags through Anki's note API."""
        block   = _block("card", "toggle", {"rich_text": [_text_item("Question")]}, children=(paragraph("Answer"), paragraph("Tags: Math")))
        payload = parse_page_to_cards("page", [block])[0]
        payload = _prepare_payload_media(SimpleNamespace(), payload)
        note    = Mock()
        note.__setitem__ = Mock()
        _apply_payload_to_note(note, payload)
        note.add_tag.assert_called_once_with("Math")

    def test_visibility_preserves_template_status(self):
        """The display setting preserves customized CSS and default-template status."""
        definition = _MODEL_DEFINITIONS[0]
        css        = _build_managed_css(_load_model_css())
        models     = _FakeModels()
        mw         = SimpleNamespace(col=SimpleNamespace(models=models))
        _ensure_model(models, definition, css)
        set_review_tags_visible(mw, True)
        self.assertIn("--noteck-tags-display: inline", models.model["css"])
        self.assertEqual(_model_template_status(models, definition, css), "current")
        set_review_tags_visible(mw, False)
        self.assertEqual(models.model["css"], css)
        for template in definition.templates:
            self.assertIn('{{#Tags}}<span class="notion-review-tags">{{Tags}}</span>{{/Tags}}', template.front)

    def test_footer_aligns_link_and_tags(self):
        """The footer optically aligns link and tag text around a middle dot."""
        css = _load_model_css()

        self.assertRegex(css, r"\.notion-footer\s*\{[^}]*align-items:\s*center;")
        self.assertRegex(css, r"\.notion-footer \.notion-source-link\s*\{[^}]*top:\s*1px;")
        self.assertRegex(css, r"\.notion-review-tags\s*\{[^}]*line-height:\s*1\.2;")
        self.assertRegex(css, r'\.notion-source-link \+ \.notion-review-tags::before\s*\{[^}]*content:\s*"·";')
