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
NOTION_BLOCK_ID_FIELD = "Notion Block ID"
NOTION_CARD_BACKGROUND_FIELD = "Notion Card Background"

# Keep sync metadata out of the way in Anki's note editor.
_COLLAPSED_METADATA_FIELDS = frozenset(
    (NOTION_BLOCK_ID_FIELD, NOTION_CARD_BACKGROUND_FIELD)
)

BASIC_CARD_NAME = "Notion (Basic)"
REVERSED_CARD_NAME = "Notion (Reversed)"
INPUT_CARD_NAME = "Notion (Input)"
CLOZE_CARD_NAME = "Notion (Cloze)"

# Increment when bundled HTML or CSS changes so installed note types can offer an update.
CARD_TEMPLATE_VERSION = 3
CARD_TEMPLATE_STATUS_CURRENT = "current"
CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE = "update_available"
CARD_TEMPLATE_STATUS_USER_MODIFIED = "user_modified"

CSS_MANAGED_MARKER = "/* Noteck card model css */"
CSS_VERSION_PREFIX = "/* Noteck card template version:"
HTML_VERSION_PREFIX = "<!-- Noteck card template version:"
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


def _with_template_version(html: str) -> str:
    """Add a hidden template version marker to bundled card HTML."""
    return f"{HTML_VERSION_PREFIX} {CARD_TEMPLATE_VERSION} -->\n{html}"


def _with_card_wrapper(html: str, *, use_background_field: bool = False) -> str:
    """Keep the Notion surface inside a bounded, optionally colored card wrapper."""
    color_class = (
        f" notion-card-background-{{{{{NOTION_CARD_BACKGROUND_FIELD}}}}}"
        if use_background_field
        else ""
    )
    return f'<div class="notion-card{color_class}">{html}</div>'


_MODEL_DEFINITIONS: tuple[ModelDefinition, ...] = (
    # Basic card type
    ModelDefinition(
        name=MODEL_NAME_BASIC,
        fields=("Front", "Back", NOTION_BLOCK_ID_FIELD, NOTION_CARD_BACKGROUND_FIELD),
        templates=(
            ModelTemplate(
                name=BASIC_CARD_NAME,
                front=_with_template_version(
                    _with_card_wrapper(
                        '<div class="notion-front">{{Front}}</div>',
                        use_background_field=True,
                    )
                ),
                back=_with_template_version(
                    _with_card_wrapper(
                        '{{FrontSide}}<hr id="answer"><div class="notion-back">{{Back}}</div>',
                        use_background_field=True,
                    )
                ),
            ),
        ),
        model_type=0,
    ),

    # Basic+Reversed card type
    ModelDefinition(
        name=MODEL_NAME_BASIC_REVERSED,
        fields=("Front", "Back", NOTION_BLOCK_ID_FIELD, NOTION_CARD_BACKGROUND_FIELD),
        templates=(
            ModelTemplate(
                name=BASIC_CARD_NAME,
                front=_with_template_version(
                    _with_card_wrapper(
                        '<div class="notion-front">{{Front}}</div>',
                        use_background_field=True,
                    )
                ),
                back=_with_template_version(
                    _with_card_wrapper(
                        '{{FrontSide}}<hr id="answer"><div class="notion-back">{{Back}}</div>',
                        use_background_field=True,
                    )
                ),
            ),
            ModelTemplate(
                name=REVERSED_CARD_NAME,
                front=_with_template_version(
                    _with_card_wrapper(
                        '<div class="notion-front">{{Back}}</div>',
                        use_background_field=True,
                    )
                ),
                back=_with_template_version(
                    _with_card_wrapper(
                        '{{FrontSide}}<hr id="answer"><div class="notion-back">{{Front}}</div>',
                        use_background_field=True,
                    )
                ),
            ),
        ),
        model_type=0,
    ),

    # Input card type
    ModelDefinition(
        name=MODEL_NAME_INPUT,
        fields=(
            "Front",
            "Back",
            "Expected Answer",
            NOTION_BLOCK_ID_FIELD,
            NOTION_CARD_BACKGROUND_FIELD,
        ),
        templates=(
            ModelTemplate(
                name=INPUT_CARD_NAME,
                front=_with_template_version(
                    _with_card_wrapper(
                        '<div class="notion-front">{{Front}}</div>'
                        '<div class="notion-input">{{type:Expected Answer}}</div>',
                        use_background_field=True,
                    )
                ),
                back=_with_template_version(
                    _with_card_wrapper(
                        '{{FrontSide}}<hr id="answer">'
                        '<div class="notion-back">{{Back}}</div>',
                        use_background_field=True,
                    )
                ),
            ),
        ),
        model_type=0,
    ),

    # Cloze card type
    ModelDefinition(
        name=MODEL_NAME_CLOZE,
        fields=("Text", "Extra", NOTION_BLOCK_ID_FIELD, NOTION_CARD_BACKGROUND_FIELD),
        templates=(
            ModelTemplate(
                name=CLOZE_CARD_NAME,
                front=_with_template_version(
                    _with_card_wrapper(
                        '<div class="notion-front">{{cloze:Text}}</div>',
                        use_background_field=True,
                    )
                ),
                back=_with_template_version(
                    _with_card_wrapper(
                        '<div class="notion-front">{{cloze:Text}}</div>'
                        '<div class="notion-back" style="font-style: italic">{{Extra}}</div>',
                        use_background_field=True,
                    )
                ),
            ),
        ),
        model_type=1,
    ),
)


