"""Anki note type registration for Notion toggle cards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

MODEL_NAME = "Notion Toggle"
MODEL_FIELDS = ("Front", "Back", "Notion Block ID")
TEMPLATE_NAME = "Card 1"
TEMPLATE_FRONT = '<div class="notion-front">{{Front}}</div>'
TEMPLATE_BACK = '{{FrontSide}}<hr id="answer"><div class="notion-back">{{Back}}</div>'
CSS_MANAGED_MARKER = "/* anki-notion-integration: notion-toggle-model */"
_PACKAGE_STYLESHEET_PATH = Path(__file__).resolve().parent / "docs" / "Notion_Card_Stylesheet.css"


def ensure_notion_toggle_model(mw: Any) -> None:
    """Ensure the dedicated Notion Toggle note type exists in the collection."""
    collection = getattr(mw, "col", None)
    if collection is None:
        return

    models = getattr(collection, "models", None)
    if models is None:
        return

    model = _model_by_name(models, MODEL_NAME)
    created = model is None
    if created:
        model = _new_model(models, MODEL_NAME)

    changed = False
    for field_name in MODEL_FIELDS:
        if not _has_field(model, field_name):
            _add_field(models, model, field_name)
            changed = True

    template = _template_by_name(model, TEMPLATE_NAME)
    if template is None:
        _add_template(models, model, TEMPLATE_NAME, TEMPLATE_FRONT, TEMPLATE_BACK)
        changed = True

    css = _build_managed_css(_load_model_css())
    current_css = str(model.get("css") or "")
    if created or not current_css.strip() or _is_managed_css(current_css):
        if current_css != css:
            model["css"] = css
            changed = True

    if created:
        _add_model(models, model)
        return
    if changed:
        _update_model(models, model)


def _model_by_name(models: Any, name: str) -> dict[str, Any] | None:
    """Return a note type by name across supported Anki APIs."""
    if hasattr(models, "by_name"):
        return models.by_name(name)
    if hasattr(models, "byName"):
        return models.byName(name)
    return None


def _new_model(models: Any, name: str) -> dict[str, Any]:
    """Create an unsaved note type dict."""
    if hasattr(models, "new"):
        return models.new(name)
    return models.newModel(name)


def _add_model(models: Any, model: dict[str, Any]) -> None:
    """Persist a newly created note type."""
    if hasattr(models, "add_dict"):
        models.add_dict(model)
        return
    if hasattr(models, "add"):
        models.add(model)
        return
    models.save(model)


def _update_model(models: Any, model: dict[str, Any]) -> None:
    """Persist updates to an existing note type."""
    if hasattr(models, "update_dict"):
        models.update_dict(model)
        return
    models.save(model)


def _has_field(model: dict[str, Any], name: str) -> bool:
    """Check whether a note type already includes a field."""
    fields = model.get("flds") or ()
    return any(field.get("name") == name for field in fields)


def _add_field(models: Any, model: dict[str, Any], name: str) -> None:
    """Add a required field to a note type."""
    if hasattr(models, "new_field"):
        field = models.new_field(name)
        models.add_field(model, field)
        return

    field = models.newField(name)
    models.addField(model, field)


def _template_by_name(model: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return a card template by name if present."""
    templates = model.get("tmpls") or ()
    for template in templates:
        if template.get("name") == name:
            return template
    return None


def _add_template(
    models: Any,
    model: dict[str, Any],
    name: str,
    front: str,
    back: str,
) -> None:
    """Add a required card template to a note type."""
    if hasattr(models, "new_template"):
        template = models.new_template(name)
        template["qfmt"] = front
        template["afmt"] = back
        models.add_template(model, template)
        return

    template = models.newTemplate(name)
    template["qfmt"] = front
    template["afmt"] = back
    models.addTemplate(model, template)


def _load_model_css() -> str:
    """Load Notion-like card CSS from disk."""
    if _PACKAGE_STYLESHEET_PATH.exists():
        return _PACKAGE_STYLESHEET_PATH.read_text(encoding="utf-8")

    return ""


def _build_managed_css(source_css: str) -> str:
    """Prefix CSS with a marker used for safe, idempotent updates."""
    payload = source_css.strip()
    if not payload:
        return f"{CSS_MANAGED_MARKER}\n"

    return f"{CSS_MANAGED_MARKER}\n{payload}\n"


def _is_managed_css(css: str) -> bool:
    """Return whether the CSS is currently managed by this add-on."""
    return css.lstrip().startswith(CSS_MANAGED_MARKER)
