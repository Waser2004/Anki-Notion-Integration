"""Anki note type registration for Notion card types."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL_NAME = "Notion (Basic)"
MODEL_NAME_BASIC = MODEL_NAME
MODEL_NAME_BASIC_REVERSED = "Notion (Basic+Reversed)"
MODEL_NAME_INPUT = "Notion (Input)"
MODEL_NAME_CLOZE = "Notion (Cloze)"

BASIC_CARD_NAME = "Notion (Basic)"
REVERSED_CARD_NAME = "Notion (Reversed)"
INPUT_CARD_NAME = "Notion (Input)"
CLOZE_CARD_NAME = "Notion (Cloze)"

CSS_MANAGED_MARKER = "/* Noteck card model css */"
# Module files live in Noteck/modules while shared resources stay in Noteck/docs.
_PACKAGE_STYLESHEET_PATH = Path(__file__).resolve().parents[1] / "docs" / "Notion_Card_Stylesheet.css"


@dataclass(frozen=True)
class ModelTemplate:
    """One model template payload."""
    name: str
    front: str
    back: str


@dataclass(frozen=True)
class ModelDefinition:
    """One model definition used for idempotent model setup."""
    name: str
    fields: tuple[str, ...]
    templates: tuple[ModelTemplate, ...]
    model_type: int = 0


_MODEL_DEFINITIONS: tuple[ModelDefinition, ...] = (
    # Basic card type
    ModelDefinition(
        name=MODEL_NAME_BASIC,
        fields=("Front", "Back", "Notion Block ID"),
        templates=(
            ModelTemplate(
                name=BASIC_CARD_NAME,
                front='<div class="notion-front">{{Front}}</div>',
                back='{{FrontSide}}<hr id="answer"><div class="notion-back">{{Back}}</div>',
            ),
        ),
        model_type=0,
    ),

    # Basic+Reversed card type
    ModelDefinition(
        name=MODEL_NAME_BASIC_REVERSED,
        fields=("Front", "Back", "Notion Block ID"),
        templates=(
            ModelTemplate(
                name=BASIC_CARD_NAME,
                front='<div class="notion-front">{{Front}}</div>',
                back='{{FrontSide}}<hr id="answer"><div class="notion-back">{{Back}}</div>',
            ),
            ModelTemplate(
                name=REVERSED_CARD_NAME,
                front='<div class="notion-front">{{Back}}</div>',
                back='{{FrontSide}}<hr id="answer"><div class="notion-back">{{Front}}</div>',
            ),
        ),
        model_type=0,
    ),

    # Input card type
    ModelDefinition(
        name=MODEL_NAME_INPUT,
        fields=("Front", "Back", "Expected Answer", "Notion Block ID"),
        templates=(
            ModelTemplate(
                name=INPUT_CARD_NAME,
                front=(
                    '<div class="notion-front">{{Front}}</div>'
                    '<div class="notion-input">{{type:Expected Answer}}</div>'
                ),
                back=(
                    '{{FrontSide}}<hr id="answer">'
                    '<div class="notion-back">{{Back}}</div>'
                ),
            ),
        ),
        model_type=0,
    ),

    # Cloze card type
    ModelDefinition(
        name=MODEL_NAME_CLOZE,
        fields=("Text", "Extra", "Notion Block ID"),
        templates=(
            ModelTemplate(
                name=CLOZE_CARD_NAME,
                front='<div class="notion-front">{{cloze:Text}}</div>',
                back=(
                    '<div class="notion-front">{{cloze:Text}}</div>'
                    '<div class="notion-back" style="font-style: italic">{{Extra}}</div>'
                ),
            ),
        ),
        model_type=1,
    ),
)


def ensure_notion_toggle_model(mw: Any) -> None:
    """Ensure all Notion note types exist in the collection."""
    collection = getattr(mw, "col", None)
    if collection is None:
        return

    models = getattr(collection, "models", None)
    if models is None:
        return

    css = _build_managed_css(_load_model_css())
    for definition in _MODEL_DEFINITIONS:
        _ensure_model(models, definition, css)


def _ensure_model(models: Any, definition: ModelDefinition, css: str) -> None:
    """Create or update one model definition idempotently."""
    model = _model_by_name(models, definition.name)

    # create model if not found.
    created = model is None
    if created:
        model = _new_model(models, definition.name)

    changed = False

    # Ensure correct model type.
    if int(model.get("type") or 0) != definition.model_type:
        model["type"] = definition.model_type
        changed = True

    # Add any missing fields. (fields are never removed to avoid data loss)
    for field_name in definition.fields:
        if not _has_field(model, field_name):
            _add_field(models, model, field_name)
            changed = True

    # Update existing templates if front or back format has changed.
    for template in definition.templates:
        existing_template = _template_by_name(model, template.name)
        if existing_template is None:
            _add_template(models, model, template.name, template.front, template.back)
            changed = True
            continue
        
        if str(existing_template.get("qfmt") or "") != template.front:
            existing_template["qfmt"] = template.front
            changed = True
        if str(existing_template.get("afmt") or "") != template.back:
            existing_template["afmt"] = template.back
            changed = True

    # update existing css if it has changed.
    current_css = str(model.get("css") or "")
    if created or not current_css.strip() or _is_managed_css(current_css):
        if current_css != css:
            model["css"] = css
            changed = True

    # apply changes if needed
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