def ensure_notion_toggle_model(mw: Any) -> None:
    """Ensure all Notion note types exist in the collection."""
    _ensure_notion_toggle_model(mw, overwrite_existing_templates=False)


def restore_default_card_templates(mw: Any) -> None:
    """Restore Noteck's default card template HTML and CSS."""
    _ensure_notion_toggle_model(mw, overwrite_existing_templates=True)


def default_card_templates_are_modified(mw: Any) -> bool:
    """Return whether installed Noteck card templates differ from bundled defaults."""
    return card_template_status(mw) != CARD_TEMPLATE_STATUS_CURRENT


def card_template_status(mw: Any) -> str:
    """Return whether card templates are current, updateable, or user-modified."""
    collection = getattr(mw, "col", None)
    if collection is None:
        return CARD_TEMPLATE_STATUS_CURRENT

    models = getattr(collection, "models", None)
    if models is None:
        return CARD_TEMPLATE_STATUS_CURRENT

    css = _build_managed_css(_load_model_css())
    statuses = [
        _model_template_status(models, definition, css)
        for definition in _MODEL_DEFINITIONS
    ]
    if CARD_TEMPLATE_STATUS_USER_MODIFIED in statuses:
        return CARD_TEMPLATE_STATUS_USER_MODIFIED
    if CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE in statuses:
        return CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE
    return CARD_TEMPLATE_STATUS_CURRENT


def _ensure_notion_toggle_model(mw: Any, *, overwrite_existing_templates: bool) -> None:
    """Ensure Noteck note types exist, optionally resetting template contents."""
    collection = getattr(mw, "col", None)
    if collection is None:
        return

    models = getattr(collection, "models", None)
    if models is None:
        return

    css = _build_managed_css(_load_model_css())
    for definition in _MODEL_DEFINITIONS:
        _ensure_model(
            models,
            definition,
            css,
            overwrite_existing_templates=overwrite_existing_templates,
        )


def _ensure_model(
    models: Any,
    definition: ModelDefinition,
    css: str,
    *,
    overwrite_existing_templates: bool = False,
) -> None:
    """Create one model definition or update an existing one idempotently.

    When overwrite_existing_templates is True, template HTML and CSS are reset to bundled defaults.
    """
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

        field = _field_by_name(model, field_name)
        if field_name in _COLLAPSED_METADATA_FIELDS and field is not None:
            # Reconcile existing note types as well as setting the new-field default.
            if field.get("collapsed") is not True:
                field["collapsed"] = True
                changed = True

    # Add missing templates, but preserve existing user-customized HTML.
    for template in definition.templates:
        existing_template = _template_by_name(model, template.name)
        if existing_template is None:
            _add_template(models, model, template.name, template.front, template.back)
            changed = True
            continue

        if overwrite_existing_templates:
            if str(existing_template.get("qfmt") or "") != template.front:
                existing_template["qfmt"] = template.front
                changed = True
            if str(existing_template.get("afmt") or "") != template.back:
                existing_template["afmt"] = template.back
                changed = True

    # Initialize CSS for new or empty note types, or when explicitly restoring defaults.
    current_css = str(model.get("css") or "")
    if created or overwrite_existing_templates or not current_css.strip():
        if current_css != css:
            model["css"] = css
            changed = True

    # apply changes if needed
    if created:
        _add_model(models, model)
        return
    if changed:
        _update_model(models, model)


