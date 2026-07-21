"""Qt-independent schema loading for Noteck's top-level UI pages."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


class UiSchemaError(RuntimeError):
    """Raised when ui.json cannot be loaded or validated."""


@dataclass(frozen=True)
class UiPageDefinition:
    """Metadata describing one top-level UI page."""

    key: str
    name: str
    module: str
    factory: str


class UiSchema:
    """Parsed UI schema with lookup helpers."""

    def __init__(self, pages: Iterable[UiPageDefinition]) -> None:
        self._pages = tuple(pages)
        self._pages_by_key = {page.key: page for page in self._pages}

    @property
    def pages(self) -> tuple[UiPageDefinition, ...]:
        """Return the ordered list of UI pages."""
        return self._pages

    def get_page(self, key: str) -> UiPageDefinition:
        """Return the page definition for the given key."""
        try:
            return self._pages_by_key[key]
        except KeyError as exc:
            raise UiSchemaError(f"Unknown page key: {key}") from exc


_DEFAULT_UI_PATH = Path(__file__).resolve().parents[1] / "docs" / "ui.json"
_DEFAULT_FACTORY = "build_page"


def load_ui_schema(path: Path | None = None) -> UiSchema:
    """Load and validate the top-level UI schema from ui.json."""
    schema_path = path or _DEFAULT_UI_PATH
    if not schema_path.exists():
        raise UiSchemaError(f"UI schema not found: {schema_path}")

    with schema_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    pages_payload = payload.get("pages")
    if not isinstance(pages_payload, list) or not pages_payload:
        raise UiSchemaError("UI schema must include a non-empty 'pages' list.")

    pages: list[UiPageDefinition] = []
    seen_keys: set[str] = set()
    for page_payload in pages_payload:
        if not isinstance(page_payload, dict):
            raise UiSchemaError("Each page entry must be an object.")

        key = page_payload.get("key")
        name = page_payload.get("name")
        module = page_payload.get("module")
        factory = page_payload.get("factory", _DEFAULT_FACTORY)
        if not key or not name or not module:
            raise UiSchemaError("Each page requires 'key', 'name', and 'module'.")
        if not isinstance(factory, str) or not factory:
            raise UiSchemaError(f"Invalid factory for page '{key}'.")
        if key in seen_keys:
            raise UiSchemaError(f"Duplicate page key: {key}")

        seen_keys.add(key)
        pages.append(
            UiPageDefinition(
                key=str(key),
                name=str(name),
                module=str(module),
                factory=str(factory),
            )
        )
    return UiSchema(pages)
