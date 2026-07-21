"""Tests for Anki note type template registration."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.cards import (  # noqa: E402
    BASIC_CARD_NAME,
    CARD_TEMPLATE_VERSION,
    CARD_TEMPLATE_STATUS_CURRENT,
    CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE,
    CARD_TEMPLATE_STATUS_USER_MODIFIED,
    CSS_MANAGED_MARKER,
    CSS_VERSION_PREFIX,
    MODEL_NAME_BASIC,
    MODEL_NAME_CLOZE,
    NOTION_BLOCK_ID_FIELD,
    NOTION_CARD_BACKGROUND_FIELD,
    _MODEL_DEFINITIONS,
    _build_managed_css,
    _ensure_model,
    _model_differs_from_defaults,
    _model_template_status,
    _strip_template_version,
)


class _FakeModels:
    """Minimal models API surface used by card registration tests."""

    def __init__(self, model: dict | None = None) -> None:
        self.model = model
        self.added = False
        self.updated = False

    def by_name(self, name: str) -> dict | None:
        """Return the existing model when its name matches."""
        if self.model and self.model.get("name") == name:
            return self.model
        return None

    def new(self, name: str) -> dict:
        """Create a new unsaved model dictionary."""
        self.model = {"name": name, "flds": [], "tmpls": [], "type": 0, "css": ""}
        return self.model

    def add_dict(self, model: dict) -> None:
        """Record that a new model was persisted."""
        self.model = model
        self.added = True

    def update_dict(self, model: dict) -> None:
        """Record that an existing model was updated."""
        self.model = model
        self.updated = True

    def new_field(self, name: str) -> dict:
        """Create a field dictionary."""
        return {"name": name}

    def add_field(self, model: dict, field: dict) -> None:
        """Append a field to the model."""
        model.setdefault("flds", []).append(field)

    def new_template(self, name: str) -> dict:
        """Create a template dictionary."""
        return {"name": name}

    def add_template(self, model: dict, template: dict) -> None:
        """Append a card template to the model."""
        model.setdefault("tmpls", []).append(template)


class CardModelTests(unittest.TestCase):
    """Exercise note type creation and non-destructive updates."""

    def test_new_model_gets_default_templates_and_css(self) -> None:
        models = _FakeModels()
        definition = _MODEL_DEFINITIONS[0]

        _ensure_model(models, definition, "/* default css */")

        self.assertTrue(models.added)
        self.assertEqual(models.model["name"], MODEL_NAME_BASIC)
        self.assertEqual(models.model["css"], "/* default css */")
        self.assertEqual([field["name"] for field in models.model["flds"]], list(definition.fields))
        self.assertEqual(models.model["tmpls"][0]["name"], BASIC_CARD_NAME)
        self.assertEqual(models.model["tmpls"][0]["qfmt"], definition.templates[0].front)
        self.assertEqual(models.model["tmpls"][0]["afmt"], definition.templates[0].back)
        fields_by_name = {field["name"]: field for field in models.model["flds"]}
        self.assertIs(fields_by_name[NOTION_BLOCK_ID_FIELD]["collapsed"], True)
        self.assertIs(fields_by_name[NOTION_CARD_BACKGROUND_FIELD]["collapsed"], True)

    def test_all_default_templates_use_the_bounded_card_wrapper(self) -> None:
        for definition in _MODEL_DEFINITIONS:
            for template in definition.templates:
                with self.subTest(model=definition.name, template=template.name):
                    self.assertIn('<div class="notion-card', template.front)
                    self.assertIn('<div class="notion-card', template.back)

    def test_card_gutter_does_not_extend_the_viewport_height(self) -> None:
        """The card gutter belongs to body's border box, not the card's outer margin."""
        css_path = Path(__file__).resolve().parents[1] / "src" / "Noteck" / "docs" / "Notion_Card_Stylesheet.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertRegex(css, r"body\s*\{[^}]*padding:\s*1rem;[^}]*min-height:\s*100vh;")
        self.assertRegex(css, r"\.notion-card\s*\{[^}]*width:\s*100%;[^}]*margin:\s*0 auto;")

    def test_toggle_templates_use_the_managed_card_background_field(self) -> None:
        for definition in _MODEL_DEFINITIONS:
            if definition.name == MODEL_NAME_CLOZE:
                self.assertNotIn(NOTION_CARD_BACKGROUND_FIELD, definition.fields)
                continue
            self.assertIn(NOTION_CARD_BACKGROUND_FIELD, definition.fields)
            for template in definition.templates:
                with self.subTest(model=definition.name, template=template.name):
                    dynamic_class = f"notion-card-background-{{{{{NOTION_CARD_BACKGROUND_FIELD}}}}}"
                    self.assertIn(dynamic_class, template.front)
                    self.assertIn(dynamic_class, template.back)

    def test_existing_model_adds_background_field_without_overwriting_templates(self) -> None:
        definition = _MODEL_DEFINITIONS[0]
        model = {
            "name": MODEL_NAME_BASIC,
            "flds": [
                {"name": name}
                for name in definition.fields
                if name != NOTION_CARD_BACKGROUND_FIELD
            ],
            "tmpls": [
                {
                    "name": BASIC_CARD_NAME,
                    "qfmt": "<custom-front>",
                    "afmt": "<custom-back>",
                }
            ],
            "type": definition.model_type,
            "css": "/* custom css */",
        }
        models = _FakeModels(model)

        _ensure_model(models, definition, "/* default css */")

        self.assertTrue(models.updated)
        self.assertIn(NOTION_CARD_BACKGROUND_FIELD, [field["name"] for field in model["flds"]])
        fields_by_name = {field["name"]: field for field in model["flds"]}
        self.assertIs(fields_by_name[NOTION_BLOCK_ID_FIELD]["collapsed"], True)
        self.assertIs(fields_by_name[NOTION_CARD_BACKGROUND_FIELD]["collapsed"], True)
        self.assertEqual(model["tmpls"][0]["qfmt"], "<custom-front>")
        self.assertEqual(model["tmpls"][0]["afmt"], "<custom-back>")

    def test_existing_metadata_fields_are_collapsed_without_overwriting_templates(self) -> None:
        """Managed metadata fields migrate to collapsed on existing note types."""
        definition = _MODEL_DEFINITIONS[0]
        model = {
            "name": MODEL_NAME_BASIC,
            "flds": [
                {"name": name, "collapsed": False}
                for name in definition.fields
            ],
            "tmpls": [
                {
                    "name": BASIC_CARD_NAME,
                    "qfmt": "<custom-front>",
                    "afmt": "<custom-back>",
                }
            ],
            "type": definition.model_type,
            "css": "/* custom css */",
        }
        models = _FakeModels(model)

        _ensure_model(models, definition, "/* default css */")

        fields_by_name = {field["name"]: field for field in model["flds"]}
        self.assertTrue(models.updated)
        self.assertIs(fields_by_name[NOTION_BLOCK_ID_FIELD]["collapsed"], True)
        self.assertIs(fields_by_name[NOTION_CARD_BACKGROUND_FIELD]["collapsed"], True)
        self.assertIs(fields_by_name["Front"]["collapsed"], False)
        self.assertEqual(model["tmpls"][0]["qfmt"], "<custom-front>")

    def test_stylesheet_preserves_special_block_shapes_and_list_markers(self) -> None:
        css_path = Path(__file__).resolve().parents[1] / "src" / "Noteck" / "docs" / "Notion_Card_Stylesheet.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn("blockquote.notion-block-color-background", css)
        self.assertIn("border-radius: 0;", css)
        self.assertIn("margin-inline: 0;", css)
        self.assertIn(".callout.notion-block-color-background", css)
        self.assertIn("border-radius: 10px;", css)
        self.assertIn("li.notion-block-color-background", css)
        self.assertIn("isolation: isolate;", css)
        self.assertIn("inset-inline-start: -1.7em;", css)
        self.assertNotIn('content: counter(list-item) ".";', css)

    def test_root_background_makes_fixed_block_surfaces_backdrop_aware(self) -> None:
        """Fixed block surfaces change only when a validated root color supplies variables."""
        css_path = Path(__file__).resolve().parents[1] / "src" / "Noteck" / "docs" / "Notion_Card_Stylesheet.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn("--notion-fixed-block-surface: var(--notion-inherited-surface);", css)
        self.assertIn(
            "background: var(--notion-fixed-block-surface, var(--notion-code-bg)) !important;",
            css,
        )
        self.assertIn(
            "background: var(--notion-fixed-block-surface, var(--notion-surface-2)) !important;",
            css,
        )
        # Mermaid SVG pixels must remain opaque; only their containing surface is translucent.
        self.assertNotIn("mix-blend-mode", css)
        self.assertIn(".code > code {", css)
        self.assertIn("background: none !important;", css)

    def test_existing_template_html_and_css_are_preserved(self) -> None:
        definition = _MODEL_DEFINITIONS[0]
        model = {
            "name": MODEL_NAME_BASIC,
            "flds": [{"name": name} for name in definition.fields],
            "tmpls": [
                {
                    "name": BASIC_CARD_NAME,
                    "qfmt": "<custom-front>",
                    "afmt": "<custom-back>",
                }
            ],
            "type": definition.model_type,
            "css": "/* custom css */",
        }
        models = _FakeModels(model)

        _ensure_model(models, definition, "/* default css */")

        # Updating collapsed metadata must not overwrite user-authored HTML or CSS.
        self.assertTrue(models.updated)
        fields_by_name = {field["name"]: field for field in model["flds"]}
        self.assertIs(fields_by_name[NOTION_BLOCK_ID_FIELD]["collapsed"], True)
        self.assertIs(fields_by_name[NOTION_CARD_BACKGROUND_FIELD]["collapsed"], True)
        self.assertEqual(model["css"], "/* custom css */")
        self.assertEqual(model["tmpls"][0]["qfmt"], "<custom-front>")
        self.assertEqual(model["tmpls"][0]["afmt"], "<custom-back>")

    def test_model_differs_from_defaults_only_when_template_or_css_changes(self) -> None:
        definition = _MODEL_DEFINITIONS[0]
        model = {
            "name": MODEL_NAME_BASIC,
            "flds": [{"name": name} for name in definition.fields],
            "tmpls": [
                {
                    "name": BASIC_CARD_NAME,
                    "qfmt": definition.templates[0].front,
                    "afmt": definition.templates[0].back,
                }
            ],
            "type": definition.model_type,
            "css": "/* default css */",
        }
        models = _FakeModels(model)

        self.assertFalse(_model_differs_from_defaults(models, definition, "/* default css */"))

        model["tmpls"][0]["qfmt"] = "<custom-front>"
        self.assertTrue(_model_differs_from_defaults(models, definition, "/* default css */"))

    def test_model_template_status_reports_current_templates(self) -> None:
        definition = _MODEL_DEFINITIONS[0]
        css = _build_managed_css("body { color: black; }")
        model = {
            "name": MODEL_NAME_BASIC,
            "flds": [{"name": name} for name in definition.fields],
            "tmpls": [
                {
                    "name": BASIC_CARD_NAME,
                    "qfmt": definition.templates[0].front,
                    "afmt": definition.templates[0].back,
                }
            ],
            "type": definition.model_type,
            "css": css,
        }

        self.assertEqual(
            _model_template_status(_FakeModels(model), definition, css),
            CARD_TEMPLATE_STATUS_CURRENT,
        )

    def test_model_template_status_reports_update_available_for_legacy_defaults(self) -> None:
        definition = _MODEL_DEFINITIONS[0]
        css = _build_managed_css("body { color: black; }")
        model = {
            "name": MODEL_NAME_BASIC,
            "flds": [{"name": name} for name in definition.fields],
            "tmpls": [
                {
                    "name": BASIC_CARD_NAME,
                    "qfmt": _strip_template_version(definition.templates[0].front),
                    "afmt": _strip_template_version(definition.templates[0].back),
                }
            ],
            "type": definition.model_type,
            "css": css,
        }

        self.assertEqual(
            _model_template_status(_FakeModels(model), definition, css),
            CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE,
        )

    def test_model_template_status_reports_update_available_for_older_css(self) -> None:
        definition = _MODEL_DEFINITIONS[0]
        current_css = _build_managed_css("body { color: black; }")
        legacy_css = (
            f"{CSS_MANAGED_MARKER}\n{CSS_VERSION_PREFIX} {CARD_TEMPLATE_VERSION - 1} */\n"
            "body { color: black; }\n"
        )
        model = {
            "name": MODEL_NAME_BASIC,
            "flds": [{"name": name} for name in definition.fields],
            "tmpls": [
                {
                    "name": BASIC_CARD_NAME,
                    "qfmt": definition.templates[0].front,
                    "afmt": definition.templates[0].back,
                }
            ],
            "type": definition.model_type,
            "css": legacy_css,
        }

        self.assertEqual(
            _model_template_status(_FakeModels(model), definition, current_css),
            CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE,
        )

    def test_model_template_status_reports_user_modified_for_custom_html(self) -> None:
        definition = _MODEL_DEFINITIONS[0]
        css = _build_managed_css("body { color: black; }")
        model = {
            "name": MODEL_NAME_BASIC,
            "flds": [{"name": name} for name in definition.fields],
            "tmpls": [
                {
                    "name": BASIC_CARD_NAME,
                    "qfmt": "<custom-front>",
                    "afmt": definition.templates[0].back,
                }
            ],
            "type": definition.model_type,
            "css": css,
        }

        self.assertEqual(
            _model_template_status(_FakeModels(model), definition, css),
            CARD_TEMPLATE_STATUS_USER_MODIFIED,
        )

    def test_restore_mode_overwrites_template_html_and_css(self) -> None:
        definition = _MODEL_DEFINITIONS[0]
        model = {
            "name": MODEL_NAME_BASIC,
            "flds": [{"name": name} for name in definition.fields],
            "tmpls": [
                {
                    "name": BASIC_CARD_NAME,
                    "qfmt": "<custom-front>",
                    "afmt": "<custom-back>",
                }
            ],
            "type": definition.model_type,
            "css": "/* custom css */",
        }
        models = _FakeModels(model)

        _ensure_model(
            models,
            definition,
            "/* default css */",
            overwrite_existing_templates=True,
        )

        self.assertTrue(models.updated)
        self.assertEqual(model["css"], "/* default css */")
        self.assertEqual(model["tmpls"][0]["qfmt"], definition.templates[0].front)
        self.assertEqual(model["tmpls"][0]["afmt"], definition.templates[0].back)


if __name__ == "__main__":
    unittest.main()
