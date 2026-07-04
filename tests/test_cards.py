"""Tests for Anki note type template registration."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.cards import (  # noqa: E402
    BASIC_CARD_NAME,
    MODEL_NAME_BASIC,
    _MODEL_DEFINITIONS,
    _ensure_model,
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

        self.assertFalse(models.updated)
        self.assertEqual(model["css"], "/* custom css */")
        self.assertEqual(model["tmpls"][0]["qfmt"], "<custom-front>")
        self.assertEqual(model["tmpls"][0]["afmt"], "<custom-back>")


if __name__ == "__main__":
    unittest.main()
