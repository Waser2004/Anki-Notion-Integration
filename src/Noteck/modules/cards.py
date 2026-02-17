"""Anki note type registration for Notion card types."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
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
_PACKAGE_CARD_TEMPLATES_PATH = Path(__file__).resolve().parents[1] / "docs" / "card_templates"

AI_FORWARD_VARIANTS_FIELD = "AI Forward Variants B64"
AI_FORWARD_AUDIO_FIELD = "AI Forward Audio B64"
AI_REVERSE_VARIANTS_FIELD = "AI Reverse Variants B64"
AI_REVERSE_AUDIO_FIELD = "AI Reverse Audio B64"


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


def _front_template(
    *,
    question_field: str,
    variants_field: str,
    audio_field: str,
    direction: str,
    include_typed_input: bool = False,
) -> str:
    """Build a front template that applies variant cycling and optional audio autoplay."""
    typed_input_html = '<div class="notion-input">{{type:Expected Answer}}</div>' if include_typed_input else ""
    return _render_template_asset(
        "front_template.html",
        {
            "__QUESTION_FIELD__": _anki_field(question_field),
            "__TYPED_INPUT_HTML__": typed_input_html,
            "__DIRECTION__": direction,
            "__VARIANTS_FIELD__": _anki_field(variants_field),
            "__AUDIO_FIELD__": _anki_field(audio_field),
            "__FRONT_RUNTIME_JS__": _front_variant_runtime_script(),
        },
    )


def _basic_back_template(answer_field: str) -> str:
    """Build a back template that preserves the variant chosen on front."""
    return _render_template_asset(
        "basic_back_template.html",
        {"__ANSWER_FIELD__": _anki_field(answer_field)},
    )


def _input_back_template() -> str:
    """Build an Input back template with placeholders for AI evaluation output."""
    return _render_template_asset(
        "input_back_template.html",
        {"__INPUT_EVAL_RUNTIME_JS__": _load_template_asset("input_eval_runtime.js")},
    )


def _cloze_audio_meta_script(*, direction: str) -> str:
    """Build cloze variant script that updates visible text and optional audio."""
    return _cloze_variant_runtime_script(default_direction=direction)


def _shared_ai_script_helpers() -> str:
    """Load shared JS helpers used by card templates."""
    return _load_template_asset("shared_ai_helpers.js")


def _front_variant_runtime_script() -> str:
    """Load the front-side runtime and inject shared helper functions."""
    return _render_template_asset(
        "front_variant_runtime.js",
        {"__SHARED_AI_HELPERS_JS__": _shared_ai_script_helpers()},
    )


def _cloze_variant_runtime_script(*, default_direction: str = "forward") -> str:
    """Load the cloze runtime and inject shared helper functions."""
    return _render_template_asset(
        "cloze_variant_runtime.js",
        {
            "__SHARED_AI_HELPERS_JS__": _shared_ai_script_helpers(),
            "__DEFAULT_DIRECTION__": default_direction,
        },
    )


def _cloze_front_template() -> str:
    """Build cloze front template with AI metadata for text/audio variant playback."""
    return _render_template_asset(
        "cloze_front_template.html",
        {
            "__DIRECTION__": "forward",
            "__FORWARD_VARIANTS_FIELD__": _anki_field(AI_FORWARD_VARIANTS_FIELD),
            "__FORWARD_AUDIO_FIELD__": _anki_field(AI_FORWARD_AUDIO_FIELD),
            "__CLOZE_RUNTIME_JS__": _cloze_audio_meta_script(direction="forward"),
        },
    )


def _cloze_back_template() -> str:
    """Build cloze back template with AI metadata for variant-consistent rendering."""
    return _render_template_asset(
        "cloze_back_template.html",
        {
            "__DIRECTION__": "forward",
            "__FORWARD_VARIANTS_FIELD__": _anki_field(AI_FORWARD_VARIANTS_FIELD),
            "__FORWARD_AUDIO_FIELD__": _anki_field(AI_FORWARD_AUDIO_FIELD),
            "__CLOZE_RUNTIME_JS__": _cloze_audio_meta_script(direction="forward"),
        },
    )


def _anki_field(field_name: str) -> str:
    """Return one Anki field reference for a given field name."""
    return "{{" + str(field_name) + "}}"


@lru_cache(maxsize=64)
def _load_template_asset(filename: str) -> str:
    """Load one card template or runtime asset from disk."""
    path = _PACKAGE_CARD_TEMPLATES_PATH / filename
    if not path.exists():
        raise RuntimeError(f"Missing card template asset: {path}")
    return path.read_text(encoding="utf-8")


def _render_template_asset(filename: str, replacements: dict[str, str] | None = None) -> str:
    """Render one template asset using literal token replacement."""
    rendered = _load_template_asset(filename)
    if not replacements:
        return rendered
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    return rendered


_MODEL_DEFINITIONS: tuple[ModelDefinition, ...] = (
    # Basic card type
    ModelDefinition(
        name=MODEL_NAME_BASIC,
        fields=("Front", "Back", "Notion Block ID", AI_FORWARD_VARIANTS_FIELD, AI_FORWARD_AUDIO_FIELD),
        templates=(
            ModelTemplate(
                name=BASIC_CARD_NAME,
                front=_front_template(
                    question_field="Front",
                    variants_field=AI_FORWARD_VARIANTS_FIELD,
                    audio_field=AI_FORWARD_AUDIO_FIELD,
                    direction="forward",
                ),
                back=_basic_back_template("Back"),
            ),
        ),
        model_type=0,
    ),

    # Basic+Reversed card type
    ModelDefinition(
        name=MODEL_NAME_BASIC_REVERSED,
        fields=(
            "Front",
            "Back",
            "Notion Block ID",
            AI_FORWARD_VARIANTS_FIELD,
            AI_FORWARD_AUDIO_FIELD,
            AI_REVERSE_VARIANTS_FIELD,
            AI_REVERSE_AUDIO_FIELD,
        ),
        templates=(
            ModelTemplate(
                name=BASIC_CARD_NAME,
                front=_front_template(
                    question_field="Front",
                    variants_field=AI_FORWARD_VARIANTS_FIELD,
                    audio_field=AI_FORWARD_AUDIO_FIELD,
                    direction="forward",
                ),
                back=_basic_back_template("Back"),
            ),
            ModelTemplate(
                name=REVERSED_CARD_NAME,
                front=_front_template(
                    question_field="Back",
                    variants_field=AI_REVERSE_VARIANTS_FIELD,
                    audio_field=AI_REVERSE_AUDIO_FIELD,
                    direction="reverse",
                ),
                back=_basic_back_template("Front"),
            ),
        ),
        model_type=0,
    ),

    # Input card type
    ModelDefinition(
        name=MODEL_NAME_INPUT,
        fields=("Front", "Back", "Expected Answer", "Notion Block ID", AI_FORWARD_VARIANTS_FIELD, AI_FORWARD_AUDIO_FIELD),
        templates=(
            ModelTemplate(
                name=INPUT_CARD_NAME,
                front=_front_template(
                    question_field="Front",
                    variants_field=AI_FORWARD_VARIANTS_FIELD,
                    audio_field=AI_FORWARD_AUDIO_FIELD,
                    direction="forward",
                    include_typed_input=True,
                ),
                back=_input_back_template(),
            ),
        ),
        model_type=0,
    ),

    # Cloze card type
    ModelDefinition(
        name=MODEL_NAME_CLOZE,
        fields=("Text", "Extra", "Notion Block ID", AI_FORWARD_VARIANTS_FIELD, AI_FORWARD_AUDIO_FIELD),
        templates=(
            ModelTemplate(
                name=CLOZE_CARD_NAME,
                front=_cloze_front_template(),
                back=_cloze_back_template(),
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