def _model_differs_from_defaults(models: Any, definition: ModelDefinition, css: str) -> bool:
    """Check whether one existing model has user-visible template differences."""
    return _model_template_status(models, definition, css) != CARD_TEMPLATE_STATUS_CURRENT


def _model_template_status(models: Any, definition: ModelDefinition, css: str) -> str:
    """Classify one model's card template state."""
    model = _model_by_name(models, definition.name)
    if model is None:
        return CARD_TEMPLATE_STATUS_CURRENT

    for template in definition.templates:
        existing_template = _template_by_name(model, template.name)
        if existing_template is None:
            return CARD_TEMPLATE_STATUS_USER_MODIFIED

        front_status = _template_part_status(str(existing_template.get("qfmt") or ""), template.front)
        back_status = _template_part_status(str(existing_template.get("afmt") or ""), template.back)
        if CARD_TEMPLATE_STATUS_USER_MODIFIED in {front_status, back_status}:
            return CARD_TEMPLATE_STATUS_USER_MODIFIED
        if CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE in {front_status, back_status}:
            return CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE

    current_css = str(model.get("css") or "")
    if current_css == css:
        return CARD_TEMPLATE_STATUS_CURRENT
    if _installed_css_version(current_css) < CARD_TEMPLATE_VERSION:
        return CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE
    return CARD_TEMPLATE_STATUS_USER_MODIFIED


def _template_part_status(installed_html: str, default_html: str) -> str:
    """Classify one front/back template by content and embedded version."""
    if installed_html == default_html:
        return CARD_TEMPLATE_STATUS_CURRENT
    if _strip_template_version(installed_html) == _strip_template_version(default_html):
        return CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE
    if _installed_template_version(installed_html) < CARD_TEMPLATE_VERSION:
        return CARD_TEMPLATE_STATUS_UPDATE_AVAILABLE
    return CARD_TEMPLATE_STATUS_USER_MODIFIED


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


def _field_by_name(model: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return one field dictionary by its stable managed name."""
    fields = model.get("flds") or ()
    return next((field for field in fields if field.get("name") == name), None)


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
    version_marker = f"{CSS_VERSION_PREFIX} {CARD_TEMPLATE_VERSION} */"
    if not payload:
        return f"{CSS_MANAGED_MARKER}\n{version_marker}\n"

    return f"{CSS_MANAGED_MARKER}\n{version_marker}\n{payload}\n"


def _is_managed_css(css: str) -> bool:
    """Return whether the CSS is currently managed by this add-on."""
    return css.lstrip().startswith(CSS_MANAGED_MARKER)


def _installed_css_version(css: str) -> int:
    """Return the Noteck card template version stored in CSS."""
    for line in css.splitlines():
        version = _parse_version_marker(line.strip(), CSS_VERSION_PREFIX, "*/")
        if version is not None:
            return version
    if _is_managed_css(css):
        return 0
    return CARD_TEMPLATE_VERSION


def _installed_template_version(html: str) -> int:
    """Return the Noteck card template version stored in HTML."""
    first_line = html.lstrip().splitlines()[0] if html.strip() else ""
    version = _parse_version_marker(first_line.strip(), HTML_VERSION_PREFIX, "-->")
    if version is None:
        return CARD_TEMPLATE_VERSION
    return version


def _strip_template_version(html: str) -> str:
    """Remove the leading Noteck HTML version marker before content comparison."""
    lines = html.lstrip().splitlines()
    if not lines:
        return ""
    if _parse_version_marker(lines[0].strip(), HTML_VERSION_PREFIX, "-->") is None:
        return html
    return "\n".join(lines[1:])


def _parse_version_marker(line: str, prefix: str, suffix: str) -> int | None:
    """Parse a numeric Noteck version marker from one line."""
    if not line.startswith(prefix) or not line.endswith(suffix):
        return None
    raw_version = line.removeprefix(prefix).removesuffix(suffix).strip()
    try:
        return int(raw_version)
    except ValueError:
        return None
